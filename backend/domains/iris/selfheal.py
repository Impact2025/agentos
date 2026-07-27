"""Iris' zelfherstel-ronde — ze probeert fouten éérst zelf op te lossen.

Waar `triage.py` wacht tot Vincent op "Analyseer & fix" klikt, draait dit
ongevraagd (scheduler, elke 10 min, en aan het begin van de ochtendbriefing).
De regel is die van een goede manager: **wat je zelf kunt controleren, meld je
niet.**

De drie stappen, in deze volgorde:

1. **Classificeren** (`shared/failures.py`) — is dit een blip (`transient`), iets
   dat alleen een mens kan (`auth`/`config`), een kwestie van tijd (`quota`/
   `ratelimit`), of onbekend? Die vraag bepaalt álles: bij een verlopen token is
   nóg een poging pure tijdverspilling, bij een TLS-blip is een melding pure
   ruis.
2. **Bewijzen, niet gokken** — voor de klassen die zichzelf kunnen herstellen
   voert Iris een *probe* uit: dezelfde bewerking nog eens echt draaien. Lukt
   het nu, dan is de fout aantoonbaar weg en gaat de kaart dicht met een
   uitkomstkaart die vertelt wat er gebeurde. Een probe is nooit "publiceer" of
   "verstuur" — alleen lezen/synchroniseren (`_SAFE_JOB_PROBES`), zodat
   zelfherstel nooit een review-gate passeert.
3. **Escaleren met inhoud** — pas als de probe herhaald faalt (of de klasse
   mens-alleen is) komt er één kaart, mét de concrete stap. Niet "controleer de
   tokens" als de tokens niets mankeren.

Leren over fouten heen deelt Iris met de handmatige triage: dezelfde
vingerafdruk (`triage._signature`) en dezelfde tabel `iris_error_fixes`. Werkte
een remedie eerder voor deze handtekening, dan pakt ze die meteen; faalde hij
drie keer zonder succes, dan probeert ze het niet nog eens maar meldt ze het —
met wat ze inmiddels weet. Kent ze de handtekening niet, dan kijkt ze naar
*verwante* fouten: zelfde actie + zelfde faalklasse. Zo profiteert een nieuwe
storing van wat een oude leerde.

Elke poging (geslaagd of niet) landt in `iris_heal_log`, niet in `activity_log`:
een mislukte poging mag geen inbox-item worden, anders vervang je één rode kaart
door drie.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from ...shared import failures as fail
from .triage import _signature

logger = logging.getLogger(__name__)

# Hoeveel fouten Iris per ronde aanpakt. Een probe kost een netwerkcall; met een
# ronde per 10 minuten is dit ruim genoeg en blijft de ronde kort.
MAX_CASES_PER_RUN = 8

# Zoveel mislukte probes op dezelfde handtekening en Iris houdt op met proberen.
MAX_PROBE_ATTEMPTS = 3

# Scheduler-jobs die veilig opnieuw gedraaid mogen worden als probe: allemaal
# lezen/synchroniseren. Bewust géén content-, publicatie- of outreach-jobs —
# die maken werk aan (en kosten tokens), en zelfherstel hoort niets te
# produceren wat een mens nog moet beoordelen.
_SAFE_JOB_PROBES = {
    "calendar_sync", "gsc_sync", "bridge_sync", "goal_autoheal", "link_monitor",
}
# Prefixen van dynamisch gegenereerde jobs (één per mailbox / social inbox).
# Die halen op en zetten concepten klaar achter de review-gate — herhalen is
# veilig (dedupe op external_id), en juist die polls falen op een netwerkblip.
_SAFE_JOB_PREFIXES = ("mail_", "social_")


def _now_iso() -> str:
    return datetime.now().isoformat()


# ── Wat staat er open? ─────────────────────────────────────────────────────

def _open_cases(limit: int = MAX_CASES_PER_RUN) -> List[Dict[str, Any]]:
    """Alle fouten die nú op een mens wachten — dezelfde bron als het
    Actiecentrum, zodat Iris precies ziet wat Vincent ziet."""
    cases: List[Dict[str, Any]] = []
    with get_conn() as conn:
        dismissed = {
            (r["kind"], r["ref_id"])
            for r in conn.execute("SELECT kind, ref_id FROM inbox_dismissals")
        }
        rows = conn.execute(
            "SELECT id, project, action, detail, next_step, created_at FROM activity_log "
            "WHERE status = 'error' AND created_at > datetime('now', '-3 day') "
            "ORDER BY created_at DESC LIMIT 40"
        ).fetchall()
        for r in rows:
            row = dict(r)
            # 'iris_actie'/'iris_zelfherstel' zijn meta over een andere fout —
            # daarop zelfherstel loslaten is jezelf achterna lopen.
            if row["action"] in ("iris_actie", "iris_zelfherstel"):
                continue
            if ("error", row["id"]) in dismissed:
                continue
            try:
                from . import metrics as _metrics
                if _metrics._error_resolved(conn, row):
                    continue
            except Exception:  # noqa: BLE001 — resolver-fout mag de ronde niet stoppen
                pass
            cases.append({
                "kind": "activity_log",
                "id": row["id"],
                "project": row["project"] or "",
                "action": row["action"] or "",
                "detail": row["detail"] or "",
                "created_at": row["created_at"],
            })

    # Scheduler-fouten leven in hun eigen tabel en hebben geen activity_log-rij.
    try:
        from ...scheduler import get_scheduler_status
        for job in get_scheduler_status().get("jobs", []):
            last = job.get("last_run") or {}
            if last.get("status") != "error":
                continue
            if ("scheduler", job["id"]) in dismissed:
                continue
            cases.append({
                "kind": "scheduler",
                "id": job["id"],
                "project": "Scheduler",
                "action": f"scheduler:{job['id']}",
                "detail": last.get("error") or "",
                "created_at": last.get("time") or "",
                "label": job.get("label") or job["id"],
            })
    except Exception:  # noqa: BLE001 — geen scheduler (bv. in tests) is geen fout
        logger.debug("[iris-selfheal] scheduler-status niet beschikbaar", exc_info=True)

    return cases[:limit]


# ── Geheugen: wat weten we al over deze fout? ──────────────────────────────

def _learned(signature: str, action: str, klass: str) -> Optional[Dict[str, Any]]:
    """Wat werkte eerder? Eerst exact deze handtekening, anders een verwante
    fout (zelfde actie + zelfde faalklasse). Dat tweede is het 'leren van andere
    fouten': een nieuwe storing in dezelfde hoek erft wat de vorige opleverde."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM iris_error_fixes WHERE signature = ?", (signature,)
        ).fetchone()
        if row:
            return dict(row)
        # Verwant: zelfde actie-deel van de handtekening, en een remedie die
        # zich bewezen heeft (minstens één succes, meer successen dan mislukkingen).
        prefix = f"{(action or '').strip().lower()}::"
        sib = conn.execute(
            "SELECT * FROM iris_error_fixes WHERE signature LIKE ? AND successes > 0 "
            "AND successes >= failures AND active = 1 ORDER BY successes DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        if sib:
            out = dict(sib)
            out["_inherited"] = True
            return out
    return None


def _remember_attempt(signature: str, case: Dict[str, Any], klass: str,
                      remedy: str, ok: bool, note: str, diagnosis: str = "") -> None:
    """Werk het gedeelde repertoire bij (zelfde tabel als de handmatige triage)."""
    now = _now_iso()
    col = "successes" if ok else "failures"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM iris_error_fixes WHERE signature = ?", (signature,)
        ).fetchone()
        if row:
            conn.execute(
                f"UPDATE iris_error_fixes SET attempts = attempts + 1, {col} = {col} + 1, "
                "last_result = ?, updated_at = ? WHERE signature = ?",
                (note[:300], now, signature),
            )
        else:
            conn.execute(
                "INSERT INTO iris_error_fixes (id, signature, project, sample_action, "
                "sample_detail, diagnosis, remedy_type, remedy_payload, human_step, "
                "attempts, successes, failures, occurrences, last_result, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'{}','',1,?,?,1,?,?,?)",
                (f"heal-{uuid.uuid4().hex[:12]}", signature, case.get("project", ""),
                 case.get("action", ""), (case.get("detail") or "")[:300],
                 diagnosis[:400] or f"faalklasse: {klass}", remedy,
                 1 if ok else 0, 0 if ok else 1, note[:300], now, now),
            )


def _log_heal(case: Dict[str, Any], signature: str, klass: str,
              remedy: str, result: str, note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO iris_heal_log (id, signature, source_kind, source_id, project, "
            "action, failure_class, remedy, result, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"heal-{uuid.uuid4().hex[:12]}", signature, case.get("kind", ""),
             case.get("id", ""), case.get("project", ""), case.get("action", ""),
             klass, remedy, result, note[:400], _now_iso()),
        )


def _count_failed_probes(conn, signature: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM iris_heal_log WHERE signature = ? "
        "AND result = 'failed' AND created_at > datetime('now', '-2 day')",
        (signature,),
    ).fetchone()
    return int(row["n"] if row else 0)


def _probe_attempts(signature: str) -> int:
    with get_conn() as conn:
        return _count_failed_probes(conn, signature)


def _escalated_source(signature: str) -> Optional[str]:
    """Wélke kaart is voor deze storing al gemeld (of None)?

    Niet alleen "is er al gemeld": de gemelde kaart zélf moet zichtbaar blijven
    staan tot een mens hem oplost. Alleen de dúbbele kaarten van dezelfde
    storing worden opgevouwen — anders verstopt Iris bij de volgende ronde haar
    eigen melding.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source_id FROM iris_heal_log WHERE signature = ? AND result = 'escalated' "
            "AND created_at > datetime('now', '-1 day') ORDER BY created_at DESC LIMIT 1",
            (signature,),
        ).fetchone()
    return row["source_id"] if row else None


# ── Probes: opnieuw proberen, en het bewijs afwachten ──────────────────────

async def _probe_network() -> Tuple[bool, str]:
    """Staat het netwerk überhaupt weer? Geen vervanging voor een echte probe,
    maar bij een fout zonder eigen probe is dit het enige harde feit dat we
    kunnen vaststellen."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://www.google.com/generate_204")
        ok = r.status_code in (200, 204)
        return ok, "netwerk bereikbaar" if ok else f"netwerk antwoordt met HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"netwerk nog steeds onbereikbaar: {fail.describe_exception(e)}"


async def _probe_social_fetch(case: Dict[str, Any]) -> Tuple[bool, str]:
    """Haal de social-kanalen van dit project echt opnieuw op.

    Bewust `fetch_new` en niet `run_inbox`: die laatste vángt fouten af en legt
    er zelf een kaart voor aan — dan zou de probe "gelukt" rapporteren terwijl
    het kanaal ons afwijst, en zou zelfherstel een kaart sluiten op grond van
    een fout die het net zelf weer aanmaakte. Een probe hoort te verifiëren,
    niet te verwerken; de gewone poll doet het echte werk 30 minuten later.
    """
    from ...shared import social_inbox
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM social_inboxes WHERE lower(project) = lower(?) "
            "AND enabled = 1", (case.get("project") or "",),
        ).fetchall()
    if not rows:
        return False, "geen actieve social-inbox meer voor dit project"
    notes: List[str] = []
    for r in rows:
        inbox = dict(r)
        try:
            found = await social_inbox.fetch_new(inbox)
            notes.append(f"{inbox['platform']}: ok ({len(found)} berichten)")
        except Exception as e:  # noqa: BLE001
            return False, f"{inbox['platform']}: {fail.describe_exception(e)}"
    return True, "; ".join(notes)


async def _probe_scheduler_job(case: Dict[str, Any]) -> Tuple[bool, str]:
    """Draai een lees-/sync-job opnieuw. Alleen jobs uit de veilige lijst: een
    zelfherstel-ronde hoort niets te produceren dat een mens moet beoordelen."""
    job_id = case["id"]
    safe = job_id in _SAFE_JOB_PROBES or job_id.startswith(_SAFE_JOB_PREFIXES)
    if not safe:
        return False, (
            f"job '{job_id}' wordt niet automatisch herhaald (maakt werk aan) — "
            "hij draait vanzelf op zijn volgende geplande moment"
        )
    try:
        from ...scheduler import _BY_ID, _record_run
    except Exception as e:  # noqa: BLE001
        return False, f"scheduler niet beschikbaar: {fail.describe_exception(e)}"
    spec = _BY_ID.get(job_id)
    if not spec:
        return False, f"job '{job_id}' bestaat niet meer"
    try:
        import asyncio
        import inspect
        if inspect.iscoroutinefunction(spec.func):
            await spec.func()
        else:
            # Synchrone jobs draaien in een thread, precies zoals APScheduler dat
            # doet. Rechtstreeks aanroepen zou hier omvallen: `calendar_sync_job`
            # doet intern `asyncio.run()`, en dat mag niet vanuit een lopende
            # event loop. De probe zou dan "job kapot" rapporteren terwijl er
            # niets mis is met de job — een zelfherstel dat zichzelf voor de gek
            # houdt.
            await asyncio.to_thread(spec.func)
    except Exception as e:  # noqa: BLE001
        return False, fail.describe_exception(e)
    # De run-historie moet meebewegen, anders blijft de oude fout in het
    # Actiecentrum staan terwijl de job aantoonbaar weer werkt.
    try:
        _record_run(job_id, "ok", None, source="selfheal")
    except Exception:  # noqa: BLE001
        logger.debug("[iris-selfheal] kon run-historie niet bijwerken", exc_info=True)
    return True, "job opnieuw gedraaid en geslaagd"


def _probe_for(case: Dict[str, Any]) -> Optional[Callable]:
    if case["kind"] == "scheduler":
        return _probe_scheduler_job
    action = (case.get("action") or "").lower()
    if action == "social_fetch":
        return _probe_social_fetch
    return None


# ── Afronden: dicht, of gemeld ─────────────────────────────────────────────

def _close_case(case: Dict[str, Any], note: str) -> None:
    """Haal de kaart uit het Actiecentrum en leg vast waaróm dat mocht."""
    # Alleen activity_log-kaarten worden weggeklikt. Een scheduler-item is geen
    # kaart maar een spiegel van `scheduler_runs`: de geslaagde probe zet die op
    # 'ok' en dan verdwijnt het item vanzelf. Zou je hem hier wegklikken, dan is
    # de job voorgoed onzichtbaar — óók als hij volgende week écht stukgaat,
    # want `inbox_dismissals` is per job-id en kent geen verval.
    if case["kind"] == "activity_log":
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO inbox_dismissals (kind, ref_id, dismissed_at) "
                "VALUES ('error', ?, datetime('now'))",
                (case["id"],),
            )
    label = case.get("label") or case.get("action") or "fout"
    log_outcome(
        project=case.get("project") or "Systeem",
        action="iris_zelfherstel",
        detail=f"Iris loste '{label}' zelf op: {note}",
        status="ok",
    )
    _reset_streaks(case)


def _reset_streaks(case: Dict[str, Any]) -> None:
    """Zet de faal-teller van de betrokken bron terug op nul.

    Zonder dit escaleert de eerstvolgende losse blip meteen, omdat de teller nog
    op drie staat van de storing die Iris net zelf oploste. De sleutel hangt aan
    de bron (inbox-id, job-id), niet aan de kaart — die heeft een eigen id.
    """
    if (case.get("action") or "").lower() == "social_fetch":
        with get_conn() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM social_inboxes WHERE lower(project) = lower(?)",
                (case.get("project") or "",),
            )]
        for inbox_id in ids:
            fail.note_success(f"social_fetch:{inbox_id}")
    elif case["kind"] == "scheduler":
        fail.note_success(f"scheduler:{case['id']}")


def _fold_duplicate(case: Dict[str, Any], signature: str, klass: str) -> None:
    """Verberg een tweede kaart van een storing die al gemeld is.

    Dezelfde storing die twee nachten achter elkaar toesloeg gaf twee identieke
    rode kaarten (zie de screenshot van 25 jul). Eén storing hoort één kaart te
    zijn; de oudste verdwijnt, de gemelde blijft staan tot hij écht opgelost is.
    Geen 'opgelost'-uitkomstkaart hier — er ís niets opgelost.
    """
    if case["kind"] != "activity_log":
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO inbox_dismissals (kind, ref_id, dismissed_at) "
            "VALUES ('error', ?, datetime('now'))",
            (case["id"],),
        )
    _log_heal(case, signature, klass, "fold_duplicate", "folded",
              "zelfde storing stond al gemeld — dubbele kaart samengevouwen")


def _enrich_detail(case: Dict[str, Any], finding: str) -> None:
    """Vul een nietszeggende foutkaart aan met wat de probe ontdekte.

    Een kaart die eindigt op "mislukt: " helpt niemand — niet Vincent, niet
    Iris bij de volgende ronde (die tekst is niet te classificeren).
    """
    if case["kind"] != "activity_log" or not finding:
        return
    detail = (case.get("detail") or "").strip()
    if finding[:60].lower() in detail.lower():
        return
    nieuw = f"{detail} — Iris' bevinding bij hercontrole: {finding}".strip(" —")
    with get_conn() as conn:
        conn.execute("UPDATE activity_log SET detail = ? WHERE id = ?",
                     (nieuw[:1000], case["id"]))
    case["detail"] = nieuw


def _escalate(case: Dict[str, Any], signature: str, klass: str, note: str) -> str:
    """Eén heldere melding, met de stap die écht helpt."""
    label = case.get("label") or case.get("action") or "fout"
    human = _human_step(case, klass)
    detail = (
        f"Iris kreeg '{label}' niet zelf opgelost ({klass}). "
        f"Wat ze probeerde: {note}"
    )
    if case["kind"] == "activity_log":
        # De bestaande kaart aanvullen in plaats van een tweede kaart maken:
        # twee rode kaarten voor één storing is precies de ruis die we bestrijden.
        with get_conn() as conn:
            conn.execute(
                "UPDATE activity_log SET next_step = ? WHERE id = ?",
                (human, case["id"]),
            )
    else:
        log_outcome(
            project=case.get("project") or "Systeem",
            action="iris_zelfherstel",
            detail=detail,
            next_step=human,
            status="error",
        )
    _log_heal(case, signature, klass, "escalate", "escalated", note)
    return human


def _human_step(case: Dict[str, Any], klass: str) -> str:
    """De concrete stap voor Vincent — per faalklasse, niet één generieke zin."""
    action = (case.get("action") or "").lower()
    if klass == fail.CLASS_AUTH:
        if "social" in action or "instagram" in (case.get("detail") or "").lower():
            return ("Vernieuw het kanaal-token in de Social-tab (Meta Business → "
                    "Toegangstokens). Iris kan dit niet zelf: een token vernieuwen "
                    "vereist inloggen als jou.")
        if "calendar" in action or "agenda" in action:
            return ("Deel de agenda met het service-account (Google Agenda → "
                    "Instellingen → Delen met specifieke personen).")
        return "Vernieuw de inloggegevens van deze koppeling; een agent kan dat niet."
    if klass == fail.CLASS_CONFIG:
        return "Vul de ontbrekende instelling aan in .env en herstart de server."
    if klass == fail.CLASS_QUOTA:
        return ("Wacht tot de quota terugkomt, of verhoog de limiet bij de provider. "
                "Iris pauzeert autonome runs zolang de rem actief is.")
    if klass == fail.CLASS_RATELIMIT:
        return "Wacht — de provider limiteert ons tempo; Iris probeert het later opnieuw."
    if klass == fail.CLASS_TRANSIENT:
        return ("Controleer de internetverbinding van deze machine. Het probleem "
                "hield aan over meerdere pogingen, dus het is geen losse blip meer.")
    return "Bekijk deze fout handmatig — Iris kon geen veilige remedie vaststellen."


# ── De ronde ───────────────────────────────────────────────────────────────

async def _heal_case(case: Dict[str, Any]) -> Dict[str, Any]:
    signature = _signature(case.get("action", ""), case.get("detail", ""))
    klass = fail.classify(case.get("detail") or "")
    learned = _learned(signature, case.get("action", ""), klass)
    out = {"id": case["id"], "action": case.get("action"), "class": klass}

    # 1. Mens-alleen: proberen heeft geen zin, wél meteen duidelijk melden.
    if klass in fail.HUMAN_ONLY:
        gemeld = _escalated_source(signature)
        if gemeld:
            if gemeld != case["id"]:
                _fold_duplicate(case, signature, klass)
            return {**out, "result": "escalated_earlier"}
        human = _escalate(case, signature, klass,
                          "niets — dit type fout kan geen agent oplossen")
        return {**out, "result": "escalated", "human_step": human}

    # 2. Bewezen kansloos: eerder al herhaald geprobeerd zonder succes.
    attempts = _probe_attempts(signature)
    hopeless = bool(learned and not learned.get("_inherited")
                    and (learned.get("failures") or 0) >= MAX_PROBE_ATTEMPTS
                    and not (learned.get("successes") or 0))
    if attempts >= MAX_PROBE_ATTEMPTS or hopeless:
        gemeld = _escalated_source(signature)
        if gemeld:
            if gemeld != case["id"]:
                _fold_duplicate(case, signature, klass)
            return {**out, "result": "escalated_earlier"}
        human = _escalate(
            case, signature, klass,
            f"{max(attempts, learned.get('failures', 0) if learned else 0)}× opnieuw "
            "geprobeerd, steeds dezelfde fout",
        )
        return {**out, "result": "escalated", "human_step": human}

    # 3. Probe: hetzelfde werk écht opnieuw doen.
    probe = _probe_for(case)
    if probe is not None:
        try:
            ok, note = await probe(case)
        except Exception as e:  # noqa: BLE001 — een kapotte probe is geen crash waard
            logger.exception("[iris-selfheal] probe voor %s viel om", case["id"])
            ok, note = False, f"probe zelf viel om: {fail.describe_exception(e)}"
        _remember_attempt(signature, case, klass, "probe", ok, note)
        if ok:
            _log_heal(case, signature, klass, "probe", "healed", note)
            _close_case(case, note)
            return {**out, "result": "healed", "note": note}
        _log_heal(case, signature, klass, "probe", "failed", note)
        # De probe weet vaak méér dan de kaart. De kaarten van 25 jul zeiden
        # letterlijk "Ophalen van instagram mislukt: " — niets om op te
        # classificeren — terwijl een verse poging meteen "token verlopen"
        # oplevert. Dan is doorproberen zinloos en melden we nú, met de échte
        # oorzaak in plaats van de lege oude tekst.
        probe_klass = fail.classify(note)
        if probe_klass in fail.HUMAN_ONLY:
            gemeld = _escalated_source(signature)
            if gemeld:
                if gemeld != case["id"]:
                    _fold_duplicate(case, signature, probe_klass)
                return {**out, "result": "escalated_earlier", "class": probe_klass}
            _enrich_detail(case, note)
            human = _escalate(case, signature, probe_klass,
                              f"opnieuw geprobeerd; oorzaak blijkt: {note}")
            return {**out, "result": "escalated", "class": probe_klass,
                    "human_step": human, "note": note}
        return {**out, "result": "retry_later", "note": note}

    # 4. Geen eigen probe. Bij een netwerkfout kunnen we wél vaststellen of de
    #    oorzaak weg is; dan was het aantoonbaar een blip.
    if klass == fail.CLASS_TRANSIENT:
        ok, note = await _probe_network()
        _remember_attempt(signature, case, klass, "network_check", ok, note)
        if ok:
            _log_heal(case, signature, klass, "network_check", "healed", note)
            _close_case(case, f"tijdelijke netwerkstoring, inmiddels voorbij ({note})")
            return {**out, "result": "healed", "note": note}
        _log_heal(case, signature, klass, "network_check", "failed", note)
        return {**out, "result": "retry_later", "note": note}

    # 5. Quota/ratelimit lossen zichzelf op zodra de rem eraf is.
    if klass in (fail.CLASS_QUOTA, fail.CLASS_RATELIMIT):
        from ...shared.outcomes import llm_quota_backoff_active
        try:
            waiting = llm_quota_backoff_active()
        except Exception:  # noqa: BLE001
            waiting = False
        if waiting:
            _log_heal(case, signature, klass, "wait", "waiting", "quota-rem nog actief")
            return {**out, "result": "waiting"}
        _close_case(case, "de quota-rem is verlopen; de volgende run draait weer normaal")
        _log_heal(case, signature, klass, "wait", "healed", "quota-rem verlopen")
        return {**out, "result": "healed"}

    # 6. Onbekend: hier mag de LLM-triage kijken — maar één keer per
    #    handtekening, anders betaalt elke ronde opnieuw voor dezelfde vraag.
    if learned is None and case["kind"] == "activity_log":
        try:
            from . import triage
            res = await triage.analyze_and_fix(case["id"])
            note = res.get("result") or res.get("human_step") or res.get("diagnosis") or ""
            ok = bool(res.get("ok")) and res.get("remedy_type") != "human_step"
            _log_heal(case, signature, klass, f"triage:{res.get('remedy_type', '?')}",
                      "healed" if ok else "failed", str(note))
            if ok:
                _close_case(case, f"Iris' diagnose + remedie: {note}")
                return {**out, "result": "healed", "note": note}
            return {**out, "result": "diagnosed", "note": note}
        except Exception as e:  # noqa: BLE001
            logger.warning("[iris-selfheal] LLM-triage mislukt: %s", fail.describe_exception(e))

    _log_heal(case, signature, klass, "none", "waiting", "geen remedie bekend")
    return {**out, "result": "waiting"}


async def run_selfheal(*, limit: int = MAX_CASES_PER_RUN,
                       source: str = "scheduler") -> Dict[str, Any]:
    """Eén zelfherstel-ronde. Retourneert een samenvatting per geval.

    Werpt nooit: een ronde die zelf omvalt zou een foutkaart opleveren over het
    oplossen van foutkaarten.
    """
    try:
        cases = _open_cases(limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("[iris-selfheal] kon openstaande fouten niet lezen")
        return {"ok": False, "error": fail.describe_exception(e), "results": []}

    results: List[Dict[str, Any]] = []
    for case in cases:
        try:
            results.append(await _heal_case(case))
        except Exception as e:  # noqa: BLE001
            logger.exception("[iris-selfheal] geval %s mislukte", case.get("id"))
            results.append({"id": case.get("id"), "result": "error",
                            "note": fail.describe_exception(e)})

    healed = [r for r in results if r.get("result") == "healed"]
    escalated = [r for r in results if r.get("result") == "escalated"]
    if healed or escalated:
        logger.info(
            "[iris-selfheal] %d gevallen bekeken (%s): %d zelf opgelost, %d gemeld",
            len(results), source, len(healed), len(escalated),
        )
    return {
        "ok": True,
        "source": source,
        "checked": len(results),
        "healed": len(healed),
        "escalated": len(escalated),
        "results": results,
    }


def recent_heals(limit: int = 20) -> List[Dict[str, Any]]:
    """Logboek voor de UI: wat heeft Iris de laatste tijd zelf opgelost?"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM iris_heal_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def heal_status(signature_action: str, detail: str, conn=None) -> Optional[Dict[str, Any]]:
    """Waar staat Iris met déze fout? Voor het Actiecentrum, zodat een kaart
    kan zeggen "Iris probeert dit zelf (poging 2)" in plaats van te doen alsof
    er meteen een mens aan te pas moet komen.

    `conn` mag meegegeven worden: het Actiecentrum loopt al door een open
    connectie heen, en dat is een pad dat elke 30 seconden draait — daar hoort
    geen verse SQLite-verbinding per foutkaart bij.
    """
    if conn is None:
        with get_conn() as own:
            return heal_status(signature_action, detail, conn=own)
    signature = _signature(signature_action, detail)
    attempts = _count_failed_probes(conn, signature)
    if not attempts:
        return None
    row = conn.execute(
        "SELECT result, note, created_at FROM iris_heal_log WHERE signature = ? "
        "ORDER BY created_at DESC LIMIT 1", (signature,),
    ).fetchone()
    last = dict(row) if row else {}
    return {
        "attempts": attempts,
        "max_attempts": MAX_PROBE_ATTEMPTS,
        "last_result": last.get("result", ""),
        "last_note": last.get("note", ""),
        "last_at": last.get("created_at", ""),
    }
