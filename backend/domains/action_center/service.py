"""Actiecentrum — één inbox met alles wat op een menselijke beslissing wacht.

Antwoordt op de drie vragen die het dashboard eerder niet beantwoordde:
  1. Wat moet ik (Vincent) nú doen?          → items met needs_you=True
  2. Wat ging er mis en vereist mijn actie?  → items kind='error'
  3. Wat is er gebeurd?                      → de uitkomst-feed (activity_log)

Elk item heeft `actions`: knoppen die de frontend 1-op-1 vertaalt naar
bestaande endpoints. Het Actiecentrum voert zelf niets uit — het verzamelt.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...shared.database import get_conn

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
        for j in conn.execute(
            "SELECT j.id, j.title, j.seo_score, j.created_at, s.name AS site "
            "FROM content_jobs j LEFT JOIN sites s ON s.id = j.site_id "
            "WHERE j.status='pending_review' ORDER BY j.created_at DESC"
        ):
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

    # Scheduler-fouten (in-memory, niet in DB)
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
