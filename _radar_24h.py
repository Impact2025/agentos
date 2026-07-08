"""Mission Radar — 24-uur-later analyse.

Vangt de huidige signaal-set (baseline) van bewaardvoorjou + bijeen, draait een
verse scan (Tavily + LLM), en berekent de delta: wat is er in de afgelopen 24 uur
nieuw bijgekomen, welke keywords schoten omhoog, en is de top-score verschoven.
Schrijft naar _radar_24h_report.txt en print een samenvatting."""
import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(__file__))
from backend.domains.radar.service import get_service, _now
from datetime import datetime, timezone

PROJECTS = ["bewaardvoorjou", "bijeen"]


def snapshot(svc, proj):
    sigs = svc.list_signals(project=proj, limit=2000)
    return {
        "count": len(sigs),
        "urls": {s["url"] for s in sigs},
        "top": sorted(sigs, key=lambda s: (s.get("signal_score") or 0), reverse=True)[:5],
        "by_keyword": {},
    }


async def scan_project(svc, proj):
    added = 0
    async for ev in svc.run_scan(project=proj):
        if ev.get("type") == "scan_done":
            added = ev.get("total_saved", 0)
    return added


if __name__ == "__main__":
    svc = get_service()
    before = {p: snapshot(svc, p) for p in PROJECTS}

    async def run_all():
        out = {}
        for p in PROJECTS:
            out[p] = await scan_project(svc, p)
        return out

    added = asyncio.run(run_all())

    after = {p: snapshot(svc, p) for p in PROJECTS}

    lines = ["# Mission Radar — 24-uur-later analyse", "",
             f"Datum: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", ""]
    for p in PROJECTS:
        b, a = before[p], after[p]
        new_urls = a["urls"] - b["urls"]
        new_sigs = [s for s in svc.list_signals(project=p, limit=2000) if s["url"] in new_urls]
        new_sigs.sort(key=lambda s: (s.get("signal_score") or 0), reverse=True)
        b_top = b["top"][0]["signal_score"] if b["top"] else 0
        a_top = a["top"][0]["signal_score"] if a["top"] else 0
        lines += [
            f"## {p}",
            f"- Signalen: {b['count']} → {a['count']}  (+{a['count'] - b['count']})",
            f"- Nieuw in afgelopen 24u: {len(new_sigs)} (scan saved={added[p]})",
            f"- Top-score: {b_top} → {a_top}  ({'+' if a_top >= b_top else ''}{round(a_top - b_top,1)})",
            "",
            "  Top-5 vóór scan:",
        ]
        for s in b["top"]:
            lines.append(f"    • {s['signal_score']}  {s['title'][:70]}  [{s['keyword']}]")
        lines += ["", "  Top-5 ná scan (incl. nieuw):"]
        for s in a["top"]:
            tag = "  ⭐NIEUW" if s["url"] in new_urls else ""
            lines.append(f"    • {s['signal_score']}  {s['title'][:70]}  [{s['keyword']}]{tag}")
        if new_sigs:
            lines += ["", "  Nieuwe signalen (hoogste eerst):"]
            for s in new_sigs[:10]:
                lines.append(f"    + {s['signal_score']}  {s['title'][:70]}  [{s['keyword']}]  {s['url'][:60]}")
        lines.append("")

    report = "\n".join(lines)
    with open("_radar_24h_report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("\n[rapport opgeslagen in _radar_24h_report.txt]")
