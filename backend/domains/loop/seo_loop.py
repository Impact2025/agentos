"""
SEO Loop — Loop Engineering toegepast op zoekmachine-optimalisatie.

Dit is de video-case "SEO/LLM-optimalisatie" (Greg Isenberg / Ellie) vertaald
naar ImpactOS:

    Build      → een maker-agent stelt content-/metadata-verbeteringen voor voor
                 de target-pagina's, gebaseerd op de échte GSC-zwakke plekken.
    Verify     → OBJECTIEF gemeten via Google Search Console: gemiddelde positie
                 en klikken van het vorige venster vs. nu (geen LLM-beoordelaar).
    Geheugen   → een Markdown-leerbestand per site houdt vast wat wel/niet werkte,
                 zodat de volgende maandelijkse run daarop voortbouwt.
    Mens-in-de-loop → de voorstellen + het gemeten effect worden een Actiecentrum-
                 kaart (log_outcome). Geen auto-CMS-write: Vincent keurt goed.

Verschil met loop/service.py (de tekst-kwaliteitslus):
    * Die meet subjectief via een LLM-beoordelaar (score 0-100).
    * Deze meet OBJECTIEF via GSC-cijfers — precies de verifier die de video
      bedoelt ("geef de agent toegang tot de echte bronnen").

De "Verify" dat een verbetering écht werkte, gebeurt bij de VOLGENDE run:
de GSC-delta tussen vóór-goedkeuring en ná-publicatie is de harde KPI.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)


# ── Publieke helpers (voor de REST-API / dashboard) ────────────────────────

def list_seo_loop_runs(limit: int = 50) -> List[Dict[str, Any]]:
    """Alle SEO-loop-runs (nieuwste eerst), met site-naam."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, action, detail, artifact, next_step, status, created_at "
            "FROM activity_log WHERE action='seo-loop' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_site_kpi(site_id: str, window_days: int = 28) -> Dict[str, Any]:
    """Huidige objectieve KPI voor één site (voor de dashboard-kaart)."""
    return measure_kpi(site_id, window_days=window_days)


def striking_distance_opportunities(
    site_id: str, lo: float = 0.0, hi: float = 100.0, limit: int = 25
) -> List[Dict[str, Any]]:
    """De meest waardevolle SEO-targets voor de Build-stap.

    Sorteert op opportunity_score (de daarvoor bedoelde rangschikking) en
    filtert optioneel op een positie-band. Default: de beste 25 ongeacht
    positie, zodat er altijd Build-doelen zijn — de 'striking distance'
    (positie 11-30) is slechts één interessante band, niet de enige.

    Let op: in de praktijk liggen de meeste opportunities tussen positie 0 en
    20, dus een vaste 11-30-band laat vaak weinig over. Daarom is de band
    optioneel en weegt opportunity_score zwaarder.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT query, position, clicks, impressions, ctr, opportunity_score, status "
            "FROM opportunities WHERE site_id = ? AND position >= ? AND position <= ? "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (site_id, lo, hi, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def run_history(site_id: str) -> List[Dict[str, Any]]:
    """Machine-leesbare run-geschiedenis uit het leerbestand (voor de grafiek)."""
    return _load_memory(site_id)

# Map site_id -> mens-leesbare projectnaam voor de Actiecentrum-kaart.
def _site_base(site_id: str) -> str:
    """Basis-URL van een site (voor leesbare target-labels)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT base_url FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
    return (row["base_url"] if row and row["base_url"] else f"site:{site_id}").rstrip("/")


def _site_name(site_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
    return (row["name"] if row else site_id) or site_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Objectieve meting (de VERIFIER) ─────────────────────────────────────────

def _avg(rows: List[Dict[str, Any]], field: str) -> float:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return sum(vals) / len(vals) if vals else 0.0


def measure_kpi(
    site_id: str,
    page_urls: Optional[List[str]] = None,
    window_days: int = 28,
) -> Dict[str, Any]:
    """Meet de objectieve SEO-KPI uit Google Search Console.

    Retourneert het gemiddelde over het laatste venster vs. het venster
    daarvoor: positie (lager = beter) en klikken (hoger = beter). Dit is de
    stopconditie van de loop — niet een LLM-oordeel.

    page_urls=None → alle pagina's van de site (scope='site'-aggregatie).
    """
    today = datetime.now(timezone.utc).date()
    cur_end = today
    cur_start = cur_end - timedelta(days=window_days)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window_days)

    def _window(start: datetime.date, end: datetime.date) -> List[Dict[str, Any]]:
        q = (
            "SELECT page_url, clicks, impressions, ctr, position "
            "FROM gsc_history WHERE site_id = ? AND scope = 'page' "
            "AND date >= ? AND date <= ?"
        )
        params: List[Any] = [site_id, start.isoformat(), end.isoformat()]
        if page_urls:
            q += " AND page_url IN ({})".format(
                ",".join("?" for _ in page_urls)
            )
            params.extend(page_urls)
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    cur = _window(cur_start, cur_end)
    prev = _window(prev_start, prev_end)

    cur_pos = _avg(cur, "position")
    prev_pos = _avg(prev, "position")
    cur_clicks = sum(r.get("clicks", 0) or 0 for r in cur)
    prev_clicks = sum(r.get("clicks", 0) or 0 for r in prev)

    # Positie: daling = verbetering (negatieve delta is goed).
    pos_delta = round(cur_pos - prev_pos, 2)      # <0 = beter
    click_delta = cur_clicks - prev_clicks
    click_pct = round(100.0 * click_delta / prev_clicks, 1) if prev_clicks else 0.0

    # Samenvattende KPI-score: klikstijging weegt zwaarder dan positie-daling.
    # Positieve score = verbetering.
    kpi_score = round(click_pct * 0.7 + (-pos_delta) * 1.5, 1)

    return {
        "site_id": site_id,
        "window_days": window_days,
        "cur_period": {"start": cur_start.isoformat(), "end": cur_end.isoformat()},
        "prev_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "avg_position_cur": round(cur_pos, 2),
        "avg_position_prev": round(prev_pos, 2),
        "position_delta": pos_delta,
        "clicks_cur": cur_clicks,
        "clicks_prev": prev_clicks,
        "click_delta": click_delta,
        "click_pct": click_pct,
        "kpi_score": kpi_score,
        "pages_measured": len({r["page_url"] for r in cur if r.get("page_url")}),
    }


# ── Leerbestand (GEHEUGEN) ─────────────────────────────────────────────────

def _learn_file_path(site_id: str) -> str:
    # Zet het leerbestand naast de site-data; val terug op een centrale map.
    base = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "workspaces")
    os.makedirs(base, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", site_id)
    return os.path.join(base, f"seo_loop_memory_{safe}.md")


def _load_memory(site_id: str) -> List[Dict[str, Any]]:
    path = _learn_file_path(site_id)
    if not os.path.exists(path):
        return []
    try:
        text = open(path, encoding="utf-8").read()
        block = re.search(r"<!-- RUNS\n(.*?)\nRUNS -->", text, re.DOTALL)
        if block:
            return json.loads(block.group(1))
    except (json.JSONDecodeError, OSError):
        logger.warning("Kon leerbestand %s niet parsen", path)
    return []


def _append_memory(site_id: str, entry: Dict[str, Any]) -> None:
    """Voeg een run toe aan het leerbestand (Markdown + machine-leesbare block).

    De proposal-tekst (indien aanwezig) komt in een apart
    `<!-- PROPOSALS:<run_id>` blok, zodat het RUNS-JSON blok geldig JSON blijft
    en de mens de gegenereerde voorstellen direct kan lezen/reviewen.
    """
    path = _learn_file_path(site_id)
    runs = _load_memory(site_id)
    runs.append(entry)
    runs = runs[-12:]  # houd de laatste 12 runs

    lines = [
        f"# SEO Loop-geheugen — {_site_name(site_id)} (`{site_id}`)",
        "",
        "Automatisch bijgehouden door de SEO-loop (Loop Engineering). Wat werkte, "
        "wat niet — zodat elke maandelijkse run daarop voortbouwt.",
        "",
        "<!-- RUNS",
        json.dumps(runs, ensure_ascii=False, indent=2),
        "RUNS -->",
        "",
        "## Recente runs",
        "",
    ]
    for r in reversed(runs):
        lines.append(
            f"- **{r.get('ran_at', '?')[:10]}** KPI={r.get('kpi_score')} "
            f"(pos {r.get('avg_position_prev')}→{r.get('avg_position_cur')}, "
            f"clicks {r.get('clicks_prev')}→{r.get('clicks_cur')}) — "
            f"{r.get('note', '')}"
        )
    # Proposal-teksten achteraan, per run-id, in eigen blok.
    for r in reversed(runs):
        prop = r.get("proposals")
        if prop:
            rid = r.get("run_id", "run")
            lines += [
                "",
                f"### Voorstellen ({rid}) — {r.get('ran_at', '?')[:10]}",
                "",
                prop,
            ]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ── Build: maker-agent stelt verbeteringen voor ────────────────────────────

_DEFAULT_MAKER = (
    "Je bent een senior SEO-specialist in een maandelijkse verbeterloop. "
    "Op basis van de Google Search Console-zwakke plekken stel je concrete, "
    "uitvoerbare verbeteringen voor aan titel, meta-omschrijving, koppen en "
    "inhoud van de genoemde pagina's. Geen vage adviezen — per pagina 3-5 "
    "specifieke wijzigingen in Markdown. Schrijf in het Nederlands."
)


async def _build_proposals(
    site_id: str,
    page_urls: List[str],
    kpi: Dict[str, Any],
    prior_notes: List[str],
) -> str:
    """Laat de maker verbeter-voorstellen genereren (Build-stap)."""
    pages_block = "\n".join(f"- {u}" for u in page_urls) or "(alle pagina's)"
    prior_block = "\n".join(f"- {n}" for n in prior_notes) or "(geen eerdere runs)"
    user = (
        f"# Site: {_site_name(site_id)}\n\n"
        f"# Huidige GSC-KPI (venster van {kpi['window_days']} dagen)\n"
        f"- Gem. positie: {kpi['avg_position_prev']} → {kpi['avg_position_cur']} "
        f"(delta {kpi['position_delta']})\n"
        f"- Klikken: {kpi['clicks_prev']} → {kpi['clicks_cur']} "
        f"({kpi['click_pct']}%)\n"
        f"- KPI-score: {kpi['kpi_score']}\n\n"
        f"# Target-pagina's\n{pages_block}\n\n"
        f"# Wat eerdere runs leerden\n{prior_block}\n\n"
        "Stel per pagina concrete verbeteringen voor (titel, meta, koppen, "
        "inhoud) gericht op de zwakste plekken. Lever alleen Markdown."
    )
    chunks: List[str] = []
    async for ev in agent_service.run_agent(
        messages=[{"role": "user", "content": user}],
        system_prompt=_DEFAULT_MAKER,
        agent="hermes",
        use_tools=False,
        purpose="seo-loop",
    ):
        if ev.get("type") == "text":
            chunks.append(ev["text"])
    return "".join(chunks).strip() or "_(geen voorstel gegenereerd)_"


# ── Orchestratie ───────────────────────────────────────────────────────────

async def run_seo_loop(
    site_id: str,
    page_urls: Optional[List[str]] = None,
    *,
    dry_run: bool = False,
    threshold_kpi: float = 0.0,
    window_days: int = 28,
    focus_striking_distance: bool = True,
) -> Dict[str, Any]:
    """Eén SEO-loop-ronde (Build → Verify → Geheugen → Mens-in-de-loop).

    focus_striking_distance=True (default): de Build-stap richt zich op de
    pagina's/met queries met de hoogste hefboom (positie 11-30 uit de
    opportunities-tabel) — niet op alle 122 pagina's tegelijk.

    dry_run=True: meet + (optioneel) bouw overslaan + schrijf leerbestand +
    log Actiecentrum-kaart, ZONDER LLM-aanroep en ZONDER productie-writes.
    In dry-run wordt géén maker aangeroepen en wordt de KPI genoteerd als
    basismeting voor de eerstvolgende échte run.

    Retourneert een samenvatting die ook naar log_outcome gaat.
    """
    kpi = measure_kpi(site_id, page_urls, window_days=window_days)
    prior_runs = _load_memory(site_id)
    prior_notes = [
        f"{r.get('ran_at','?')[:10]}: KPI {r.get('kpi_score')} — {r.get('note','')}"
        for r in prior_runs[-5:]
    ]

    # Build-doelen: de waardevolste opportunities (op opportunity_score), of
    # expliciete page_urls. Leesbare labels voor de maker, geen geforceerde URL.
    targets = page_urls
    if not targets and focus_striking_distance:
        targets = [
            f"{o['query']} (positie {round(o['position'], 1)}, {o['clicks'] or 0} klikken)"
            for o in striking_distance_opportunities(site_id, limit=15)
        ]

    proposals = ""
    if not dry_run:
        proposals = await _build_proposals(
            site_id, targets or ["(alle pagina's)"], kpi, prior_notes
        )

    # Geheugen: sla deze meting + (als die er is) het voorstel op.
    import uuid as _uuid
    run_id = "run-" + _uuid.uuid4().hex[:8]
    entry = {
        "run_id": run_id,
        "ran_at": _now(),
        "mode": "dry_run" if dry_run else "live",
        "kpi_score": kpi["kpi_score"],
        "avg_position_prev": kpi["avg_position_prev"],
        "avg_position_cur": kpi["avg_position_cur"],
        "clicks_prev": kpi["clicks_prev"],
        "clicks_cur": kpi["clicks_cur"],
        "note": ("basismeting (dry-run)" if dry_run
                 else "voorstellen klaargezet ter goedkeuring"),
        "proposals_len": len(proposals),
    }
    # Alleen de echte voorstel-tekst meenemen in een live-run (droog is leeg).
    if proposals:
        entry["proposals"] = proposals
    _append_memory(site_id, entry)

    passed = kpi["kpi_score"] >= threshold_kpi
    artifact = _learn_file_path(site_id)
    detail = (
        f"SEO-loop {site_id}: KPI {kpi['kpi_score']} "
        f"(positie {kpi['avg_position_prev']}→{kpi['avg_position_cur']}, "
        f"klikken {kpi['clicks_prev']}→{kpi['clicks_cur']}, "
        f"{kpi['click_pct']}%). "
        + ("Basismeting (dry-run)." if dry_run
           else f"{len(proposals)} tekens aan verbeter-voorstellen klaar.")
    )
    next_step = (
        "Geen actie (dry-run basismeting)."
        if dry_run else
        "Review de verbeter-voorstellen in het leerbestand en keur publicatie goed."
    )
    log_outcome(
        _site_name(site_id),
        "seo-loop",
        detail,
        artifact=artifact,
        next_step=next_step,
        status="ok",
    )

    logger.info("SEO-loop %s (%s): kpi=%s passed=%s",
                site_id, "dry" if dry_run else "live", kpi["kpi_score"], passed)
    return {
        "site_id": site_id,
        "dry_run": dry_run,
        "kpi": kpi,
        "passed": passed,
        "proposals_len": len(proposals),
        "memory_file": artifact,
    }
