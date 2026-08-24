"""Iris' "analyseer & fix"-knop — van een foutkaart in het Actiecentrum naar
een diagnose + agent-actie, met een repertoire dat over fouten heen leert.

Anders dan actions.py (Iris' eigen briefing-initiatief) start dit vanuit een
BESTAANDE foutmelding die Vincent al ziet: hij klikt "Analyseer & fix", Iris
stelt een diagnose + remedie voor via dezelfde whitelist als haar
briefing-acties (fix.py/_ALLOWED_TYPES), en voert die na dat ene klikje meteen
uit — precies zoals "Ja, fix dit" bij haar eigen suggesties al werkt. Er is
geen aparte goedkeuringsstap nodig: het klikken op "Analyseer & fix" ÍS de
goedkeuring, en de remedie zelf landt zoals altijd achter de bestaande
review-gates (Wachtrij/outreach-review/etc.) — nooit een directe publicatie.

Leren over fouten heen: elke fout krijgt een genormaliseerde "handtekening"
(actie + fout-tekst zonder cijfers/UUID's/titels). De eerste keer vraagt Iris
een LLM-diagnose; daarna hergebruikt ze de remedie die eerder voor dezelfde
handtekening werkte (`iris_error_fixes`), zodat een terugkerende storing niet
elke keer opnieuw de LLM in hoeft en een remedie die aantoonbaar niet werkt
vanzelf wordt afgeraden (meer failures dan successes → 'active' blijft 1 maar
de UI toont het bewijs; de mens beslist dan zelf hoe verder).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

_MAX_TOKENS = 1200

_SYSTEM = (
    "Je bent Iris, de manager van Impact OS. Vincent klikte 'Analyseer & fix' op "
    "een foutmelding uit het Actiecentrum. Diagnosticeer de oorzaak in 1-2 zinnen "
    "en kies PRECIES ÉÉN remedie uit deze whitelist — nooit iets anders:\n"
    "  content_run       — herstart de contentmotor voor een site (target = sitenaam)\n"
    "  seo_refresh       — herverrijk wegzakkende pagina's (target = sitenaam)\n"
    "  outreach_run      — zet een nieuwe outreach-batch klaar ter review\n"
    "  linkbuilding_run  — zet een nieuwe linkbuilding-batch klaar ter review\n"
    "  lead_search_run   — vul de acquisitie-funnel met nieuwe leads\n"
    "  human_step        — als geen enkele agent-actie dit veilig kan oplossen "
    "(bv. ontbrekende credentials, een externe dienst die plat ligt, een "
    "configuratiefout in .env); geef dan de concrete stap die Vincent zelf moet "
    "zetten.\n"
    "Kies human_step als je twijfelt — een verkeerde agent-actie op een fout die "
    "een mens moet oplossen, verspilt alleen tijd. Antwoord UITSLUITEND met JSON: "
    '{"diagnose": "...", "remedy_type": "...", "target": "sitenaam of leeg", '
    '"aantal": 1, "human_step": "alleen invullen bij remedy_type=human_step"}'
)

_ALLOWED_REMEDIES = {"content_run", "seo_refresh", "outreach_run",
                    "linkbuilding_run", "lead_search_run", "human_step"}

# Vingerafdruk-normalisatie: cijfers, UUID's en aanhalingstekens-titels weg,
# zodat "job 8f21..." en "job a93c..." dezelfde handtekening krijgen.
_RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_RE_QUOTED = re.compile(r"'[^']{3,80}'")
_RE_DIGITS = re.compile(r"\d+")


def _signature(action: str, detail: str) -> str:
    d = _RE_UUID.sub("<id>", detail or "")
    d = _RE_QUOTED.sub("<titel>", d)
    d = _RE_DIGITS.sub("<n>", d)
    d = re.sub(r"\s+", " ", d).strip().lower()
    return f"{(action or '').strip().lower()}::{d[:300]}"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _load_error(error_id: str, kind: str = "activity_log") -> Optional[Dict[str, Any]]:
    """Fouten leven in twee tabellen met losse id-namespaces: activity_log
    (algemene fout-kaarten) en content_jobs (mislukte publicaties). Beide
    worden hier plat naar hetzelfde project/action/detail-schema gebracht."""
    with get_conn() as conn:
        if kind == "content_job":
            row = conn.execute(
                "SELECT j.id, s.name AS project, 'publish_failed' AS action, "
                "j.error AS detail, '' AS next_step, j.created_at FROM content_jobs j "
                "LEFT JOIN sites s ON s.id = j.site_id WHERE j.id = ?",
                (error_id,),
            ).fetchone()
            return dict(row) if row else None
        row = conn.execute(
            "SELECT id, project, action, detail, next_step, created_at "
            "FROM activity_log WHERE id = ?",
            (error_id,),
        ).fetchone()
        return dict(row) if row else None


def _known_remedy(signature: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM iris_error_fixes WHERE signature = ? AND active = 1", (signature,)
        ).fetchone()
        return dict(row) if row else None


def _remember(signature: str, project: str, action: str, detail: str,
              diagnosis: str, remedy_type: str, remedy_payload: dict,
              human_step: str) -> None:
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM iris_error_fixes WHERE signature = ?", (signature,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE iris_error_fixes SET occurrences = occurrences + 1, "
                "updated_at = ? WHERE signature = ?", (now, signature),
            )
            return
        import json
        conn.execute(
            "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
            "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
            "occurrences, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
            (f"fix-{uuid.uuid4().hex[:12]}", signature, project, action, detail[:300],
             diagnosis[:400], remedy_type, json.dumps(remedy_payload), human_step[:400],
             now, now),
        )


def _record_outcome(signature: str, ok: bool, note: str) -> None:
    with get_conn() as conn:
        col = "successes" if ok else "failures"
        conn.execute(
            f"UPDATE iris_error_fixes SET attempts = attempts + 1, {col} = {col} + 1, "
            "last_result = ?, updated_at = ? WHERE signature = ?",
            (note[:300], _now_iso(), signature),
        )


async def _diagnose(project: str, action: str, detail: str) -> Optional[Dict[str, Any]]:
    from .service import _llm, _extract_json
    prompt = (
        f"Project: {project}\nActie/type: {action}\nFoutdetail: {detail[:800]}\n\n"
        "Diagnosticeer en kies de remedie."
    )
    raw = await _llm(_SYSTEM, prompt, max_tokens=_MAX_TOKENS)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        return None
    remedy = str(parsed.get("remedy_type") or "").strip().lower()
    if remedy not in _ALLOWED_REMEDIES:
        return None
    return parsed


# ── Patroonherkenning: diagnoses die géén LLM nodig hebben ───────────────
# Aanleiding: op de foutenkaarten van 11-8-2026 (OpenModel "Connection error",
# "Niet geauthenticeerd bij Microsoft", "inhaalslag afgekapt") koos de triage-LLM
# steevast human_step met de vaag "check je credentials" — terwijl de oorzaak
# backend-breed is en een mens niets aan de credentials kan doen. Die LLM-call
# is verspilde tijd + tokens en geeft Vincent geen houvast. Deze patronen zijn
# deterministisch herkenbaar uit de fouttekst, dus diagnosticeren we ze direct.
_RE_OPENMODEL = re.compile(r"openmodel fout\s*:\s*connection error", re.I)
_RE_MS_AUTH = re.compile(r"niet geauthenticeerd bij microsoft|microsoft", re.I)
_RE_CATCHUP = re.compile(r"inhaalslag|afgekapt|tijdgrens|time[ -]?out", re.I)


def _pattern_diagnose(action: str, detail: str) -> Optional[Dict[str, Any]]:
    """Geef een kant-en-klare diagnose terug als de fouttekst in een bekend
    patroon valt, anders None (→ gewone LLM-diagnose)."""
    d = (detail or "").lower()
    if _RE_OPENMODEL.search(d):
        return {
            "diagnose": ("OpenModel (de LLM-backend) is niet bereikbaar — 'Connection error'. "
                         "Dit is een backend/storing, géén instelling aan jouw kant."),
            "remedy_type": "human_step",
            "human_step": ("Wacht tot de OpenModel-verbinding herstelt (check of api.openmodel.ai "
                           "bereikbaar is en of de OpenModel-key/route klopt). Zodra de verbinding terug "
                           "is, herstarten de vastgelopen taken vanzelf — of herstart de doelen via de "
                           "Doelen-tab (herstel-poging per taak). Iris kan dit niet zelf oplossen."),
            "source": "patroonherkenning (openmodel down)",
        }
    if _RE_MS_AUTH.search(d):
        return {
            "diagnose": ("De Bridge kan niet bij Microsoft (Outlook/Graph) — niet geauthenticeerd. "
                         "Je refresh-token is vermoedelijk verlopen."),
            "remedy_type": "human_step",
            "human_step": ("Klik hieronder op 'Verbind Microsoft opnieuw' (start de device-code login), "
                           "of vernieuw de credentials in het Bridge-project. Daarna herstelt de "
                           "mail-sync zichzelf."),
            "source": "patroonherkenning (ms-auth)",
            "reconnect_ms": True,
        }
    if _RE_CATCHUP.search(d):
        return {
            "diagnose": ("De ochtend-inhaalslag (Iris' startup-catch-up) liep tegen de time-out aan en is "
                         "afgebroken. Niet alle taken hebben vandaag gedraaid."),
            "remedy_type": "human_step",
            "human_step": ("Verhoog de timeout in .env (INHAAL_TIMEOUT_MIN van 40 naar 60) en herstart "
                           "Impact OS, óf start de gemiste runs handmatig via hun kaarten. De afgekapte run "
                           "zelf hoeft niet opnieuw — alleen wat eronder niet draaide."),
            "source": "patroonherkenning (catch-up timeout)",
        }
    return None


async def _waarheidsaudit(error_id: str, err: Dict[str, Any]) -> Dict[str, Any]:
    """De 'Analyseer & fix'-knop op een bevinding van de waarheidsaudit.

    Hier hoort geen LLM aan te pas te komen, en zeker niet de whitelist van
    _SYSTEM. Aanleiding (6 aug 2026): op de kaart 'Meerdere eigen pagina's
    vertonen bij Google op één zoekwoord' koos het model een contentronde; die
    leverde niets op en de gebruiker las "❌ Geen uitvoering opgeleverd — zie de
    foutkaart hierboven" onder een diagnose, met een verwijzing naar een kaart
    die er niet is. Erger dan de doodlopende knop is wat eronder gebeurde: de
    remedie werd in `iris_error_fixes` vastgelegd als de bekende aanpak voor
    deze handtekening, zodat elke volgende klik hem zónder LLM zou herhalen —
    een remedie die per constructie niets kan doen, ingesleten als beleid.

    Het model hoefde het antwoord ook helemaal niet te bedenken: elke invariant
    draagt in het register een `stap` die het échte incident eronder codeert, en
    de kaart draagt die stap al als `next_step`. En voor cluster-kannibalisatie
    is méér content precies de verkeerde ingreep — de invariant zegt letterlijk
    "schrijf er géén nieuw artikel bij".

    Bestaat er wél een echte remedie (`repair.REMEDIES`), dan is de klik de
    goedkeuring en draait die remedie — via de gewone publicatieroute mét gates.
    """
    from . import integrity
    from ..publish import repair

    inv = integrity.invariant_voor_kaart(error_id, err.get("detail") or "")
    stap = (err.get("next_step") or "").strip() or (inv.stap if inv else "")
    diagnose = (f"{inv.titel} ({inv.severity}). Aanleiding: {inv.incident}"
                if inv else (err.get("detail") or "Bevinding van de waarheidsaudit."))

    doe = repair.REMEDIES.get(inv.key) if inv else None
    if doe:
        try:
            uit = await doe(project=(err.get("project") or None), maximum=25)
        except Exception as e:  # noqa: BLE001
            logger.exception("[iris] reparatie van %s mislukt", inv.key)
            return {"ok": False, "diagnosis": diagnose, "remedy_type": inv.key,
                    "error": f"Reparatie mislukte: {str(e)[:200]}"}
        n = uit.get("gerepareerd") or 0
        return {"ok": bool(n), "diagnosis": diagnose, "remedy_type": inv.key,
                "result": (f"{n} geval(len) gerepareerd; de audit sluit de bevinding "
                           f"zodra de toets hem niet meer vindt."
                           if n else "Geen enkel geval kon gerepareerd worden — "
                                     "zie het logboek voor de reden per geval."),
                "human_step": stap, "source": "remedie uit het invariant-register"}

    # Bewust géén log_outcome zoals bij de gewone human_step-tak: de audit-kaart
    # staat er al en draagt exact deze stap. Er een tweede kaart naast zetten is
    # dezelfde dubbele melding die `stilstand_dubbel_gemeld` verbiedt.
    return {
        "ok": True,
        "diagnosis": diagnose,
        "remedy_type": "human_step",
        "human_step": stap or ("Bekijk de bevindingen op /api/iris/integrity — voor deze "
                               "toets bestaat nog geen automatische remedie."),
        "source": "invariant-register (geen LLM-gok: deze bevinding gaat over de "
                  "buitenwereld, en daar kan geen agent-actie bij)",
    }


async def analyze_and_fix(error_id: str, kind: str = "activity_log") -> Dict[str, Any]:
    """Diagnosticeer één foutkaart en voer de remedie meteen uit (of leg de
    mens-stap vast als geen agent-actie veilig is). Retourneert altijd een
    dict met minstens 'diagnosis'; 'ok' geeft aan of een agent-actie draaide."""
    err = _load_error(error_id, kind)
    if not err:
        return {"ok": False, "error": "Foutmelding niet gevonden"}

    actie = (err.get("action") or "").strip()
    if actie == "waarheidsaudit":
        return await _waarheidsaudit(error_id, err)

    # Dode bronlink in een publish_failed-job: volledig machine-oplosbaar, géén
    # LLM nodig. Direct naar de reparatie (vervangt de dode link, zet de job
    # terug in de Wachtrij achter de goedkeurings-gate). De LLM koos hier
    # steevast human_step ("los het zelf op") — precies de ruis die we wegnemen.
    if kind == "content_job" and actie == "publish_failed" and \
            "link-dood" in ((err.get("error") or err.get("detail") or "")).lower():
        from ..publish import repair
        try:
            uit = await repair.repareer_dode_link_in_job(error_id)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "diagnosis": "Reparatie mislukte",
                    "remedy_type": "human_step",
                    "human_step": f"De dode-link-reparatie viel om: {fail.describe_exception(e)}"}
        if uit.get("ok"):
            n = len(uit.get("vervangen") or [])
            return {"ok": True, "diagnosis": f"{n} dode bronlink(s) vervangen",
                    "remedy_type": "dode_link",
                    "result": f"Job terug in de Wachtrij — één klik op Publiceer zet hem live.",
                    "source": "deterministische dode-link-reparatie (geen LLM)"}
        return {"ok": False, "diagnosis": "Geen vervangbare dode link gevonden",
                "remedy_type": "human_step",
                "human_step": uit.get("reden", "zie log")}

    # Kaarten die per ontwérp op een mens wachten dragen hun eigen stap al (de
    # inhaalknop bij een gemiste run, "draai hem en lees de fout" bij een job
    # die nooit slaagde). `selfheal` laat ze met precies die reden met rust; de
    # triage-knop deed dat niet en liet een LLM alsnog een contentronde kiezen.
    # Eén lijst voor beide, anders lopen ze uit elkaar.
    from .selfheal import _MENSELIJK_BESLUIT
    if actie in _MENSELIJK_BESLUIT and (err.get("next_step") or "").strip():
        return {"ok": True, "remedy_type": "human_step",
                "diagnosis": (err.get("detail") or "")[:400],
                "human_step": err["next_step"].strip(),
                "source": "de kaart draagt zijn eigen stap — geen agent-actie kan dit "
                          "besluit overnemen"}

    signature = _signature(err["action"], err["detail"] or "")

    # Patroonherkenning vóór de LLM: bekende foutteksten (OpenModel-down,
    # MS-auth, catch-up-timeout) krijgen een directe, juiste diagnose zonder
    # een LLM-call die toch alleen "check je credentials" zegt. Deze diagnose
    # wordt niet in iris_error_fixes vastgelegd — hij is deterministisch en
    # hoeft niet "aangeleerd" te worden.
    patroon = _pattern_diagnose(err["action"], err["detail"] or "")
    if patroon:
        return {
            "ok": True,
            "diagnosis": patroon["diagnose"],
            "remedy_type": patroon["remedy_type"],
            "human_step": patroon["human_step"],
            "source": patroon["source"],
            "reconnect_ms": patroon.get("reconnect_ms", False),
        }

    known = _known_remedy(signature)

    if known and known.get("remedy_type"):
        remedy_type = known["remedy_type"]
        import json
        try:
            payload = json.loads(known.get("remedy_payload") or "{}")
        except (ValueError, TypeError):
            payload = {}
        target = known.get("project") or err["project"]
        diagnosis = known["diagnosis"]
        human_step = known.get("human_step") or ""
        source = "herkend uit eerdere fout (dit patroon kwam al eens langs)"
    else:
        parsed = await _diagnose(err["project"], err["action"], err["detail"] or "")
        if not parsed:
            # De LLM gaf geen bruikbare diagnose (bijv. omdat de LLM-backend
            # down is). Val terug op patroonherkenning: als de fouttekst in een
            # bekend patroon valt, geef dan een directe, juiste diagnose in
            # plaats van "Diagnose mislukt" — zeker in de bulk-loop (waar de
            # LLM toch al niet per kaart opgeroepen hoort te worden).
            patroon = _pattern_diagnose(err["action"], err["detail"] or "")
            if patroon:
                return {
                    "ok": True,
                    "diagnosis": patroon["diagnose"],
                    "remedy_type": patroon["remedy_type"],
                    "human_step": patroon["human_step"],
                    "source": patroon["source"] + " (LLM-diagnose faalde, patroon-val)",
                    "reconnect_ms": patroon.get("reconnect_ms", False),
                }
            log_outcome(
                err["project"], "iris_actie",
                f"Analyseren mislukt voor '{err['action']}' — de LLM gaf geen bruikbare diagnose",
                next_step="Probeer het opnieuw, of los de fout handmatig op.",
                status="error",
            )
            return {"ok": False, "error": "Diagnose mislukt — probeer het nog eens"}
        remedy_type = str(parsed.get("remedy_type") or "human_step").strip().lower()
        target = str(parsed.get("target") or "").strip() or err["project"]
        payload = {"aantal": parsed.get("aantal") or 1}
        diagnosis = str(parsed.get("diagnose") or "").strip()
        human_step = str(parsed.get("human_step") or "").strip()
        _remember(signature, err["project"], err["action"], err["detail"] or "",
                  diagnosis, remedy_type, payload, human_step)
        source = "nieuwe diagnose"

    if remedy_type == "human_step":
        detail = f"Iris' diagnose: {diagnosis or 'zie next_step'} ({source})"
        log_outcome(
            err["project"], "iris_actie", detail,
            next_step=human_step or "Bekijk de fout handmatig — geen agent-actie kan dit veilig oplossen.",
        )
        return {"ok": True, "diagnosis": diagnosis, "remedy_type": "human_step",
                "human_step": human_step, "source": source}

    reason = f"Analyseer & fix op foutmelding '{err['action']}' ({source})"
    try:
        from . import actions
        n = payload.get("aantal") or 1
        if remedy_type == "content_run":
            done = await actions.content_run(target, n, reason)
        elif remedy_type == "seo_refresh":
            done = await actions.seo_refresh(target, n, reason)
        elif remedy_type == "outreach_run":
            done = await actions.outreach_run(n, reason)
        elif remedy_type == "linkbuilding_run":
            done = await actions.linkbuilding_run(n, reason)
        elif remedy_type == "lead_search_run":
            done = await actions.lead_search_run(None, reason)
        else:
            done = None
    except Exception as e:  # noqa: BLE001
        logger.exception("[iris] triage-remedie %s mislukt voor %s", remedy_type, error_id)
        _record_outcome(signature, False, str(e)[:200])
        return {"ok": False, "diagnosis": diagnosis, "remedy_type": remedy_type,
                "error": str(e)[:300]}

    ok = bool(done)
    _record_outcome(signature, ok, done or "geen resultaat")
    if not ok:
        # Zeg wat er is gebeurd, niet waar het elders zou staan. De oude tekst
        # verwees naar "de foutkaart hierboven" — die bestaat niet: dit ís de
        # foutkaart. En een remedie die niets oplevert mag geen ingesleten
        # aanpak worden: hij verliest zijn status als bekende remedie zodra hij
        # drie keer niets deed (zelfde bewijs-eis als elders — een remedie sluit
        # niet omdat iemand zegt dat hij werkt).
        _verleer_bij_aanhoudend_falen(signature)
        return {"ok": False, "diagnosis": diagnosis, "remedy_type": remedy_type,
                "result": (f"De remedie '{remedy_type}' draaide, maar leverde niets op — "
                           f"deze fout is er niet mee opgelost. Los hem handmatig op of "
                           f"kijk in het logboek waarom de actie leeg terugkwam."),
                "source": source}
    return {"ok": True, "diagnosis": diagnosis, "remedy_type": remedy_type,
            "result": done, "source": source}


_VERLEER_NA = 3


def _verleer_bij_aanhoudend_falen(signature: str) -> None:
    """Een remedie die aantoonbaar niets doet, mag niet blijven terugkomen.

    `_known_remedy` slaat de LLM over zodra er een remedie bij deze handtekening
    staat. Zonder deze rem betekent dat: één ongelukkige keuze, en elke volgende
    klik op 'Analyseer & fix' herhaalt hem voor altijd — stil, want er wordt
    netjes een uitvoering gemeld. Na drie pogingen zonder één succes gaat de
    remedie op `active = 0`, waarna de volgende klik opnieuw diagnosticeert.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE iris_error_fixes SET active = 0 WHERE signature = ? "
            "AND active = 1 AND successes = 0 AND attempts >= ?",
            (signature, _VERLEER_NA),
        )


async def analyze_and_fix_all(kinds: Optional[list] = None) -> Dict[str, Any]:
    """Bulk-variant van analyze_and_fix: analyseer + herstel alle foutkaarten
    in het Actiecentrum in één keer, op de achtergrond. Voor patroon-errors
    (OpenModel-down, MS-auth, catch-up) gebruikt elk item de directe diagnose —
    géén LLM per kaart. Terug met een job_id; resultaten landen in de
    outcome-feed en als kaart-updates.

    `kinds` (optioneel) beperkt tot bepaalde kaart-soorten; default = alle
    error-kaarten (activity_log-fouten + mislukte content_jobs).
    """
    import threading, uuid as _uuid
    from . import service as _ac

    job_id = "trijob_" + _uuid.uuid4().hex[:10]

    def _collect_ids() -> list:
        ids = []
        # activity_log-fouten (laatste ERROR_WINDOW_DAYS dagen) — zelfde filter
        # als de Actiecentrum-inbox. Lokaal gespiegeld (3 dagen) i.p.v. import uit
        # shared.config, want die const leeft in action_center.service — een
        # `from ...shared.config import ERROR_WINDOW_DAYS` crasht met ImportError.
        ERROR_WINDOW_DAYS = 3
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT id FROM activity_log WHERE (status='error' OR action LIKE '%fout%') "
                "AND created_at > datetime('now', ?) ORDER BY created_at DESC",
                (f"-{ERROR_WINDOW_DAYS} day",),
            ):
                ids.append({"id": r["id"], "kind": "activity_log"})
            # mislukte content_jobs
            for r in conn.execute(
                "SELECT id FROM content_jobs WHERE status IN ('publish_failed','error','needs_work') "
                "ORDER BY created_at DESC"
            ):
                ids.append({"id": r["id"], "kind": "content_job"})
        return ids

    def _run():
        ids = _collect_ids()
        done = 0
        for spec in ids:
            try:
                # analyze_and_fix doet patroonherkenning zelf (OpenModel-down,
                # MS-auth, catch-up) — géén LLM nodig voor die errors, ook niet
                # als de LLM-backend platligt. Alleen onbekende fouten roepen de
                # LLM aan; als die faalt, valt analyze_and_fix terug op de
                # patroon-diagnose. Dus de bulk-loop blijft altijd nuttig.
                res = analyze_and_fix(spec["id"], kind=spec["kind"])
            except Exception as e:  # noqa: BLE001
                logger.exception("[iris] bulk-triage mislukt voor %s/%s", spec["kind"], spec["id"])
                res = {"ok": False, "error": str(e)[:200]}
            # Een "human_step" of een patroon-diagnose is géén fout — het is de
            # juiste uitkomst. Alleen echte excepties tellen als mislukt.
            done += 1
        logger.info("[iris] bulk-triage %s klaar: %d kaart(en) verwerkt", job_id, done)

    t = threading.Thread(target=_run, name=job_id, daemon=True)
    t.start()
    return {"ok": True, "background": True, "job_id": job_id,
            "message": "Alle foutkaarten worden op de achtergrond geanalyseerd. Ververs het Actiecentrum over enkele seconden voor de resultaten."}
