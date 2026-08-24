"""GEO citatie-check — het échte AI-zichtbaarheid-KPI.

De video (Goldie x Float) leerde: je KPI is niet 'rank #1' maar 'wordt je merk
genoemd als bron' in ChatGPT/Perplexity/Bing. Die check kan niet uit GSC komen
— die meet alleen Google-klikken. Dus vragen we per ICP-vraag de LLM expliciet
of hij het merk noemt, en tracken we dat per week.

Deze module:
  * run_citation_check() — draait voor elke site mét geconfigureerde persona's
    de ICP-vragen tegen de LLM en parset of het merk genoemd wordt.
  * slaat per (site, query, week) een rij op in geo_citations.
  * levert een wekelijkse citatie-score (0-100) per site.

Deterministisch wat betreft opslag/parsing; de bron-vraag zelf is een LLM-call
(goedkoop: 1 vraag per ICP-query, korte verwachting).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.config import OPENMODEL_SMART_MODEL

CITATION_TABLE = "geo_citations"


def ensure_schema() -> None:
    with get_conn() as conn:
        conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {CITATION_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT NOT NULL,
            site_name TEXT,
            query TEXT NOT NULL,
            week TEXT NOT NULL,
            cited INTEGER,            -- 1=genoemd als bron, 0=nee
            raw_answer TEXT,
            model TEXT,
            checked_at TEXT DEFAULT (datetime('now'))
        )""")


def _ask_llm(question: str) -> Optional[str]:
    """Stel de vraag aan de OpenModel-gateway en retourneer de tekst.

    Gebruikt dezelfde client als de rest van Impact OS (chat/claude.py), zodat
    routing/quota-rem/gebruikslogging centraal blijven.
    """
    try:
        from ..chat import claude
        # Non-streaming vraag; deepseek-v4-flash via gateway, thinking uit.
        chunks = []
        async def _collect():
            async for t in claude.stream_response(
                messages=[{"role": "user", "content": question}],
                system_prompt=(
                    "Je bent een objectieve testassistent. Beantwoord de vraag "
                    "kort (max 3 zinnen) en noem ALLEEN concrete merken/bedrijven "
                    "die je als antwoord beschouwt. Vermeld geen merk dat je niet "
                    "expliciet als antwoord ziet."
                ),
                max_tokens=300,
                purpose="geo-citation",
            ):
                chunks.append(t)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We zitten al in een event loop (FastAPI-handler): run via
                # loop.create_task is hier niet bruikbaar zonder wachten; gebruik
                # daarom de synchrone OpenModel-client.
                return _ask_llm_sync(question)
            loop.run_until_complete(_collect())
        except RuntimeError:
            asyncio.run(_collect())
        return "".join(chunks).strip() or None
    except Exception:
        return _ask_llm_sync(question)


def _ask_llm_sync(question: str) -> Optional[str]:
    """Synchrone fallback via httpx direct naar de OpenModel-gateway."""
    import httpx
    from ...shared.config import (
        OPENMODEL_API_KEY, OPENMODEL_BASE_URL,
    )
    if not OPENMODEL_API_KEY:
        return None
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(
                OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {OPENMODEL_API_KEY}",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": OPENMODEL_SMART_MODEL,
                    "system": ("Je bent een objectieve testassistent. Beantwoord "
                               "de vraag kort (max 3 zinnen) en noem ALLEEN concrete "
                               "merken/bedrijven die je als antwoord beschouwt."),
                    "messages": [{"role": "user", "content": question}],
                    "max_tokens": 300,
                    "thinking": {"type": "disabled"},
                },
            )
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            if usage:
                from ...shared.outcomes import log_llm_usage
                log_llm_usage(
                    backend="openmodel", model=OPENMODEL_SMART_MODEL,
                    route="geo-citation",
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                )
            return "".join(
                b.get("text", "") for b in data.get("content", []) if isinstance(b, dict)
            ).strip() or None
    except Exception:
        return None


def _brand_cited(answer: Optional[str], brand: str) -> bool:
    if not answer:
        return False
    # Case-insensitive, maar vermijd false positives op subwoorden
    # (bijv. 'impact' in 'WeAreImpact' niet matchen op 'impact' alleen).
    pattern = re.compile(r"\b" + re.escape(brand) + r"\b", re.IGNORECASE)
    if pattern.search(answer):
        return True
    # Sommige merken hebben een spatie-loze variant; probeer ook het merk
    # gesplitst op niet-alfanumerieke tekens.
    stripped = re.sub(r"[^a-z0-9]", "", brand.lower())
    if stripped and stripped in re.sub(r"[^a-z0-9]", "", answer.lower()):
        return True
    return False


def run_citation_check() -> Dict[str, Any]:
    """Draai de citatie-check voor alle sites mét persona's.

    Retourneert een samenvatting per site: totaal queries, aantal waarin het
    merk genoemd werd, en de citatie-score (0-100).
    """
    ensure_schema()
    week = date.today().isocalendar()
    week_str = f"{week[0]}-W{week[1]:02d}"
    from . import service as geo_service

    with get_conn() as conn:
        sites = conn.execute(
            "SELECT id, name FROM sites WHERE COALESCE(is_test,0)=0"
        ).fetchall()

    results: List[Dict[str, Any]] = []
    for s in sites:
        site_id = s["id"]
        personas = geo_service.list_personas(site_id)
        if not personas:
            continue
        queries: List[str] = []
        for p in personas:
            queries.extend(p.get("queries") or [])
        if not queries:
            continue

        cited = 0
        total = 0
        for q in queries[:10]:  # cap op 10 queries/site om kosten te temmen
            total += 1
            question = (
                f"Als iemand op zoek is naar een aanbieder voor: '{q}', "
                f"welk(e) merk(en)/bedrijf(en) noem je dan als antwoord?"
            )
            ans = _ask_llm(question)
            is_cited = _brand_cited(ans, s["name"])
            if is_cited:
                cited += 1
            with get_conn() as w:
                w.execute(
                    f"INSERT INTO {CITATION_TABLE} "
                    "(site_id, site_name, query, week, cited, raw_answer, model) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (site_id, s["name"], q, week_str, 1 if is_cited else 0,
                     ans, OPENMODEL_SMART_MODEL),
                )
        score = round(100 * cited / total) if total else 0
        results.append({
            "site_id": site_id,
            "site_name": s["name"],
            "queries": total,
            "cited": cited,
            "citation_score": score,
        })
    return {"week": week_str, "results": results}


def weekly_score(site_id: str, weeks: int = 4) -> List[Dict[str, Any]]:
    """Citatie-score per week voor een site (voor trend-weergave)."""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT week, COUNT(*) AS n, SUM(cited) AS cited FROM {CITATION_TABLE} "
            "WHERE site_id=? GROUP BY week ORDER BY week DESC LIMIT ?",
            (site_id, weeks),
        ).fetchall()
    out = []
    for r in rows:
        n = r["n"] or 0
        out.append({
            "week": r["week"],
            "score": round(100 * (r["cited"] or 0) / n) if n else 0,
            "cited": r["cited"] or 0,
            "queries": n,
        })
    return out


def latest_week_summary() -> Dict[str, Any]:
    """Laatste week citatie-score per site (voor dashboard/Iris)."""
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT MAX(week) FROM {CITATION_TABLE}"
        ).fetchone()
        week = row[0] if row else None
        if not week:
            return {"week": None, "sites": []}
        rows = conn.execute(
            f"SELECT site_id, site_name, COUNT(*) n, SUM(cited) cited FROM {CITATION_TABLE} "
            "WHERE week=? GROUP BY site_id", (week,)
        ).fetchall()
    sites = []
    for r in rows:
        n = r["n"] or 0
        sites.append({
            "site_id": r["site_id"],
            "site_name": r["site_name"],
            "citation_score": round(100 * (r["cited"] or 0) / n) if n else 0,
            "cited": r["cited"] or 0,
            "queries": n,
        })
    return {"week": week, "sites": sites}
