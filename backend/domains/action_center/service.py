"""Actiecentrum — één inbox met alles wat op een menselijke beslissing wacht.

Antwoordt op de drie vragen die het dashboard eerder niet beantwoordde:
  1. Wat moet ik (Vincent) nú doen?          → items met needs_you=True
  2. Wat ging er mis en vereist mijn actie?  → items kind='error'
  3. Wat is er gebeurd?                      → de uitkomst-feed (activity_log)

Elk item heeft `actions`: knoppen die de frontend 1-op-1 vertaalt naar
bestaande endpoints. Het Actiecentrum voert zelf niets uit — het verzamelt.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# Vacatures met fit_score (0-100) vanaf deze drempel zijn een inbox-item waard.
VACANCY_FIT_THRESHOLD = 60

# Fouten ouder dan dit aantal dagen vervallen vanzelf uit de inbox.
ERROR_WINDOW_DAYS = 3


def _dismissed(conn) -> set:
    return {
        (r["kind"], r["ref_id"])
        for r in conn.execute("SELECT kind, ref_id FROM inbox_dismissals")
    }


def _goal_task_counts(conn, goal_id: str) -> Dict[str, int]:
    counts = {"total": 0}
    for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM goal_tasks WHERE goal_id=? GROUP BY status",
        (goal_id,),
    ):
        counts[r["status"]] = r["n"]
        counts["total"] += r["n"]
    return counts


def build_inbox() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    with get_conn() as conn:
        skip = _dismissed(conn)

        # ── 1. Doelen die op jou wachten ────────────────────────────────
        for g in conn.execute(
            "SELECT id, title, status, project, created_at FROM goals "
            "WHERE status IN ('draft','ready','failed') ORDER BY created_at DESC"
        ):
            if ("goal", g["id"]) in skip:
                continue
            counts = _goal_task_counts(conn, g["id"])
            if g["status"] == "draft":
                summary = "Plan ligt klaar maar is nooit bevestigd — zonder jouw klik gebeurt er niets."
                actions = [
                    {"label": "Bevestig & start", "type": "goal_confirm_start", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            elif g["status"] == "ready":
                summary = f"Bevestigd ({counts['total']} taken) — wacht op start."
                actions = [
                    {"label": "Start nu", "type": "goal_start", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            else:  # failed
                summary = "Uitvoering is vastgelopen."
                actions = [
                    {"label": "Opnieuw proberen", "type": "goal_retry", "id": g["id"]},
                    {"label": "Verwijder", "type": "goal_delete", "id": g["id"], "danger": True},
                ]
            items.append({
                "kind": f"goal_{g['status']}",
                "dismiss_kind": "goal",
                "id": g["id"],
                "title": g["title"],
                "project": g["project"],
                "created_at": g["created_at"],
                "summary": summary,
                "actions": actions,
            })

        # ── 2. Wachtrij: content dat op review wacht ────────────────────
        # Harde regel: een artikel met score < CONTENT_MIN_SCORE mag NOOIT bij de
        # mens op het dashboard verschijnen — de agent moet het zelf verbeteren.
        # Jobs die desondanks in 'pending_review' met een te lage score staan
        # (oude data vóór de gate-fix, of een vastgelopen verbeter-loop) laten we
        # weg uit de inbox en rapporteren we als inconsistente-staat-logging, zodat
        # de content-verbeteraar (scheduler) ze oppakt i.p.v. de mens.
        from ...shared.config import CONTENT_MIN_SCORE
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='pending_review' ORDER BY j.created_at DESC"
        ):
            score = int(j["seo_score"] or 0)
            if score < CONTENT_MIN_SCORE:
                # Inconsistent: onder grens maar wél in de goedkeuringsqueue.
                # Niet aan Vincent tonen — de agent lost het op (zie
                # content-pipeline improve-loop / scheduler verbeter-taak).
                logger.warning(
                    "[actiecentrum] Job %s (%s) staat op pending_review met score %s "
                    "< grens %s — weggelaten uit inbox, agent moet verbeteren.",
                    j["id"], j["title"], score, CONTENT_MIN_SCORE,
                )
                continue
            items.append({
                "kind": "content_review",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": j["title"],
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": f"Artikel klaar (SEO {j['seo_score']}/100) — goedkeuren publiceert echt.",
                "actions": [
                    {"label": "Bekijk in Wachtrij", "type": "open_tab", "tab": "Wachtrij"},
                    {"label": "Publiceer", "type": "content_approve", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ],
            })

        # ── 2a. Content onder de kwaliteitsgrens: verbeteren of afwijzen ─
        from ...shared.config import CONTENT_MIN_SCORE
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='needs_work' ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            items.append({
                "kind": "content_needs_work",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": j["title"],
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": (
                    f"Score {j['seo_score']}/100 — onder de kwaliteitsgrens ({CONTENT_MIN_SCORE}). "
                    "Publiceren is geblokkeerd; laat de agent herschrijven of wijs af."
                ),
                "actions": [
                    {"label": "Verbeter met AI", "type": "content_regenerate", "id": j["id"]},
                    {"label": "Wijs af", "type": "content_reject", "id": j["id"], "danger": True},
                ],
            })

        # ── 2b. Wachtrij-jobs waarvan publiceren misging: retry mogelijk ─
        for j in conn.execute(
            "SELECT j.id, j.title, j.error, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='error' ORDER BY j.created_at DESC"
        ):
            if ("content", j["id"]) in skip:
                continue
            items.append({
                "kind": "error",
                "dismiss_kind": "content",
                "id": j["id"],
                "title": f"Publiceren mislukt: {j['title']}",
                "project": j["site"] or "?",
                "created_at": j["created_at"],
                "summary": (j["error"] or "Onbekende fout")[:220],
                "actions": [
                    {"label": "Opnieuw publiceren", "type": "content_approve", "id": j["id"]},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "content", "id": j["id"]},
                ],
            })

        # ── 3. Conveyor-taken die op goedkeuring wachten ────────────────
        for t in conn.execute(
            "SELECT id, title, agent, created_at FROM tasks "
            "WHERE status='awaiting_approval' ORDER BY created_at DESC"
        ):
            if ("task", t["id"]) in skip:
                continue
            items.append({
                "kind": "task_approval",
                "dismiss_kind": "task",
                "id": t["id"],
                "title": t["title"],
                "project": t["agent"] or "Pipeline",
                "created_at": t["created_at"],
                "summary": "Taakresultaat wacht op jouw goedkeuring.",
                "actions": [
                    {"label": "Bekijk in Technisch", "type": "open_tab", "tab": "Technisch"},
                    {"label": "Keur goed", "type": "task_approve", "id": t["id"]},
                ],
            })

        # ── 4. Fouten die jouw actie vereisen (laatste 3 dagen) ─────────
        for e in conn.execute(
            "SELECT id, project, action, detail, created_at FROM activity_log "
            "WHERE (status='error' OR action LIKE '%fout%') "
            "AND created_at > datetime('now', ?) ORDER BY created_at DESC LIMIT 10",
            (f"-{ERROR_WINDOW_DAYS} day",),
        ):
            if ("error", e["id"]) in skip:
                continue
            # Zelfherstellend: een publicatiefout waarvoor later een geslaagde
            # 'live' van hetzelfde project+artikel bestaat, is opgelost — dat
            # is een logregel, geen actie-item.
            if e["action"] in ("live-fout", "publish-fout", "live-overgeslagen"):
                title_part = (e["detail"] or "").split("':")[0].lstrip("'")
                fixed = conn.execute(
                    "SELECT 1 FROM activity_log WHERE action='live' AND project=? "
                    "AND detail LIKE ? AND created_at >= ? LIMIT 1",
                    (e["project"], f"%{title_part[:60]}%", e["created_at"]),
                ).fetchone()
                if fixed:
                    continue
            actions: List[Dict[str, Any]] = [
                {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "error", "id": e["id"]},
            ]
            items.append({
                "kind": "error",
                "dismiss_kind": "error",
                "id": e["id"],
                "title": f"{e['action']} — {e['project']}",
                "project": e["project"],
                "created_at": e["created_at"],
                "summary": (e["detail"] or "")[:220],
                "actions": actions,
            })

        # ── 5. Kansen: vacatures met hoge fit (gegroepeerd) ─────────────
        vac = conn.execute(
            "SELECT COUNT(*) AS n, MAX(fit_score) AS top FROM vacancies "
            "WHERE status='new' AND fit_score >= ?",
            (VACANCY_FIT_THRESHOLD,),
        ).fetchone()
        if vac["n"] and ("vacancies", "open") not in skip:
            items.append({
                "kind": "vacancies",
                "dismiss_kind": "vacancies",
                "id": "open",
                "title": f"{vac['n']} interim-opdrachten met fit ≥ {VACANCY_FIT_THRESHOLD}",
                "project": "Opdrachten",
                "created_at": None,
                "summary": f"Beste match: fit {vac['top']}. Reageren op vacatures kan alleen jij.",
                "actions": [
                    {"label": "Open Opdrachten", "type": "open_tab", "tab": "Opdrachten"},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "vacancies", "id": "open"},
                ],
            })

        # ── 5b. Outreach-concepten die op jouw verzendklik wachten ──────
        # De input-kant van de acquisitieformule: de agent schreef het
        # concept, alleen jij kunt versturen (Wachtrij-gate voor e-mail).
        for l in conn.execute(
            "SELECT id, org_name, city, email, outreach_subject, outreach_draft, "
            "outreach_drafted_at, score FROM leads WHERE status='outreach_review' "
            "ORDER BY score DESC, outreach_drafted_at DESC"
        ):
            if ("outreach", l["id"]) in skip:
                continue
            preview = (l["outreach_draft"] or "").replace("\n", " ")[:140]
            items.append({
                "kind": "outreach_review",
                "dismiss_kind": "outreach",
                "id": l["id"],
                "title": f"Outreach klaar: {l['org_name']}" + (f" ({l['city']})" if l["city"] else ""),
                "project": "Leads",
                "created_at": l["outreach_drafted_at"] or None,
                "summary": f"‘{l['outreach_subject']}’ — {preview}",
                "actions": [
                    {"label": "Verstuur", "type": "outreach_send", "id": l["id"]},
                    {"label": "Wijs af (lead vervalt)", "type": "outreach_dismiss", "id": l["id"], "danger": True},
                ],
            })

        # ── 6. Kansen: nieuwe leads (gegroepeerd) ───────────────────────
        leads = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status='new'").fetchone()
        if leads["n"] and ("leads", "open") not in skip:
            items.append({
                "kind": "leads",
                "dismiss_kind": "leads",
                "id": "open",
                "title": f"{leads['n']} nieuwe leads wachten op eerste contact",
                "project": "Leads",
                "created_at": None,
                "summary": "Verrijken kan de agent; benaderen beslis jij.",
                "actions": [
                    {"label": "Open Leads", "type": "open_tab", "tab": "Leads"},
                    {"label": "Gezien, verberg", "type": "dismiss", "dismiss_kind": "leads", "id": "open"},
                ],
            })

        # ── 2c. Mail helpdesk: concept-antwoorden wachten op goedkeuring ──
        for r in conn.execute(
            "SELECT r.id, r.to_addr, r.subject, r.draft_body, r.created_at, "
            "m.project, m.address, i.from_name "
            "FROM mail_reply r "
            "JOIN mailboxes m ON m.id=r.mailbox_id "
            "JOIN mail_inbox i ON i.id=r.inbox_id "
            "WHERE r.status='pending_review' ORDER BY r.created_at DESC"
        ):
            if ("mail", r["id"]) in skip:
                continue
            items.append({
                "kind": "mail_reply",
                "dismiss_kind": "mail",
                "id": r["id"],
                "title": f"Mail {r['from_name'] or r['to_addr']}: {r['subject']}",
                "project": r["project"] or "Helpdesk",
                "created_at": r["created_at"],
                "summary": (r["draft_body"][:240] + ("…" if len(r["draft_body"]) > 240 else "")),
                "actions": [
                    {"label": "Verstuur", "type": "mail_send", "id": r["id"]},
                    {"label": "Bewerk", "type": "mail_edit", "id": r["id"]},
                    {"label": "Afwijzen", "type": "mail_reject", "id": r["id"], "danger": True},
                ],
            })

    # Scheduler-fouten. Staan sinds de run-historie in `scheduler_runs` ook een
    # herstart door: een gefaalde job blijft in het Actiecentrum tot hij slaagt.
    try:
        from ...scheduler import get_scheduler_status
        for job in get_scheduler_status().get("jobs", []):
            last = job.get("last_run")
            if last and last.get("status") == "error":
                items.append({
                    "kind": "error",
                    "dismiss_kind": "scheduler",
                    "id": job["id"],
                    "title": f"Geplande taak faalde: {job['label']}",
                    "project": "Scheduler",
                    "created_at": last.get("time"),
                    "summary": (last.get("error") or "")[:220],
                    "actions": [
                        {"label": "Bekijk in Technisch", "type": "open_tab", "tab": "Technisch"},
                    ],
                })
    except Exception:
        pass

    errors = [i for i in items if i["kind"] == "error"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "total": len(items),
            "needs_you": len(items) - len(errors),
            "errors": len(errors),
        },
        "items": items,
    }


def dismiss(kind: str, ref_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO inbox_dismissals (kind, ref_id, dismissed_at) "
            "VALUES (?, ?, datetime('now'))",
            (kind, ref_id),
        )


def outcome_feed(limit: int = 25) -> List[Dict[str, Any]]:
    """Recente uitkomst-kaarten: wat gedaan → waar → wat nu."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, action, detail, artifact, next_step, status, created_at "
            "FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
