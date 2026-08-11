"""
Google Search Console — per-project zoekanalyse voor het weekrapport.

Haalt voor elke geregistreerde site (sites-tabel) de zoekprestaties op uit GSC:
top-zoekwoorden, top-pagina's, dagtrend en — het belangrijkste — week-op-week
(wo-w) groei in klikken, impressies, CTR en gemiddelde positie.

Daarnaast wordt er een "kansen"-analyse gemaakt:
  * Quick wins : zoekwoorden op positie 4–15 met veel impressies -> dicht bij
    pagina 1, met content/linkwerk makkelijk naar boven te duwen.
  * CTR-gorgels : zoekwoorden met veel impressies maar lage CTR -> title/meta
    optimalisatie.
  * Stijgers/dalers: grootste wo-w verschuivingen in positie en volume.

Alles is faal-veilig: één site die geen data teruggeeft (of een API-fout) mag
het hele rapport niet laten crashen — die site wordt overgeslagen met een
korte melding.
"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from ...shared.database import get_conn
from ...domains.seo import gsc


PERIOD_DAYS = 28          # huidige periode: vandaag-29 .. vandaag-2 (GSC loopt 2d achter)
COMPARE_DAYS = 28         # vorige periode: vandaag-56 .. vandaag-30


# --------------------------------------------------------------------------
# Sites ophalen
# --------------------------------------------------------------------------
def get_sites() -> List[Dict]:
    """Alle sites uit de sites-tabel (id, name, gsc_property)."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, gsc_property FROM sites "
                "WHERE gsc_property IS NOT NULL AND gsc_property != ''"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        print(f"[GSC] sites ophalen mislukt: {e}")
        return []


# --------------------------------------------------------------------------
# Aggregatie-hulpen
# --------------------------------------------------------------------------
def _weighted_position(rows) -> float:
    tot = sum(r["impressions"] for r in rows) or 1
    return round(sum(r["position"] * r["impressions"] for r in rows) / tot, 1)


def _aggregate(rows) -> Dict:
    clicks = sum(r["clicks"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    return {
        "clicks": clicks,
        "impressions": impr,
        "ctr": round(clicks / impr * 100, 2) if impr else 0.0,
        "position": _weighted_position(rows) if rows else 0.0,
    }


def _wo_comparison(cur: Dict, prev: Dict) -> Dict:
    """Bereken absolute en relatieve groei tussen twee periodes."""
    out = {}
    for key in ("clicks", "impressions", "ctr", "position"):
        c, p = cur.get(key, 0), prev.get(key, 0)
        if key == "position":
            # lagere positie = beter; groei positief als positie daalt
            delta = round(p - c, 1)
            out[key] = {"cur": c, "prev": p, "delta": delta}
        else:
            delta = c - p
            pct = round(delta / p * 100, 1) if p else (100.0 if c else 0.0)
            out[key] = {"cur": c, "prev": p, "delta": delta, "pct": pct}
    return out


# --------------------------------------------------------------------------
# Per-site analyse
# --------------------------------------------------------------------------
def analyze_site(site: Dict, days: int = PERIOD_DAYS) -> Optional[Dict]:
    """Volledige GSC-analyse voor één site. Geen data -> None (overgeslagen)."""
    prop = site["gsc_property"]
    name = site.get("name") or prop
    try:
        cur_q = gsc.fetch_query_performance(prop, days=days, row_limit=100)
        cur_p = gsc.fetch_page_performance(prop, days=days, row_limit=100)
        cur_d = gsc.fetch_daily_performance(prop, days=days)
        prev_q = gsc.fetch_query_performance(prop, days=days, row_limit=100, end_offset=days)
        prev_p = gsc.fetch_page_performance(prop, days=days, row_limit=100, end_offset=days)
    except Exception as e:  # noqa: BLE001
        print(f"[GSC] {name}: API-fout -> overgeslagen ({type(e).__name__})")
        return None

    if not cur_q and not cur_p:
        return None  # geen enkele zoekopname -> niets om te analyseren

    agg_cur = _aggregate(cur_q)
    agg_prev = _aggregate(prev_q)
    # sommige sites hebben queries maar geen pages (of omgekeerd); vul aan
    if agg_prev["impressions"] == 0 and cur_q:
        agg_prev = _aggregate(prev_q)  # al gedaan; hier enkel defensief

    comp = _wo_comparison(agg_cur, agg_prev)

    # Top zoekwoorden op impressies (meest representatief voor zichtbaarheid)
    top_queries = sorted(cur_q, key=lambda r: r["impressions"], reverse=True)[:12]
    # Top pagina's
    top_pages = sorted(cur_p, key=lambda r: r["impressions"], reverse=True)[:10]

    # Quick wins: positie 4.0–15.0 met >= 20 impressies en nog weinig klikken
    quick_wins = [
        r for r in cur_q
        if 4.0 <= r["position"] <= 15.0 and r["impressions"] >= 20
    ]
    quick_wins = sorted(quick_wins, key=lambda r: r["impressions"], reverse=True)[:8]

    # CTR-gorgels: >= 100 impressies en CTR < 2%
    ctr_fix = [
        r for r in cur_q
        if r["impressions"] >= 100 and r["ctr"] < 2.0
    ]
    ctr_fix = sorted(ctr_fix, key=lambda r: r["impressions"], reverse=True)[:8]

    # Stijgers/dalers: match queries tussen periodes op naam
    prev_by_q = {r["query"]: r for r in prev_q}
    movers = []
    for r in cur_q:
        q = r["query"]
        if q in prev_by_q:
            p = prev_by_q[q]
            movers.append({
                "query": q,
                "pos_delta": round(p["position"] - r["position"], 1),  # + = gestegen
                "impr_delta": r["impressions"] - p["impressions"],
                "clicks_delta": r["clicks"] - p["clicks"],
                "cur_position": r["position"],
                "cur_impr": r["impressions"],
            })
    risers = sorted([m for m in movers if m["pos_delta"] > 0],
                    key=lambda m: m["pos_delta"], reverse=True)[:6]
    fallers = sorted([m for m in movers if m["pos_delta"] < 0],
                     key=lambda m: m["pos_delta"])[:6]

    return {
        # site_id draagt de analyse naar `weekly_insights` en daarmee naar Iris:
        # de projectnaam is niet stabiel genoeg om weken aan elkaar te knopen.
        "site_id": str(site.get("id") or ""),
        "name": name,
        "property": prop,
        "aggregate": agg_cur,
        "comparison": comp,
        "top_queries": top_queries,
        "top_pages": top_pages,
        "quick_wins": quick_wins,
        "ctr_fix": ctr_fix,
        "risers": risers,
        "fallers": fallers,
        "daily": cur_d,
        "has_prev": bool(prev_q),
    }


def collect_all(days: int = PERIOD_DAYS) -> List[Dict]:
    """Analyseer alle sites. Sites zonder data worden overgeslagen."""
    if not gsc.is_configured():
        print("[GSC] niet geconfigureerd — geen Search Console-data")
        return []
    results = []
    for site in get_sites():
        a = analyze_site(site, days=days)
        if a:
            results.append(a)
    print(f"[GSC] {len(results)} sites met zoekdata geanalyseerd")
    return results


# --------------------------------------------------------------------------
# Markdown-formattering (voor in de analyse-prompt én de Obsidian-note)
# --------------------------------------------------------------------------
def _fmt_pct(v: Optional[float]) -> str:
    return f"{v:+.1f}%" if v is not None else "n/b"


def format_markdown(analyses: List[Dict]) -> str:
    """Compacte, leesbare GSC-samenvatting voor alle projecten."""
    if not analyses:
        return "_Geen Search Console-data beschikbaar voor de geconfigureerde sites._"

    lines = ["# Google Search Console — per project (laatste 28 dagen vs. vorige 28 dagen)", ""]

    # Portfolio-overzichtstabel
    lines += ["## Portfolio-overzicht",
              "| Project | Klikken | Impressies | CTR | Pos | Klik Δ% | Impressie Δ% | Pos Δ |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a in analyses:
        c = a["comparison"]
        clk_pct = _fmt_pct(c["clicks"].get("pct"))
        imp_pct = _fmt_pct(c["impressions"].get("pct"))
        pos_d = c["position"]["delta"]
        lines.append(
            f"| {a['name']} | {a['aggregate']['clicks']:,} | {a['aggregate']['impressions']:,} "
            f"| {a['aggregate']['ctr']}% | {a['aggregate']['position']} "
            f"| {clk_pct} | {imp_pct} | {pos_d:+.1f} |"
        )
    lines.append("")

    # Per project
    for a in analyses:
        c = a["comparison"]
        lines.append(f"## {a['name']}")
        lines.append(
            f"- Klikken **{a['aggregate']['clicks']:,}** ({_fmt_pct(c['clicks'].get('pct'))}), "
            f"Impressies **{a['aggregate']['impressions']:,}** ({_fmt_pct(c['impressions'].get('pct'))}), "
            f"CTR **{a['aggregate']['ctr']}%**, Gem. positie **{a['aggregate']['position']}** "
            f"({c['position']['delta']:+.1f} vs. vorige periode)"
        )
        if a["has_prev"] and c["clicks"]["delta"] == 0 and c["impressions"]["delta"] == 0:
            lines.append("- _Geen vergelijkingsdata uit vorige periode (site nieuw of geen historie)._")

        if a["top_queries"]:
            lines.append("")
            lines.append("**Top zoekwoorden (op impressies):**")
            lines.append("| Zoekwoord | Klikken | Impressies | CTR | Pos |")
            lines.append("|---|---:|---:|---:|---:|")
            for r in a["top_queries"][:10]:
                lines.append(f"| {r['query']} | {r['clicks']:,} | {r['impressions']:,} "
                             f"| {r['ctr']}% | {r['position']} |")

        if a["quick_wins"]:
            lines.append("")
            lines.append("**Quick wins (pos 4–15, veel impressies):**")
            for r in a["quick_wins"]:
                lines.append(f"- `{r['query']}` — pos {r['position']}, {r['impressions']:,} impr, "
                             f"{r['clicks']:,} klikken -> push naar pagina 1")

        if a["ctr_fix"]:
            lines.append("")
            lines.append("**CTR-verbeterpunten (≥100 impr, CTR <2%):**")
            for r in a["ctr_fix"]:
                lines.append(f"- `{r['query']}` — {r['impressions']:,} impr, CTR {r['ctr']}% -> "
                             f"title/meta aanscherpen")

        if a["risers"]:
            lines.append("")
            lines.append("**Sterkste stijgers in positie:** " +
                         ", ".join(f"`{m['query']}` (+{m['pos_delta']})" for m in a["risers"][:3]))
        if a["fallers"]:
            lines.append("**Grootste dalers in positie:** " +
                         ", ".join(f"`{m['query']}` ({m['pos_delta']})" for m in a["fallers"][:3]))
        lines.append("")

    return "\n".join(lines)
