"""
Facebook Trends & Benchmark — wereldklasse-analytics op de opgeslagen snapshots.

De dagelijkse `facebook_snapshot`-job schrijft voortaan OOK naar `fb_history`
(append-only, één rij per site per dag). Daardoor kunnen we hier echte
tijdreeksen bouwen — fan-groei over tijd, engagement-trend, posting-cadans —
zonder ooit extra Meta-calls te doen. UI en Iris lezen uit deze laag, net als
uit de snapshot-store: instant, rate-limit-vrij, offline-bestendig.

Verder: cross-project benchmark (LiefdeVoorIedereen vs BewaardVoorJou) zodat
je in één oogopslag ziet welk project harder groeit / beter convert-eert.

Faalveilig (zoals de rest van analytics): met <2 historiepunten geeft trend
'expliciet_te_kort' i.p.v. een stille leugen.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from ...shared.database import get_conn

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Historische tabel (append-only) — geschreven door snapshot_all_facebook()
# ─────────────────────────────────────────────────────────────────────────────

def ensure_history_schema() -> None:
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fb_history (
                site_name    TEXT NOT NULL,
                date         TEXT NOT NULL,             -- YYYY-MM-DD (UTC-dag)
                fan_count    INTEGER,
                followers_count INTEGER,
                posts_analysed INTEGER,
                total_engagement INTEGER,
                avg_engagement_per_post REAL,
                best_posting_day TEXT,
                best_posting_hour INTEGER,
                PRIMARY KEY (site_name, date)
            )"""
        )


def append_history(site_name: str, metrics: Dict[str, Any]) -> None:
    """Sla één dagpunt op. Upsert op (site_name, date) — een dag wordt nooit
    verdubbeld als de snapshot twee keer op één dag draait."""
    ensure_history_schema()
    date = (metrics.get("captured_at") or datetime.now(timezone.utc).isoformat())[:10]
    a = metrics.get("snapshot") or {}
    page = a.get("page") or {}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO fb_history
               (site_name, date, fan_count, followers_count, posts_analysed,
                total_engagement, avg_engagement_per_post, best_posting_day,
                best_posting_hour)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(site_name, date) DO UPDATE SET
                 fan_count=excluded.fan_count,
                 followers_count=excluded.followers_count,
                 posts_analysed=excluded.posts_analysed,
                 total_engagement=excluded.total_engagement,
                 avg_engagement_per_post=excluded.avg_engagement_per_post,
                 best_posting_day=excluded.best_posting_day,
                 best_posting_hour=excluded.best_posting_hour""",
            (site_name, date,
             page.get("fan_count"),
             page.get("followers_count"),
             a.get("posts_analysed"),
             a.get("total_engagement"),
             a.get("avg_engagement_per_post"),
             a.get("best_posting_day"),
             a.get("best_posting_hour")),
        )


def get_history(site_name: str, limit_days: int = 90) -> List[Dict[str, Any]]:
    """Tijdreeks, oud→nieuw, voor één site."""
    ensure_history_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT site_name, date, fan_count, followers_count, posts_analysed, "
            "total_engagement, avg_engagement_per_post, best_posting_day, best_posting_hour "
            "FROM fb_history WHERE site_name = ? ORDER BY date DESC LIMIT ?",
            (site_name, limit_days),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ─────────────────────────────────────────────────────────────────────────────
# Trend-berekening (geen Meta-calls — puur uit fb_history)
# ─────────────────────────────────────────────────────────────────────────────

def compute_trend(site_name: str, limit_days: int = 90) -> Dict[str, Any]:
    """Bouw een trend-rapport uit de historische punten.

    Geeft: eerste/laatste fans + delta + dag-velocity, engagement-trend
    (gemiddelde per post eerste vs laatste week), en de within-snapshot
    momentum van de meest recente snapshot als 'verse' indicator.
    """
    hist = get_history(site_name, limit_days)
    if len(hist) < 2:
        return {
            "success": True, "site_name": site_name,
            "points": len(hist),
            "trend": "expliciet_te_kort",
            "note": "Nog onvoldoende historie (<2 dagen) om een trend te tonen. "
                    "Dit vult zich automatisch aan vanaf de 2e dagelijkse snapshot.",
        }

    first, last = hist[0], hist[-1]
    fans_first = first.get("fan_count")
    fans_last = last.get("fan_count")
    fan_delta = None
    fan_velocity_daily = None
    if fans_first is not None and fans_last is not None:
        fan_delta = fans_last - fans_first
        days_span = max(1, (datetime.strptime(last["date"], "%Y-%m-%d")
                            - datetime.strptime(first["date"], "%Y-%m-%d")).days)
        fan_velocity_daily = round(fan_delta / days_span, 2)

    # Engagement per post: eerste week vs laatste week (buiten de allernieuwste
    # dag, die soms nog niet compleet is).
    half = max(1, len(hist) // 2)
    early = hist[:half]
    recent = hist[half:]
    early_eng = sum(r.get("total_engagement") or 0 for r in early)
    early_posts = sum(r.get("posts_analysed") or 0 for r in early) or 1
    recent_eng = sum(r.get("total_engagement") or 0 for r in recent)
    recent_posts = sum(r.get("posts_analysed") or 0 for r in recent) or 1
    early_avg = round(early_eng / early_posts, 1)
    recent_avg = round(recent_eng / recent_posts, 1)
    eng_delta_pct = round((recent_avg - early_avg) / early_avg * 100, 1) if early_avg else None

    return {
        "success": True,
        "site_name": site_name,
        "points": len(hist),
        "span_days": (datetime.strptime(last["date"], "%Y-%m-%d")
                      - datetime.strptime(first["date"], "%Y-%m-%d")).days,
        "first_date": first["date"],
        "last_date": last["date"],
        "fans_first": fans_first,
        "fans_last": fans_last,
        "fan_delta": fan_delta,
        "fan_velocity_daily": fan_velocity_daily,
        "engagement_avg_early": early_avg,
        "engagement_avg_recent": recent_avg,
        "engagement_delta_pct": eng_delta_pct,
        "best_posting_day": last.get("best_posting_day"),
        "best_posting_hour": last.get("best_posting_hour"),
        "trend": ("stijgend" if (fan_delta or 0) >= 0 and (eng_delta_pct or 0) >= 0
                  else "dalend" if (fan_delta or 0) < 0 or (eng_delta_pct or 0) < 0
                  else "stabiel"),
        "series": hist,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cross-project benchmark
# ─────────────────────────────────────────────────────────────────────────────

def benchmark_projects() -> Dict[str, Any]:
    """Vergelijk alle FB-sites in één tabel: wie groeit harder, wie
    convert-eert beter (engagement per fan)."""
    from .facebook_store import get_all_snapshots
    snaps = get_all_snapshots()
    rows = []
    for s in snaps:
        a = s.get("snapshot") or {}
        page = a.get("page") or {}
        fans = page.get("fan_count")
        total_eng = a.get("total_engagement")
        posts = a.get("posts_analysed")
        # Engagement per 1000 fans — eerlijke verhouding tussen kleine en
        # grote pagina's.
        eng_per_1k = round(total_eng / fans * 1000, 1) if (fans and fans > 0) else None
        t = compute_trend(s["site_name"])
        rows.append({
            "site_name": s["site_name"],
            "page_name": page.get("name"),
            "status": s.get("status"),
            "fans": fans,
            "posts_28d": posts,
            "total_engagement_28d": total_eng,
            "engagement_per_1k_fans": eng_per_1k,
            "fan_velocity_daily": t.get("fan_velocity_daily"),
            "fan_delta": t.get("fan_delta"),
            "trend": t.get("trend"),
            "best_posting_day": a.get("best_posting_day"),
            "best_posting_hour": a.get("best_posting_hour"),
            "window_momentum": a.get("window_momentum"),
        })
    # Sorteer op engagement-per-1000-fans (desc) — de meest 'actieve' community.
    rows.sort(key=lambda r: (r.get("engagement_per_1k_fans") or -1), reverse=True)
    return {"success": True, "count": len(rows), "projects": rows}


# ─────────────────────────────────────────────────────────────────────────────
# Leesbare output voor Iris
# ─────────────────────────────────────────────────────────────────────────────

def trends_prompt_block() -> str:
    """Trend + benchmark-samenvatting voor Iris' prompt. Altijd een zin."""
    b = benchmark_projects()
    if not b.get("projects"):
        return ("Facebook-trends: nog geen enkele historie. De dagelijkse "
                "snapshot vult dit automatisch aan vanaf dag 2.")
    lines = ["Facebook-trends & benchmark (uit dagelijkse historie):"]
    for p in b["projects"]:
        if p.get("status") != "ok":
            lines.append(f"- {p['site_name']}: niet leesbaar, geen trend.")
            continue
        vel = p.get("fan_velocity_daily")
        fdelta = p.get("fan_delta")
        edelta = p.get("engagement_delta_pct")
        trend = p.get("trend")
        bm = (f"fan-Δ {fdelta} ({vel}/dag), engagement-Δ {edelta}%, "
              f"eng/1k fans {p.get('engagement_per_1k_fans')}") if vel is not None else "trend n/b"
        lines.append(
            f"- {p['site_name']} ({p['fans']} fans): {trend} — {bm}. "
            f"Beste dag: {p.get('best_posting_day') or 'n/b'}, "
            f"uur: {p.get('best_posting_hour') if p.get('best_posting_hour') is not None else 'n/b'}."
        )
    return "\n".join(lines)
