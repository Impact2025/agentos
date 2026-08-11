"""Iris' actie-executor — de brug van "Wil je dat ik dit fix?" naar de agents.

De briefing (service.py) legt concreet uitvoerbare acties klaar in
`iris_suggestions` (status='pending'). Dit module voert ze pas uit als
Vincent ze goedkeurt (apply). Elke actie-type is gebonden aan een
bestaande, veilige infrastructuur en landt ALTIJD achter een
review-gate:

  content_run  -> actions.content_run      -> artikelen in Wachtrij (geen live)
  seo_refresh -> actions.seo_refresh    -> verrijkte pagina's in Wachtrij
  outreach_run -> actions.outreach_run    -> concepten ter review (geen verzending)
  lead_search_run -> actions.lead_search_run -> nieuwe leads als 'new' (geen mail)
  gsc_connect  -> logt een concreet "koppel GSC"-kaart (menselijke stap)
  goal_draft  -> actions/_apply_draft_goal -> concept-doel in Actiecentrum
  run_job    -> scheduler.run_job_now     -> gemiste geplande taak alsnog

Geen enkele actie publiceert of verstuurt iets zelf. Dedupe:
een actie die al 'applied'/'approved'/'rejected' is, wordt nooit
twee keer gerund.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

# Actie-types die Iris mag uitvoeren, met hun executor. 'gsc_connect'
# is GEEN agent-actie: het is een menselijke stap (de property koppelen
# in Search Console). Die logt alleen een heldere kaart met next_step.
_ALLOWED_TYPES = {"content_run", "seo_refresh", "outreach_run", "linkbuilding_run",
                  "lead_search_run", "goal_draft", "gsc_connect", "run_job"}


def _now_iso() -> str:
    return datetime.now().isoformat()


# ── Persisteren van voorstellen ──────────────────────────────────────────

def upsert_suggestions(report_date: str, suggestions: List[Dict[str, Any]]) -> int:
    """Sla Iris' actie-voorstellen op; dedupe op (report, type, target, title).

    Geeft het aantal nieuw aangemaakte rijen terug. Bestaande
    pending-rijen met dezelfde sleutel worden niet vervangen (zodat een
    'Analyseer nu'-herrun geen dubbele knoppen oplevert).
    """
    saved = 0
    now = _now_iso()
    with get_conn() as conn:
        for s in suggestions[:12]:
            typ = (s.get("type") or "").strip().lower()
            if typ not in _ALLOWED_TYPES:
                continue
            title = (s.get("title") or "").strip()
            target = (s.get("target") or "all")
            if not title:
                continue
            row = conn.execute(
                "SELECT id, status FROM iris_suggestions "
                "WHERE report_date = ? AND type = ? AND target = ? AND title = ?",
                (report_date, typ, target, title),
            ).fetchone()
            if row:
                continue  # al aangeboden — niet overschrijven
            sid = s.get("id") or f"sug-{abs(hash((report_date, typ, target, title))) & 0xffffffff:08x}"
            payload = s.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            conn.execute(
                "INSERT INTO iris_suggestions "
                "(id, report_date, scope, type, title, detail, target, payload, "
                " priority, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    sid,
                    report_date,
                    (s.get("scope") or "all")[:40],
                    typ,
                    title[:200],
                    (s.get("detail") or "")[:600],
                    str(target)[:120],
                    json.dumps(payload, ensure_ascii=False),
                    int(s.get("priority") or 5),
                    now,
                ),
            )
            saved += 1
    return saved


def list_pending(report_date: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if report_date:
            rows = conn.execute(
                "SELECT * FROM iris_suggestions WHERE report_date = ? "
                "ORDER BY priority ASC, created_at ASC",
                (report_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM iris_suggestions ORDER BY report_date DESC, "
                "priority ASC, created_at ASC"
            ).fetchall()
    out = []
    for r in rows:
        rec = dict(r)
        try:
            rec["payload"] = json.loads(rec.get("payload") or "{}")
        except json.JSONDecodeError:
            rec["payload"] = {}
        out.append(rec)
    return out


# ── Beslissingen (menselijke stap) ────────────────────────────────────

def _decide(sid: str, decision: str) -> Optional[Dict[str, Any]]:
    allowed = {"approved", "rejected", "pending"}
    if decision not in allowed:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, status FROM iris_suggestions WHERE id = ?", (sid,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE iris_suggestions SET status = ?, decided_at = ? WHERE id = ?",
            (decision, _now_iso(), sid),
        )
        return dict(row)


def reject(sid: str) -> bool:
    return _decide(sid, "rejected") is not None


def approve(sid: str) -> bool:
    return _decide(sid, "approved") is not None


# ── Uitvoering (na goedkeuring) ───────────────────────────────────────

def _load(sid: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM iris_suggestions WHERE id = ?", (sid,)
        ).fetchone()
        if not row:
            return None
        rec = dict(row)
        try:
            rec["payload"] = json.loads(rec.get("payload") or "{}")
        except json.JSONDecodeError:
            rec["payload"] = {}
        return rec


def _finish(sid: str, status: str, detail: str, goal_id: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE iris_suggestions SET status = ?, applied_detail = ?, "
            "applied_at = ?, goal_id = ? WHERE id = ?",
            (status, detail[:600], _now_iso(), goal_id[:200] if goal_id else "", sid),
        )


async def apply(sid: str) -> Dict[str, Any]:
    """Voer één goedgekeurde actie uit via de juiste agent-infra.

    Idempotent: een rij die al 'applied' is, wordt niet herdraaid.
    Alles landt achter een review-gate — nooit directe publicatie/verzending.
    """
    rec = _load(sid)
    if not rec:
        return {"ok": False, "error": "Onbekende actie-id"}
    if rec["status"] in ("applied", "rejected"):
        return {"ok": False, "error": f"Actie is al '{rec['status']}' — niet opnieuw uitvoeren"}
    if rec["status"] not in ("approved", "failed"):
        # Harde regel: eerst goedkeuren. Voorkomt dat een 'pending' actie per
        # ongeluk direct vuurt vanuit de UI. 'failed' blijft herkansbaar —
        # de goedkeuring was er al, alleen de uitvoering strandde.
        return {"ok": False, "error": f"Actie is nog niet goedgekeurd (status: {rec['status']})"}

    typ = rec["type"]
    target = rec.get("target") or ""
    payload = rec.get("payload") or {}
    reason = rec.get("detail") or rec.get("title") or ""
    try:
        if typ == "content_run":
            from . import actions
            n = payload.get("aantal") or payload.get("count") or 1
            done = await actions.content_run(target, n, reason)
            return _result(sid, done, typ, target)
        if typ == "seo_refresh":
            from . import actions
            n = payload.get("aantal") or payload.get("count") or 1
            done = await actions.seo_refresh(target, n, reason)
            return _result(sid, done, typ, target)
        if typ == "outreach_run":
            from . import actions
            n = payload.get("aantal") or payload.get("count") or 5
            done = await actions.outreach_run(n, reason)
            return _result(sid, done, typ, target)
        if typ == "linkbuilding_run":
            from . import actions
            n = payload.get("aantal") or payload.get("count") or 5
            done = await actions.linkbuilding_run(n, reason)
            return _result(sid, done, typ, target)
        if typ == "lead_search_run":
            from . import actions
            done = await actions.lead_search_run(
                payload.get("zoekopdrachten") or payload.get("queries"), reason,
                template=str(payload.get("template") or ""),
                lead_type=str(payload.get("lead_type") or ""))
            return _result(sid, done, typ, target)
        if typ == "goal_draft":
            from .service import _apply_draft_goal
            project = payload.get("project") or target or "WeAreImpact"
            objective = payload.get("doelstelling") or payload.get("objective") or ""
            done = await _apply_draft_goal(project, rec.get("title", ""), objective, reason)
            if done:
                # done is nu {'detail': ..., 'goal_id': ...} — koppel het
                # aangemaakte concept-doel terug zodat het dashboard geen
                # dubbele, tegenstrijdige kaart toont (zie database-migratie).
                return _result(sid, done.get("detail", ""), typ, target, done.get("goal_id", ""))
            return _result(sid, None, typ, target)
        if typ == "run_job":
            # Een gemiste geplande taak alsnog draaien. Zelfde grenzen als de
            # knop in het Actiecentrum (`scheduler.run_job_now`): alleen jobs
            # met catch_up, en geen enkele daarvan publiceert of verstuurt.
            from ...scheduler import run_job_now
            job_id = str(payload.get("job_id") or target or "")
            res = await run_job_now(job_id)
            if not res.get("ok"):
                return _result(sid, None, typ, job_id)
            detail = (f"Gemiste taak '{res.get('label') or job_id}' alsnog gestart — "
                      "de kaart sluit zodra hij slaagt.")
            log_outcome("Scheduler", "iris_actie", detail,
                        next_step="Niets — controleer morgen of de taak geslaagd is.")
            return _result(sid, detail, typ, job_id)
        if typ == "gsc_connect":
            # GEEN agent-actie: dit is een menselijke stap. Log een
            # heldere kaart met de property die gekoppeld moet worden.
            detail = (
                f"GSC-koppeling nodig voor {target or 'dit project'}: "
                f"voeg het service-account toe in Search Console "
                f"(Instellingen > Gebruikers) op {target or 'de property'}."
            )
            log_outcome(
                target or "Iris", "iris_actie", detail,
                next_step="Koppel GSC en draai daarna 'Analyseer nu' — de SEO-pijler wordt dan meetbaar.",
            )
            _finish(sid, "applied", detail)
            return {"ok": True, "detail": detail, "type": typ, "target": target}
        return {"ok": False, "error": f"Onbekend actie-type: {typ}"}
    except Exception as e:  # noqa: BLE001
        logger.exception("[iris] apply() mislukt voor %s (%s)", sid, typ)
        _finish(sid, "failed", f"Fout: {str(e)[:400]}")
        return {"ok": False, "error": str(e)[:400], "type": typ}


def _result(sid: str, done: Optional[str], typ: str, target: str, goal_id: str = "") -> Dict[str, Any]:
    if not done:
        _finish(sid, "failed", f"Geen uitvoering opgeleverd voor {typ} ({target})")
        return {"ok": False, "error": f"{typ} leverde geen resultaat op", "type": typ}
    _finish(sid, "applied", done, goal_id)
    return {"ok": True, "detail": done, "type": typ, "target": target, "goal_id": goal_id}
