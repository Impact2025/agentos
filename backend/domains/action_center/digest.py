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
        lines.append(f"## Ging mis ({len(errors)} open)")
        for e in errors[:5]:
            lines.append(f"- **{e['title']}** — {e['summary'][:140]}")
        for f in failed[:3]:
            if not any(f["detail"][:60] in e["summary"] for e in errors):
                lines.append(f"- {f['project']}: {f['detail'][:140]}")
        lines.append("")

    # ── 2. Wat wacht op jou ──
    if waiting:
        lines.append(f"## Wacht op jou ({len(waiting)})")
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
            "linkbuilding_review": "link-outreach-concept(en) wachten op je verzendklik",
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
    lines.append(f"## Gisteren opgeleverd ({len(delivered)})")
    if delivered:
        for d in delivered[:10]:
            entry = f"- {d['project']}: {d['detail'][:120]}"
            if d["artifact"]:
                entry += f" → {d['artifact']}"
            lines.append(entry)
    else:
        lines.append("- (geen opgeleverde resultaten in de afgelopen 24 uur)")
    lines.append("")

    # ── 3a. Stilstand: wat er niet gebeurde ──
    # Hoort direct ná "gisteren opgeleverd", want het is de tegenhanger: dit is
    # het werk dat níet is opgeleverd omdat de machine uit stond. Zonder dit
    # blok leest een lege oplever-lijst als een rustige dag, terwijl er vier
    # geplande taken zijn overgeslagen (28-31 jul 2026).
    try:
        from ...shared import downtime
        gaps = [g for g in downtime.summary() if g["recoverable"]]
        if gaps:
            lines.append(f"## Niet gedraaid ({len(gaps)} taak/taken)")
            for g in gaps[:6]:
                lines.append(f"- {g['detail']}")
            lines.append("")
            lines.append("  _Deze taken zijn in te halen via het Actiecentrum "
                         "('Nu alsnog draaien') — ze gebeuren niet vanzelf alsnog._")
            lines.append("")
    except Exception:
        logger.exception("[digest] Kon stilstand-sectie niet bouwen")

    # ── 3b. De formule: input → output, gemeten ──
    # Sales als conversieformule — je stuurt op de input (verstuurde outreach,
    # gepubliceerde content) en dit blok laat zien of de cijfers al werken.
    try:
        from ...domains.prospecting import funnel as funnel_mod
        f = funnel_mod.funnel_stats()
        inp = funnel_mod.input_stats(days=7)
        lines.append("## De formule (laatste 7 dagen)")
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
                f"- Onder target: keur wachtende concepten goed of draai een extra batch — "
                "de output volgt de input."
            )
        # Outreach-leerlus: toon de sterkste gemeten stijl-les zodra die er is.
        from ...shared.learning import top_lesson
        best = top_lesson("outreach")
        if best:
            lines.append(
                f"- Geleerd: {best['lesson']} "
                f"(vertrouwen {round(best['confidence'] * 100)}%, "
                f"{best['times_confirmed']}× bevestigd)"
            )
        lines.append("")
    except Exception:
        logger.exception("Formule-sectie in ochtendrapport mislukt")

    # ── 3b2. Linkbuilding-formule: mails → links live, geverifieerd ──
    try:
        from ...domains.linkbuilding import service as lb_service
        lb = lb_service.funnel_stats()
        r = lb["reached"]
        review = (lb["by_status"] or {}).get("outreach_review") or 0
        # Alleen tonen zodra de funnel leeft — een rapport vol nullen is ruis.
        if lb["total_prospects"]:
            lines.append("## Linkbuilding")
            lines.append(
                f"- Funnel: {r['contacted']} benaderd → {r['replied']} gereageerd → "
                f"{r['link_live']} link(s) live ({r['verified']} geverifieerd, "
                f"{lb['dofollow_live']} dofollow)"
            )
            if review:
                lines.append(f"- {review} concept(en) wachten op je verzendklik in het Actiecentrum")
            if lb["formula"]:
                lines.append(f"- **Formule: {lb['formula']}**")
            lines.append("")
    except Exception:
        logger.exception("Linkbuilding-sectie in ochtendrapport mislukt")

    # ── 3b3. Beursmeester: de portefeuille náást de index ──
    # Het rendement staat hier nooit alleen. Zonder de benchmark ernaast is
    # "+4%" geen prestatie maar een getal — en precies dat maakte het oude
    # finance-dagrapport tot een advies dat nooit werd afgerekend.
    try:
        from ..invest import portfolio as invest_portfolio
        from ..invest import service as invest_service
        pf = invest_portfolio.get()
        if pf:
            r = invest_portfolio.rendement()
            open_v = invest_service.open_voorstellen()
            lines.append("## Beursmeester")
            if r["rendement_pct"] is None:
                lines.append(f"- Rendement nog niet te bepalen — {r['onvolledig_reden']}")
            elif r["benchmark_pct"] is None:
                lines.append(f"- Portefeuille {r['rendement_pct']:+.2f}% sinds {r['sinds']} "
                             "(geen benchmark om tegen af te zetten)")
            else:
                oordeel = "vóór" if (r["alpha_pct"] or 0) >= 0 else "achter op"
                lines.append(
                    f"- Portefeuille {r['rendement_pct']:+.2f}% vs. {r['benchmark_symbol']} "
                    f"{r['benchmark_pct']:+.2f}% → **{abs(r['alpha_pct']):.2f}%-punt {oordeel} de index**"
                )
            trefkans = invest_service.track_record(invest_service.AGENT)
            if trefkans["accuracy"] is not None:
                lines.append(f"- Trefkans: {trefkans['accuracy']}% over "
                             f"{trefkans['correct'] + trefkans['wrong']} afgerekende voorspellingen")
            if open_v:
                lines.append(f"- {len(open_v)} beleggingsvoorstel(len) wachten op je beoordeling")
            lines.append("")
    except Exception:
        logger.exception("Beursmeester-sectie in ochtendrapport mislukt")

    # ── 3c. Iris' advies van vandaag (de 06:45-manageranalyse) ──
    #
    # Dat de briefing er ís, wordt afgedwongen in `run_daily_digest` — hier
    # lezen we alleen. `build_digest` is synchroon en wordt óók door de
    # on-demand-endpoint aangeroepen; die mag geen LLM-run aanzwengelen.
    try:
        from ..iris import service as iris_service
        iris_report = iris_service.latest_report()
        if iris_report and iris_report["report_date"] == today.strftime("%Y-%m-%d"):
            advice = iris_report.get("advice") or []
            if advice:
                lines.append("## Iris' advies voor vandaag")
                for a in sorted(advice, key=lambda x: x.get("prio", 9))[:3]:
                    lines.append(f"- **{a.get('actie', '')}** — {a.get('waarom', '')}")
                lines.append("")
    except Exception:
        logger.exception("Iris-sectie in ochtendrapport mislukt")

    # ── 4. Vandaag gepland ──
    try:
        from ...scheduler import get_scheduler_status
        jobs = get_scheduler_status().get("jobs", [])
        today_jobs = [
            j for j in jobs
            if j.get("next_run") and j["next_run"][:10] == today.strftime("%Y-%m-%d")
        ]
        if today_jobs:
            lines.append("## Vandaag gepland")
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

    # Het rapport hangt aan Iris' briefing van vandáág (`build_digest` vergelijkt
    # op datum). Op 7 aug 2026 werd de laptop om 08:33 uit slaapstand gewekt en
    # speelde APScheduler de gemiste vuurmomenten in zijn eigen volgorde af: het
    # rapport om 08:33:49, de briefing pas om 08:38:49 — dus ging het rapport
    # zonder haar advies de deur uit. De chronologie stond alleen vast in de
    # inhaalslag bij een kóude start; dit pad kende die bescherming niet.
    # Daarom hier, waar de afhankelijkheid echt zit: doet niets als de briefing
    # er al is of nog niet aan de beurt was, en wacht op het dagslot als hij op
    # dit moment elders draait.
    try:
        from ...scheduler import ensure_ran_today
        await ensure_ran_today("iris_briefing")
    except Exception:
        logger.exception("Kon Iris' briefing niet garanderen — het rapport gaat "
                         "door met wat er wél is")

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
