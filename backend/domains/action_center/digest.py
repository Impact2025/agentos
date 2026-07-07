"""Ochtendrapport — dagelijkse digest van het Actiecentrum.

Eén samenvatting die de drie vragen beantwoordt zonder dat je het dashboard
hoeft te openen: wat ging er mis, wat wacht op jou, wat hebben de agents
gisteren opgeleverd, en wat staat er vandaag gepland.

Wordt elke ochtend door de scheduler gedraaid: gelogd als uitkomst-kaart en
per mail verstuurd zodra SMTP is geconfigureerd (zie .env: SMTP_HOST etc.).
"""
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytz

from ...shared.database import get_conn
from . import service as ac_service

logger = logging.getLogger(__name__)

_TZ = pytz.timezone("Europe/Amsterdam")

# Acties in de uitkomst-feed die "er is echt iets opgeleverd" betekenen —
# start/stop-geluiden (goal_start, phase_done, task_start) blijven eruit.
_DELIVERY_ACTIONS = {
    "task_done", "goal_done", "live", "publicatie", "wachtrij_staged",
    "auto-content-klaar", "vacature-scan", "sky-scan", "strategist_goal",
}


def _yesterday_outcomes() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT project, action, detail, artifact, next_step, status, created_at "
            "FROM activity_log WHERE created_at > datetime('now', '-1 day') "
            "ORDER BY created_at DESC LIMIT 100",
        ).fetchall()
    return [dict(r) for r in rows]


def build_digest() -> Dict[str, Any]:
    """Bouw het ochtendrapport: markdown + de onderliggende data."""
    inbox = ac_service.build_inbox()
    items = inbox["items"]
    errors = [i for i in items if i["kind"] == "error"]
    waiting = [i for i in items if i["kind"] != "error"]

    outcomes = _yesterday_outcomes()
    delivered = [o for o in outcomes if o["action"] in _DELIVERY_ACTIONS and o["status"] == "ok"]
    failed = [o for o in outcomes if o["status"] == "error"]

    _DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    _MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni",
                "juli", "augustus", "september", "oktober", "november", "december"]
    today = datetime.now(_TZ)
    datum = f"{_DAGEN[today.weekday()]} {today.day} {_MAANDEN[today.month - 1]} {today.year}"
    lines = [f"# Ochtendrapport — {datum}", ""]

    # ── 1. Fouten eerst: die kosten je geld/kansen als ze blijven liggen ──
    if errors or failed:
        lines.append(f"## ⚠ Ging mis ({len(errors)} open)")
        for e in errors[:5]:
            lines.append(f"- **{e['title']}** — {e['summary'][:140]}")
        for f in failed[:3]:
            if not any(f["detail"][:60] in e["summary"] for e in errors):
                lines.append(f"- {f['project']}: {f['detail'][:140]}")
        lines.append("")

    # ── 2. Wat wacht op jou ──
    if waiting:
        lines.append(f"## ✋ Wacht op jou ({len(waiting)})")
        by_kind: Dict[str, int] = {}
        for w in waiting:
            by_kind[w["kind"]] = by_kind.get(w["kind"], 0) + 1
        kind_labels = {
            "goal_draft": "doel(en) wachten op je akkoord",
            "goal_ready": "doel(en) staan klaar om te starten",
            "goal_failed": "doel(en) zijn vastgelopen",
            "content_review": "artikel(en) wachten op review in de Wachtrij",
            "task_approval": "taak/taken wachten op goedkeuring",
            "vacancies": "vacature-kansen met hoge fit",
            "leads": "nieuwe leads voor eerste contact",
            "outreach_review": "outreach-concept(en) wachten op je verzendklik",
        }
        for kind, n in sorted(by_kind.items(), key=lambda x: -x[1]):
            lines.append(f"- {n} {kind_labels.get(kind, kind)}")
        lines.append("")
        # De 3 belangrijkste concreet benoemen
        top = [w for w in waiting if w["kind"] in ("content_review", "goal_failed", "goal_draft")][:3]
        for t in top:
            lines.append(f"  - _{t['project']}_: {t['title'][:90]}")
        if top:
            lines.append("")

    # ── 3. Wat de agents gisteren opleverden ──
    lines.append(f"## ✓ Gisteren opgeleverd ({len(delivered)})")
    if delivered:
        for d in delivered[:10]:
            entry = f"- {d['project']}: {d['detail'][:120]}"
            if d["artifact"]:
                entry += f" → {d['artifact']}"
            lines.append(entry)
    else:
        lines.append("- (geen opgeleverde resultaten in de afgelopen 24 uur)")
    lines.append("")

    # ── 3b. De formule: input → output, gemeten ──
    # Sales als conversieformule — je stuurt op de input (verstuurde outreach,
    # gepubliceerde content) en dit blok laat zien of de cijfers al werken.
    try:
        from ...domains.prospecting import funnel as funnel_mod
        f = funnel_mod.funnel_stats()
        inp = funnel_mod.input_stats(days=7)
        lines.append("## 📈 De formule (laatste 7 dagen)")
        lines.append(
            f"- Input: {inp['outreach_sent']}/{inp['outreach_target']} outreach verstuurd · "
            f"{inp['outreach_drafts_ready']} concept(en) wachten op je verzendklik · "
            f"{inp['content_live']} artikel(en) live"
        )
        r = f["reached"]
        lines.append(
            f"- Funnel totaal: {r['contacted']} benaderd → {r['replied']} gereageerd → "
            f"{r['call']} gesprek → {r['won']} klant"
        )
        if f["formula"]:
            lines.append(f"- **Formule: {f['formula']}**")
        if inp["outreach_sent"] < inp["outreach_target"]:
            lines.append(
                f"- ⚡ Onder target: keur wachtende concepten goed of draai een extra batch — "
                "de output volgt de input."
            )
        lines.append("")
    except Exception:
        logger.exception("Formule-sectie in ochtendrapport mislukt")

    # ── 4. Vandaag gepland ──
    try:
        from ...scheduler import get_scheduler_status
        jobs = get_scheduler_status().get("jobs", [])
        today_jobs = [
            j for j in jobs
            if j.get("next_run") and j["next_run"][:10] == today.strftime("%Y-%m-%d")
        ]
        if today_jobs:
            lines.append("## 📅 Vandaag gepland")
            for j in sorted(today_jobs, key=lambda x: x["next_run"]):
                lines.append(f"- {j['next_run'][11:16]} — {j['label']}")
            lines.append("")
    except Exception:
        pass

    lines.append("_Open het Actiecentrum op http://localhost:1250 om items af te handelen._")

    return {
        "date": today.strftime("%Y-%m-%d"),
        "markdown": "\n".join(lines),
        "counts": {
            "errors": len(errors),
            "waiting": len(waiting),
            "delivered": len(delivered),
        },
    }


async def run_daily_digest() -> None:
    """Scheduler entry-point: bouw het rapport, log het, mail het indien mogelijk."""
    from ...shared.outcomes import log_outcome
    digest = build_digest()
    c = digest["counts"]
    summary = (
        f"{c['errors']} fout(en), {c['waiting']} item(s) wachten op jou, "
        f"{c['delivered']} resultaat/resultaten opgeleverd"
    )

    mailed = False
    try:
        from ...shared import email_service
        if email_service.is_configured():
            mailed = email_service.send_report(
                f"Agent OS ochtendrapport {digest['date']} — {summary}",
                digest["markdown"],
            )
    except Exception as e:
        logger.warning(f"Ochtendrapport mailen mislukt: {e}")

    log_outcome(
        "Agent OS", "ochtendrapport", summary,
        next_step=(
            "" if mailed else
            "Bekijk het rapport op het dashboard (mail ontvangen? stel SMTP_HOST/USER/PASSWORD in .env in)"
        ),
    )
    logger.info("Ochtendrapport klaar: %s (gemaild: %s)", summary, mailed)
