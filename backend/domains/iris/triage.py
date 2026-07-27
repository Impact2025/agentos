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
    "Je bent Iris, de manager van Agent OS. Vincent klikte 'Analyseer & fix' op "
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
                "j.error AS detail, j.created_at FROM content_jobs j "
                "LEFT JOIN sites s ON s.id = j.site_id WHERE j.id = ?",
                (error_id,),
            ).fetchone()
            return dict(row) if row else None
        row = conn.execute(
            "SELECT id, project, action, detail, created_at FROM activity_log WHERE id = ?",
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


async def analyze_and_fix(error_id: str, kind: str = "activity_log") -> Dict[str, Any]:
    """Diagnosticeer één foutkaart en voer de remedie meteen uit (of leg de
    mens-stap vast als geen agent-actie veilig is). Retourneert altijd een
    dict met minstens 'diagnosis'; 'ok' geeft aan of een agent-actie draaide."""
    err = _load_error(error_id, kind)
    if not err:
        return {"ok": False, "error": "Foutmelding niet gevonden"}

    signature = _signature(err["action"], err["detail"] or "")
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
    return {"ok": ok, "diagnosis": diagnosis, "remedy_type": remedy_type,
            "result": done or "Geen uitvoering opgeleverd — zie de foutkaart hierboven.",
            "source": source}
