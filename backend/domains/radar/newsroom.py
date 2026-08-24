"""
WeAreImpact Nieuwsagent — een dagelijkse, professioneel geanalyseerde
nieuwsbriefing bovenop Mission Radar.

Radar verzamelt al continu signalen voor WeAreImpact (sector-keywords,
vakmedia-RSS, concurrent/vakgenoot-sites) via de bestaande watchlist —
dit bouwt daar geen tweede verzamelmachine naast (zelfde les als 3a-ter:
"één weg naar de Gauntlet"). Wat ontbreekt is de laag ERBOVEN: niet "hier
is een lijst signalen met een score", maar "dit is het belangrijkste
nieuws van vandaag, dit betekent het voor WeAreImpact, en dit zou je
ermee kunnen doen" — zoals een analist het zou brengen.

Pipeline:
  1. Selecteer verse, ongebriefte signalen (laatste 48u, boven de
     kwaliteitspoort van radar/quality.py, nog niet eerder in een
     briefing verwerkt — radar_news_briefings is de dedupe-tabel).
  2. Categoriseer deterministisch (sector / concurrent & vakmedia /
     algemeen) op basis van het watch-type — geen LLM nodig voor een
     vormvraag (zelfde afweging als de signaalpoort zelf).
  3. Verdiep de sterkste per categorie met één LLM-oordeel: samenvatting,
     relevantie (het "zo wat" voor WeAreImpact) en een concrete
     actie-suggestie. Faalt een item, dan valt het uit de briefing —
     nooit de hele ronde.
  4. Schrijf de briefing naar de vault (ALTIJD, ook bij nul items — "geen
     nieuws" moet een expliciete uitspraak zijn, geen leeg bestand dat
     als storing leest) en log één outcome-kaart.

Nooit publiceert of verstuurt dit iets — puur signaal + duiding voor een
mens (of voor Iris, via prompt_block()).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ...shared.config import OBSIDIAN_VAULT_PATH
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...shared.outcomes import log_outcome
from .service import get_service

log = logging.getLogger(__name__)

PROJECT = "weareimpact"
NEWS_VAULT_DIR = "10_Projects/_nieuws/WeAreImpact"
LOOKBACK_HOURS = 48
TOP_N = 6
PER_CATEGORY_MAX = {"sector": 4, "concurrent": 3, "algemeen": 2}

ANALYST_PROFILE_NAME = "WeAreImpact Nieuwsanalist"
_ANALYST_MODEL = "openrouter/openai/gpt-oss-120b:free"

_ANALYST_PROMPT = (
    "Je bent de nieuwsanalist voor WeAreImpact (interim-management, AI-implementatie "
    "en verandermanagement voor gemeenten, zorg en welzijn — het sociaal domein). "
    "Je krijgt één nieuwsbericht (titel, bron, snippet). Analyseer het zoals een "
    "professionele branche-analist dat voor een directeur zou doen: kort, feitelijk, "
    "zonder marketingtaal.\n\n"
    "Lever exact drie dingen:\n"
    "- samenvatting: één zin, wat is er feitelijk gebeurd.\n"
    "- relevantie: waarom dit ertoe doet voor WeAreImpact specifiek (niet in het "
    "algemeen 'interessant', maar wat het betekent voor hun positionering, klanten "
    "of markt). Als het er in werkelijkheid niet toe doet, zeg dat eerlijk.\n"
    "- actie: één concrete, uitvoerbare vervolgstap (bijv. 'schrijf hierover', 'volg "
    "deze ontwikkeling bij [organisatie]', 'gebruik dit in het volgende klantgesprek'). "
    "Is er geen zinnige actie, antwoord dan letterlijk 'geen actie nodig'.\n\n"
    "ANTWOORD UITSLUITEND met één JSON-object, zonder markdown eromheen:\n"
    '{"samenvatting": "<één zin>", "relevantie": "<1-2 zinnen>", "actie": "<één zin>"}'
)

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radar_news_briefings (
                id           TEXT PRIMARY KEY,
                project      TEXT NOT NULL,
                signal_id    TEXT NOT NULL,
                briefing_date TEXT NOT NULL,
                categorie    TEXT NOT NULL DEFAULT 'sector',
                samenvatting TEXT DEFAULT '',
                relevantie   TEXT DEFAULT '',
                actie        TEXT DEFAULT '',
                title        TEXT DEFAULT '',
                url          TEXT DEFAULT '',
                score        REAL DEFAULT 0,
                created_at   TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_news_briefing_signal "
            "ON radar_news_briefings(project, signal_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_briefing_date "
            "ON radar_news_briefings(project, briefing_date)"
        )
    _schema_ready = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Extra watches voor "algemeen zakelijk/AI-nieuws" ────────────────────────
# De bestaande WeAreImpact-watchlist (zie radar_watchlist) dekt al sector
# (keywords sociaal domein/zorg/AI) en concurrent & vakmedia (site-watches
# als Murakami, Brightest, KPMG, Nictiz, VNG). Wat ontbrak was een bredere
# blik op algemeen AI/ondernemersnieuws — niet gebonden aan het sociaal
# domein maar wel relevant voor een AI-adviesbureau. Idempotent: draait bij
# elke briefing, voegt alleen toe wat nog niet bestaat.
_ALGEMEEN_WATCHES = [
    ("Algemeen: AI in Nederlandse organisaties", "keyword", "ai adoptie nederlandse organisaties"),
    ("Algemeen: AI-regelgeving EU", "keyword", "ai act europese regelgeving bedrijven"),
]

# Watch-types/labels die "concurrent & vakmedia" zijn i.p.v. "sector" — de
# consultancy/advies-achtige site-watches. De rest van type site/competitor
# (institutionele vakmedia als VNG/Nictiz/Zorgvisie) telt als sector-nieuws:
# dat ís het sociaal domein zelf, geen concurrent.
_CONCURRENT_DOMEINEN = {"murakami.nl", "brightest.nl", "novi.nl", "kpmg.com/nl"}


def ensure_watches() -> None:
    """Zaai de ontbrekende 'algemeen'-watches (idempotent op (project, type, value))."""
    svc = get_service()
    bestaand = {(w["type"], w["value"].lower()) for w in svc.list_watch(PROJECT)}
    for label, wtype, value in _ALGEMEEN_WATCHES:
        if (wtype, value.lower()) in bestaand:
            continue
        try:
            svc.add_watch(PROJECT, label, wtype, value)
        except ValueError:
            log.warning("[newsroom] Kon watch '%s' niet toevoegen", label)


def _categorize(sig: Dict, watch_by_id: Dict[str, Dict]) -> str:
    watch = watch_by_id.get(sig.get("watch_id") or "")
    wtype = (watch or {}).get("type", "keyword")
    value = ((watch or {}).get("value") or "").lower()
    label = ((watch or {}).get("label") or "")
    if label.startswith("Algemeen:"):
        return "algemeen"
    if wtype in ("competitor",) or value in _CONCURRENT_DOMEINEN:
        return "concurrent"
    if wtype == "site":
        return "concurrent" if value in _CONCURRENT_DOMEINEN else "sector"
    return "sector"  # keyword, rss, youtube, reddit


def _select_candidates() -> List[Dict]:
    """Verse, ongebriefte, niet-uitgefilterde signalen — brand_mention is PR-
    bewijs, geen nieuws, en blijft daarom buiten deze selectie."""
    ensure_schema()
    svc = get_service()
    watches = {w["id"]: w for w in svc.list_watch(PROJECT)}
    merk_watch_ids = {w["id"] for w in watches.values() if w["type"] == "brand_mention"}
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radar_signals
               WHERE LOWER(project) = ? AND status = 'new'
                 AND created_at >= datetime('now', ?)
               ORDER BY signal_score DESC LIMIT 60""",
            (PROJECT, f"-{LOOKBACK_HOURS} hours"),
        ).fetchall()
        already = {
            r["signal_id"] for r in conn.execute(
                "SELECT signal_id FROM radar_news_briefings WHERE project = ?", (PROJECT,)
            ).fetchall()
        }
    candidates = []
    for r in rows:
        d = dict(r)
        if d["id"] in already or d.get("watch_id") in merk_watch_ids:
            continue
        d["categorie"] = _categorize(d, watches)
        candidates.append(d)

    # Diversifieer: cap per categorie, dan de rest aanvullen tot TOP_N op score.
    gekozen: List[Dict] = []
    per_cat: Dict[str, int] = {}
    for d in candidates:
        cat = d["categorie"]
        if per_cat.get(cat, 0) >= PER_CATEGORY_MAX.get(cat, 2):
            continue
        gekozen.append(d)
        per_cat[cat] = per_cat.get(cat, 0) + 1
        if len(gekozen) >= TOP_N:
            break
    return gekozen


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1].strip() if start != -1 and end > start else raw.strip()


async def _analyze(sig: Dict) -> Optional[Dict]:
    user_content = (
        f"Titel: {sig.get('title','')}\nBron: {sig.get('source','')} ({sig.get('url','')})\n"
        f"Snippet: {(sig.get('snippet') or '')[:1200]}"
    )
    chunks: List[str] = []
    try:
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_ANALYST_PROMPT,
            agent="hermes",
            use_tools=False,
            max_tokens=500,
            purpose="weareimpact-nieuws",
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
    except Exception as e:  # noqa: BLE001
        log.warning("[newsroom] Analyse mislukt voor '%s': %s", sig.get("title", "")[:60], e)
        return None
    raw = "".join(chunks)
    if not raw:
        return None
    try:
        parsed = json.loads(_strip_json(raw))
    except Exception:
        log.warning("[newsroom] Onparseerbare analyse voor '%s'", sig.get("title", "")[:60])
        return None
    return {
        "samenvatting": str(parsed.get("samenvatting", ""))[:400],
        "relevantie": str(parsed.get("relevantie", ""))[:600],
        "actie": str(parsed.get("actie", ""))[:300],
    }


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "briefing").lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug or "briefing")[:max_len].rstrip("-")


_CATEGORIE_LABEL = {
    "sector": "Sector — sociaal domein, zorg & AI",
    "concurrent": "Concurrentie & vakmedia",
    "algemeen": "Algemeen AI- & ondernemersnieuws",
}


def _write_vault(date: str, items: List[Dict]) -> Optional[str]:
    if not OBSIDIAN_VAULT_PATH:
        return None
    vault = Path(OBSIDIAN_VAULT_PATH)
    if not vault.exists():
        return None
    out_dir = vault / NEWS_VAULT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date}.md"

    lines = [
        "---", "type: weareimpact-nieuwsbriefing", f"date: {date}",
        f"created: {_now()}", "---", "",
        f"# WeAreImpact Nieuwsbriefing — {date}", "",
    ]
    if not items:
        lines.append("_Geen nieuws vandaag dat de kwaliteits- en relevantiepoort haalde._")
    else:
        by_cat: Dict[str, List[Dict]] = {}
        for it in items:
            by_cat.setdefault(it["categorie"], []).append(it)
        for cat in ("sector", "concurrent", "algemeen"):
            if cat not in by_cat:
                continue
            lines += [f"## {_CATEGORIE_LABEL[cat]}", ""]
            for it in by_cat[cat]:
                lines += [
                    f"### [{it['title']}]({it['url']})",
                    f"**Wat er gebeurde:** {it['samenvatting']}",
                    f"**Relevantie voor WeAreImpact:** {it['relevantie']}",
                    f"**Actie:** {it['actie']}",
                    "",
                ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path.relative_to(vault))


async def build_daily_briefing() -> Dict:
    """Bouw + schrijf de dagelijkse WeAreImpact-nieuwsbriefing. Idempotent per
    dag qua bron-signalen (die worden nooit tweemaal gebriefd), maar een
    herrun dezelfde dag overschrijft het vault-bestand met de dan bekende
    stand — net als het weekrapport (10b)."""
    ensure_schema()
    ensure_watches()
    date = _today()
    candidates = _select_candidates()

    items: List[Dict] = []
    for sig in candidates:
        result = await _analyze(sig)
        if not result:
            continue
        items.append({
            "signal_id": sig["id"], "categorie": sig["categorie"],
            "title": sig.get("title", ""), "url": sig.get("url", ""),
            "score": sig.get("signal_score", 0),
            **result,
        })

    now = _now()
    if items:
        with get_conn() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO radar_news_briefings
                   (id, project, signal_id, briefing_date, categorie, samenvatting,
                    relevantie, actie, title, url, score, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(f"{it['signal_id']}-{date}", PROJECT, it["signal_id"], date, it["categorie"],
                  it["samenvatting"], it["relevantie"], it["actie"], it["title"], it["url"],
                  it["score"], now)
                 for it in items],
            )

    vault_path = _write_vault(date, items)

    if items:
        top_actie = next((it["actie"] for it in items if it["actie"].lower() != "geen actie nodig"), "")
        detail = (f"{len(items)} nieuwsitem(s) geanalyseerd "
                  f"({', '.join(sorted({it['categorie'] for it in items}))})")
        next_step = top_actie or "Geen van de items vraagt vandaag om een actie — lezen volstaat."
    else:
        detail = "Geen nieuws vandaag dat de kwaliteits- en relevantiepoort haalde."
        next_step = ""
    log_outcome(
        "WeAreImpact", "nieuws_briefing", detail,
        artifact=vault_path or "", next_step=next_step, status="ok",
    )
    log.info("[newsroom] WeAreImpact-nieuwsbriefing %s: %d item(s)", date, len(items))
    return {"date": date, "items": items, "obsidian_path": vault_path}


def latest_briefing(limit_days: int = 3) -> Dict:
    """Voor de API/dashboard: de meest recente briefing-items (laatste
    `limit_days` dagen, want een handmatige rerun kan een dag overslaan)."""
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radar_news_briefings WHERE project = ?
               AND briefing_date >= date('now', ?)
               ORDER BY briefing_date DESC, score DESC""",
            (PROJECT, f"-{limit_days} days"),
        ).fetchall()
    items = [dict(r) for r in rows]
    date = items[0]["briefing_date"] if items else None
    return {"date": date, "items": [it for it in items if it["briefing_date"] == date] if date else []}


def prompt_block() -> str:
    """Voor Iris' briefing-prompt: de nieuwsbriefing van vandaag/gisteren als
    tekst. Expliciet leeg ≠ stil weglaten — een ontbrekend blok leest anders
    als 'geen nieuws' terwijl de briefing misschien gewoon nog niet draaide."""
    try:
        b = latest_briefing(limit_days=2)
    except Exception as e:  # noqa: BLE001
        log.exception("[newsroom] prompt_block mislukt")
        return f"WeAreImpact-nieuws: NIET beschikbaar deze ronde ({type(e).__name__})."
    if not b["date"]:
        return "WeAreImpact-nieuws: nog geen briefing van de laatste 2 dagen — trek hier geen conclusie uit."
    if not b["items"]:
        return f"WeAreImpact-nieuws ({b['date']}): geen items die de relevantiepoort haalden."
    lines = [f"WeAreImpact-nieuws ({b['date']}):"]
    for it in b["items"]:
        lines.append(f"- [{it['categorie']}] {it['title']}: {it['relevantie']} -> {it['actie']}")
    return "\n".join(lines)
