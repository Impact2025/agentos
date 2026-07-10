"""
Mission Radar (Hermes Astros) — de Sky Scanner die concurrenten, keywords en
RSS-feeds monitort en trending content omzet in eigen content-kansen.

Pipeline per watch-item:
  1. Bron ophalen        Tavily (keyword / site:concurrent) of RSS (feedparser)
  2. Dedupliceren        op (project, url) — bestaande signalen slaan we over
  3. Signal Score        LLM-vrije heuristiek (scorer.compute_signal_score)
  4. AI-verrijking       hook + unieke invalshoek + 3 titels voor de beste hits
  5. Bulk-opslag         executemany per batch (ontlast SQLite naast de
                         2-seconden conveyor-poll)
  6. Geheugen-loop       topsignalen → markdown in de Obsidian-vault
                         (10_Projects/_trends/) zodat de chat-agent er direct
                         uit kan putten

De AEO Domination Journey (aeo_attack) zet een goedgekeurd signaal om in een
keten gekoppelde conveyor-taken: listicle → videoscript → Reddit-concept.
Elke stap krijgt de output van zijn voorganger (pipeline.get_previous_result
werkt op de gedeelde workspace-basismap). Er wordt NOOIT automatisch
gepubliceerd — de taken leveren concepten.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...shared.config import (
    TAVILY_API_KEY, OBSIDIAN_VAULT_PATH,
    AEO_AUTO_ATTACK, AEO_AUTO_MIN_SCORE, AEO_AUTO_MAX_PER_SCAN,
)
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ..vacancies import scraper
from . import scorer
from .models import ensure_schema

log = logging.getLogger(__name__)

SCAN_LOOKBACK_DAYS = 14          # hoe ver terug we "trending" laten meetellen
MAX_RESULTS_PER_WATCH = 6        # Tavily-resultaten per watch-item
MAX_AI_ENRICH_PER_WATCH = 4      # LLM-verrijking alleen voor de beste hits
MIN_SCORE_FOR_ENRICH = 30.0      # heuristische ondergrens voor AI-verrijking
MIN_SCORE_FOR_OBSIDIAN = 70.0    # topsignalen gaan automatisch de vault in

TRENDS_VAULT_DIR = "10_Projects/_trends"

# AEO-kanalen → (workspace-bestandsnaam, expertprofiel, taakomschrijving-template)
AEO_CHANNELS = ("listicle", "video", "reddit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(raw: str) -> str:
    """Haal het JSON-object uit een LLM-antwoord, ook met ```-fences eromheen."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    return s[start:end + 1] if start != -1 and end > start else s


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "trend").lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug or "trend")[:max_len].rstrip("-")


class RadarService:
    def __init__(self):
        ensure_schema()
        scorer.ensure_analyst_profile()
        self._tavily = None
        if TAVILY_API_KEY:
            try:
                from tavily import TavilyClient
                self._tavily = TavilyClient(api_key=TAVILY_API_KEY)
            except Exception:
                pass

    # ── Watchlist CRUD ───────────────────────────────────────────────────────

    def add_watch(self, project: str, label: str, wtype: str, value: str) -> Dict:
        project = (project or "").strip().lower()
        if wtype not in ("keyword", "competitor", "rss"):
            raise ValueError(f"Ongeldig watch-type '{wtype}'")
        value = value.strip()
        if wtype == "competitor":
            # Normaliseer naar kaal domein — gebruikers plakken vaak volledige URL's.
            bare = re.sub(r"^https?://", "", value).split("/")[0]
            if bare.startswith("www."):
                bare = bare[4:]
            value = bare or value
        item = {
            "id": str(uuid.uuid4()), "project": project, "label": label.strip() or value,
            "type": wtype, "value": value, "active": 1,
            "last_scanned_at": "", "created_at": _now(),
        }
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, last_scanned_at, created_at) "
                "VALUES (:id, :project, :label, :type, :value, :active, :last_scanned_at, :created_at)",
                item,
            )
        return item

    def list_watch(self, project: Optional[str] = None) -> List[Dict]:
        project = (project or "").strip().lower() or None
        with get_conn() as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM radar_watchlist WHERE project = ? ORDER BY created_at",
                    (project,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM radar_watchlist ORDER BY project, created_at").fetchall()
        return [dict(r) for r in rows]

    def set_watch_active(self, watch_id: str, active: bool) -> bool:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE radar_watchlist SET active = ? WHERE id = ?",
                (1 if active else 0, watch_id),
            )
        return cur.rowcount > 0

    def delete_watch(self, watch_id: str) -> bool:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM radar_watchlist WHERE id = ?", (watch_id,))
        return cur.rowcount > 0

    # ── Bronnen ──────────────────────────────────────────────────────────────

    def _tavily_search(self, query: str, days: int = SCAN_LOOKBACK_DAYS,
                       max_results: int = MAX_RESULTS_PER_WATCH) -> List[Dict]:
        if not self._tavily:
            log.warning("[radar] Geen TAVILY_API_KEY geconfigureerd — scan levert niets op")
            return []
        try:
            resp = self._tavily.search(
                query=query,
                max_results=max_results,
                search_depth="advanced",
                include_answer=False,
                days=days,
                topic="news" if "site:" not in query else "general",
            )
        except TypeError:
            # Oudere tavily-python zonder days/topic-parameters.
            try:
                resp = self._tavily.search(query=query, max_results=max_results,
                                           search_depth="advanced", include_answer=False)
            except Exception as e:
                log.error("[radar] Tavily fout: %s", e)
                return []
        except Exception as e:
            log.error("[radar] Tavily fout: %s", e)
            return []
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "tavily_score": float(r.get("score") or 0.0),
                "published_days_ago": _days_ago(r.get("published_date")),
            }
            for r in resp.get("results", [])
            if r.get("url")
        ]

    def _fetch_rss(self, feed_url: str) -> List[Dict]:
        try:
            import feedparser
            feed = feedparser.parse(feed_url)
        except Exception as e:
            log.warning("[radar] RSS-feed %s onleesbaar: %s", feed_url, e)
            return []
        out: List[Dict] = []
        now = datetime.now(timezone.utc)
        for entry in feed.entries[:20]:
            days = -1
            for attr in ("published_parsed", "updated_parsed"):
                tp = getattr(entry, attr, None)
                if tp:
                    days = max(0, (now - datetime(*tp[:6], tzinfo=timezone.utc)).days)
                    break
            if days > SCAN_LOOKBACK_DAYS:
                continue
            out.append({
                "title": getattr(entry, "title", ""),
                "url": getattr(entry, "link", ""),
                "snippet": re.sub(r"<[^>]+>", " ", getattr(entry, "summary", ""))[:800],
                "tavily_score": 0.5,
                "published_days_ago": days,
            })
        return [r for r in out if r["url"]]

    def _gather(self, watch: Dict) -> List[Dict]:
        """Ruwe resultaten per watch-item. Voor keywords doen we ook een
        Reddit-gescopeerde zoekactie — Google weegt Reddit-discussies zwaar
        mee in AI Overviews, dus die signalen willen we niet missen."""
        wtype, value = watch["type"], watch["value"]
        if wtype == "rss":
            results = self._fetch_rss(value)
            for r in results:
                r["source"] = "rss"
            return results
        if wtype == "competitor":
            # Tavily weigert een 'site:'-query zónder zoekterm — dus voegen we
            # het merkwoord (het domein zónder tld) toe zodat de concurrent zijn
            # eigen branded content teruggeeft. `value` is al genormaliseerd naar
            # een kaal domein in add_watch().
            # VERBREDING (juli 2026): veel NL-teambuilding-concurrenten publiceren
            # weinig puur branded content, dus voegen we generieke termen toe en
            # vergroten we de lookback zodat ook oudere landingspagina's boven
            # komen. Zonder dit bleven 5/8 concurrenten op 0 signalen hangen.
            brand = value.split(".")[0] or value
            results = self._tavily_search(
                f"site:{value} ({brand} OR teambuilding OR training OR 'team uitje')",
                days=90,
            )
        else:  # keyword
            results = self._tavily_search(value)
            results += self._tavily_search(f"site:reddit.com {value}", max_results=3)
        for r in results:
            r["source"] = scorer.classify_source(r["url"])
        return results

    # ── Scan (async generator — SSE-route én scheduler consumeren dit) ──────

    async def run_scan(self, project: Optional[str] = None, enrich: bool = True):
        watches = [w for w in self.list_watch(project) if w["active"]]
        yield {"type": "scan_start", "watch_count": len(watches)}
        if not watches:
            yield {"type": "scan_done", "total_saved": 0,
                   "note": "Watchlist is leeg — voeg concurrenten of keywords toe."}
            return

        total_saved = 0
        now = _now()
        for watch in watches:
            yield {"type": "watch_start", "label": watch["label"], "watch_type": watch["type"]}
            try:
                results = self._gather(watch)
            except Exception as e:
                log.exception("[radar] Scan van '%s' mislukt", watch["label"])
                yield {"type": "watch_error", "label": watch["label"], "error": str(e)[:200]}
                continue

            # Dedupe in één query per watch-item i.p.v. één per resultaat.
            urls = [r["url"] for r in results]
            existing: set = set()
            if urls:
                with get_conn() as conn:
                    ph = ",".join("?" * len(urls))
                    existing = {
                        row["url"] for row in conn.execute(
                            f"SELECT url FROM radar_signals WHERE project = ? AND url IN ({ph})",
                            [watch["project"], *urls],
                        ).fetchall()
                    }
            fresh = [r for r in results if r["url"] not in existing]
            # Ook binnen deze batch dedupliceren (keyword- + reddit-query overlappen soms).
            seen: set = set()
            fresh = [r for r in fresh if not (r["url"] in seen or seen.add(r["url"]))]

            # Heuristische score voor alles.
            for r in fresh:
                r["signal_score"] = scorer.compute_signal_score(
                    r["title"], r["url"], watch["value"],
                    r.get("published_days_ago", -1), r.get("tavily_score", 0.0),
                    r.get("source"), project=watch["project"],
                )
            fresh.sort(key=lambda r: r["signal_score"], reverse=True)

            # AI-verrijking alleen voor de kansrijkste hits (kosten/tijd).
            enriched = 0
            for r in fresh:
                r.update({"ai_hook": "", "ai_angle": "", "ai_titles": [], "ai_match_score": -1})
                if not enrich or enriched >= MAX_AI_ENRICH_PER_WATCH:
                    continue
                if r["signal_score"] < MIN_SCORE_FOR_ENRICH:
                    continue
                yield {"type": "analyzing", "title": r["title"], "label": watch["label"]}
                angle = await scorer.generate_angle(
                    r["title"], r["url"], r["source"], r["snippet"],
                    watch["value"], watch["project"],
                )
                r.update({
                    "ai_hook": angle["hook"], "ai_angle": angle["angle"],
                    "ai_titles": angle["titles"], "ai_match_score": angle["match_score"],
                })
                r["signal_score"] = scorer.blend_scores(r["signal_score"], angle["match_score"])
                enriched += 1

            # Bulk-opslag: één executemany per watch-item.
            rows = [
                (
                    str(uuid.uuid4()), watch["id"], watch["project"], watch["value"],
                    r["title"][:300], r["url"], r["source"], (r["snippet"] or "")[:2000],
                    int(r.get("published_days_ago", -1)), r["signal_score"],
                    r["ai_hook"], r["ai_angle"], json.dumps(r["ai_titles"], ensure_ascii=False),
                    int(r["ai_match_score"]), "new", "", now, now, now,
                )
                for r in fresh
            ]
            if rows:
                with get_conn() as conn:
                    conn.executemany(
                        """INSERT OR IGNORE INTO radar_signals
                           (id, watch_id, project, keyword, title, url, source, snippet,
                            published_days_ago, signal_score, ai_hook, ai_angle, ai_titles,
                            ai_match_score, status, obsidian_path, scanned_at, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        rows,
                    )
                    conn.execute(
                        "UPDATE radar_watchlist SET last_scanned_at = ? WHERE id = ?",
                        (now, watch["id"]),
                    )
                total_saved += len(rows)

            # Geheugen-loop: topsignalen direct de vault in.
            for r in fresh:
                if r["signal_score"] >= MIN_SCORE_FOR_OBSIDIAN and r["ai_angle"]:
                    sig = self._signal_by_url(watch["project"], r["url"])
                    if sig:
                        self.write_trend_note(sig)

            yield {
                "type": "watch_done", "label": watch["label"],
                "found": len(rows), "skipped": len(results) - len(fresh),
                "top_score": fresh[0]["signal_score"] if fresh else 0,
            }

        yield {"type": "scan_done", "total_saved": total_saved}

        # Autonome vervolgstap: de agent start zelfstandig AEO-aanvallen op de
        # beste verse signalen (tot aan de Wachtrij-gate). De mens hoeft alleen
        # nog "publiceer" te klikken. Zacht — faalt nooit de hele scan.
        if total_saved:
            try:
                attacked = self._auto_aeo_top_signals()
                if attacked:
                    yield {"type": "auto_aeo", "count": len(attacked),
                           "signals": attacked}
            except Exception:
                log.exception("[radar] Auto-AEO na scan mislukt (niet fataal)")

    def _auto_aeo_top_signals(self) -> List[str]:
        """Na een scan: start zelfstandig AEO-aanvallen op de beste verse
        signalen. Idempotent: alleen signalen met status 'new' en een score
        boven AEO_AUTO_MIN_SCORE komen in aanmerking, max AEO_AUTO_MAX_PER_SCAN
        per run. Geeft de titels terug van de aangevallen signalen (voor de
        SSE-feed / log). Doe niets als AEO_AUTO_ATTACK uit staat.

        Cluster-diepte-voorkeur: binnen de kandidaten krijgen signalen die het
       zelfde onderwerp-cluster raken als al gepubliceerde content voorrang.
        Zo bouwt de agent één silo dicht in plaats van breed te sproeien —
        topical authority komt van diepte, niet van volume."""
        if not AEO_AUTO_ATTACK:
            return []
        top = [
            s for s in self.list_signals(status="new", limit=50)
            if (s.get("signal_score") or 0) >= AEO_AUTO_MIN_SCORE
        ]
        if not top:
            return []

        # Bouw de set cluster-tokens van reeds gepubliceerde content (per
        # project) zodat we kunnen meten welke signalen een bestaande silo
        # verdiepen.
        cluster_tokens: Dict[str, set] = {}
        try:
            from ...shared.database import get_conn as _gc
            with _gc() as conn:
                for r in conn.execute(
                    "SELECT site_id, title, slug FROM published_pages "
                    "WHERE html != ''"
                ).fetchall():
                    r = dict(r)
                    tok = {t for t in re.findall(r"[a-zà-ü0-9]{4,}",
                              ((r.get("title") or "") + " " + (r.get("slug") or "")).lower())}
                    # site_id is hier de project-naam-gelijke key; we indexeren
                    # op project via de signalen zelf hieronder.
                    cluster_tokens.setdefault(r.get("site_id") or "", set()).update(tok)
        except Exception:
            cluster_tokens = {}

        def _depth_bonus(sig) -> int:
            kw = (sig.get("keyword") or sig.get("title") or "").lower()
            kw_tokens = {t for t in re.findall(r"[a-zà-ü0-9]{4,}", kw)}
            if not kw_tokens:
                return 0
            # Match tegen alle bekende clusters (project-loos: NL-sites zijn klein).
            overlap = 0
            for toks in cluster_tokens.values():
                overlap = max(overlap, len(kw_tokens & toks))
            return overlap

        # Re-rank: diepte (bestaande silo versterken) telt zwaarder dan score.
        top.sort(key=lambda s: (_depth_bonus(s), s.get("signal_score", 0)), reverse=True)
        attacked: List[str] = []
        for sig in top[: max(0, AEO_AUTO_MAX_PER_SCAN)]:
            try:
                self.aeo_attack(sig["id"])
                attacked.append(sig.get("title", "")[:120])
            except Exception:
                log.exception("[radar] Auto-AEO voor signaal %s mislukt", sig.get("id"))
        if attacked:
            log.info("[radar] Auto-AEO startte %d aanval(len) op top-signalen", len(attacked))
        return attacked

    def _signal_by_url(self, project: str, url: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM radar_signals WHERE LOWER(project) = LOWER(?) AND url = ?", (project or "", url)
            ).fetchone()
        return dict(row) if row else None

    # ── Signals CRUD ─────────────────────────────────────────────────────────

    def list_signals(self, project: Optional[str] = None, status: Optional[str] = None,
                     min_score: Optional[float] = None, source: Optional[str] = None,
                     limit: int = 100) -> List[Dict]:
        where, params = [], []
        if project:
            where.append("LOWER(project) = LOWER(?)"); params.append(project)
        if status:
            where.append("status = ?"); params.append(status)
        if source:
            where.append("source = ?"); params.append(source)
        if min_score is not None:
            where.append("signal_score >= ?"); params.append(min_score)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM radar_signals {clause} "
                f"ORDER BY signal_score DESC, created_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["ai_titles"] = json.loads(d.get("ai_titles") or "[]")
            except Exception:
                d["ai_titles"] = []
            out.append(d)
        return out

    def get_signal(self, signal_id: str) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM radar_signals WHERE id = ?", (signal_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["ai_titles"] = json.loads(d.get("ai_titles") or "[]")
        except Exception:
            d["ai_titles"] = []
        return d

    def update_signal_status(self, signal_id: str, status: str) -> Optional[Dict]:
        if status not in ("new", "targeted", "converted", "dismissed"):
            raise ValueError(f"Ongeldige status '{status}'")
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE radar_signals SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), signal_id),
            )
            if cur.rowcount == 0:
                return None
        sig = self.get_signal(signal_id)
        # Geheugen-loop: wat we besluiten te targeten gaat altijd de vault in.
        if sig and status == "targeted" and not sig.get("obsidian_path"):
            self.write_trend_note(sig)
            sig = self.get_signal(signal_id)
        return sig

    def delete_signal(self, signal_id: str) -> bool:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM radar_signals WHERE id = ?", (signal_id,))
        return cur.rowcount > 0

    def get_stats(self, project: Optional[str] = None) -> Dict:
        where = "WHERE LOWER(project) = LOWER(?)" if project else ""
        params = [project] if project else []
        with get_conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM radar_signals {where}", params).fetchone()[0]
            by_status = dict(conn.execute(
                f"SELECT status, COUNT(*) FROM radar_signals {where} GROUP BY status", params
            ).fetchall())
            top = conn.execute(
                f"SELECT MAX(signal_score) FROM radar_signals {where}", params
            ).fetchone()[0]
            watch_count = conn.execute(
                "SELECT COUNT(*) FROM radar_watchlist WHERE active = 1" +
                (" AND LOWER(project) = LOWER(?)" if project else ""), params,
            ).fetchone()[0]
        return {
            "total": total,
            "new": by_status.get("new", 0),
            "targeted": by_status.get("targeted", 0),
            "converted": by_status.get("converted", 0),
            "dismissed": by_status.get("dismissed", 0),
            "top_score": top or 0,
            "watch_count": watch_count,
        }

    # ── Geheugen-loop: Obsidian ──────────────────────────────────────────────

    def write_trend_note(self, sig: Dict) -> Optional[str]:
        """Schrijf een signaal als markdown-note in de vault
        (10_Projects/_trends/). De chat-agent (ObsidianService.search) pikt
        deze automatisch op — vraag 'wat moet ik vandaag schrijven?' en de
        verse concurrentie-analyse doet mee als context."""
        if not OBSIDIAN_VAULT_PATH:
            return None
        vault = Path(OBSIDIAN_VAULT_PATH)
        if not vault.exists():
            return None

        titles = sig.get("ai_titles") or []
        if isinstance(titles, str):
            try:
                titles = json.loads(titles)
            except Exception:
                titles = []

        slug = _slugify(f"{sig.get('keyword','trend')}-{sig.get('title','')}")
        note_dir = vault / TRENDS_VAULT_DIR
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{slug}.md"

        lines = [
            "---",
            f"score: {sig.get('signal_score', 0)}",
            f"source: {sig.get('url', '')}",
            f'keyword: "{sig.get("keyword", "")}"',
            f'project: "{sig.get("project", "")}"',
            f"status: {sig.get('status', 'new')}",
            f"created: {datetime.now().strftime('%Y-%m-%d')}",
            "---",
            "",
            f"# {sig.get('title', 'Trend')}",
            "",
            "## Hook",
            sig.get("ai_hook") or "_nog niet gegenereerd_",
            "",
            "## Aanbevolen invalshoek",
            sig.get("ai_angle") or "_nog niet gegenereerd_",
            "",
            "## Titel-suggesties",
        ]
        lines += [f"- {t}" for t in titles] or ["- _nog geen_"]
        lines += ["", "## Bron-snippet", (sig.get("snippet") or "")[:1200], ""]

        note_path.write_text("\n".join(lines), encoding="utf-8")
        rel = str(note_path.relative_to(vault))
        with get_conn() as conn:
            conn.execute(
                "UPDATE radar_signals SET obsidian_path = ?, updated_at = ? WHERE id = ?",
                (rel, _now(), sig["id"]),
            )
        return rel

    # ── AEO Domination Journey ───────────────────────────────────────────────

    def aeo_attack(self, signal_id: str, channels: Optional[List[str]] = None) -> Dict:
        """Zet een signaal om in gekoppelde conveyor-taken (één keyword,
        meerdere formaten). De taken delen een workspace-basismap zodat elke
        stap de output van de vorige als input krijgt."""
        sig = self.get_signal(signal_id)
        if not sig:
            raise LookupError("Signaal niet gevonden")
        channels = [c for c in (channels or list(AEO_CHANNELS)) if c in AEO_CHANNELS]
        if not channels:
            raise ValueError("Geen geldige kanalen opgegeven")

        titles = sig.get("ai_titles") or []
        chosen_title = titles[0] if titles else (sig.get("ai_angle") or sig["title"])
        keyword = sig.get("keyword") or sig["title"]
        hook = sig.get("ai_hook") or ""
        angle = sig.get("ai_angle") or ""
        base = f"radar-aeo-{_slugify(chosen_title)}"

        context = (
            f"Keyword: {keyword}\nGekozen titel: {chosen_title}\n"
            f"Hook: {hook}\nUnieke invalshoek: {angle}\n"
            f"Trending bron (alleen ter inspiratie, NIET kopiëren): {sig['title']} — {sig['url']}"
        )
        specs = {
            "listicle": (
                "SEO Copywriter",
                f"AEO-listicle: {chosen_title}",
                "Schrijf een SEO-geoptimaliseerde listicle van ~1000 woorden voor Google "
                f"AI Overviews (AEO).\n\n{context}\n\nEisen: listicle-structuur met genummerde "
                "H2's, intro die de zoekintentie meteen beantwoordt, FAQ-sectie (3-5 vragen), "
                "meta-titel + meta-description onderaan. Nederlands, B1-niveau.",
            ),
            "video": (
                "Video Director",
                f"YouTube/TikTok-script: {chosen_title}",
                "Schrijf een YouTube-script (met shot list voor AI-avatar/b-roll) plus een "
                f"korte TikTok/Shorts-variant over exact hetzelfde keyword.\n\n{context}\n\n"
                "Gebruik de listicle-tekst van de vorige stap als inhoudelijke basis zodat "
                "blog en video dezelfde boodschap dragen. Nederlands.",
            ),
            "reddit": (
                "Social Media Copywriter",
                f"Reddit-discussiepost: {chosen_title}",
                "Schrijf een Reddit-discussiepost-CONCEPT (titel + body) over hetzelfde "
                f"onderwerp.\n\n{context}\n\nToon: authentiek Reddit — persoonlijke ervaring "
                "of open vraag, geen marketingtaal, geen links in de body. Noem 2-3 subreddits "
                "waar dit past. Dit is een concept voor menselijke review, wordt NIET "
                "automatisch gepost.",
            ),
        }

        now = _now()
        created: List[Dict] = []
        with get_conn() as conn:
            profile_ids = {
                name: (conn.execute("SELECT id FROM agent_profiles WHERE name = ?", (name,)).fetchone() or {"id": None})["id"]
                for name in {specs[c][0] for c in channels}
            }
            base_pos = (conn.execute("SELECT COALESCE(MAX(position), -1) FROM tasks").fetchone()[0] or 0) + 1
            rows = []
            for i, ch in enumerate(channels):
                profile_name, title, description = specs[ch]
                task_id = str(uuid.uuid4())
                rows.append((
                    task_id, title, description,
                    "ready" if i == 0 else "todo",
                    profile_name, profile_ids.get(profile_name),
                    base_pos + i, f"{base}/{i + 1:02d}-{ch}.md",
                    now, now,
                ))
                created.append({"id": task_id, "title": title, "channel": ch, "agent": profile_name})
            conn.executemany(
                """INSERT INTO tasks
                   (id, title, description, status, agent, assigned_agent_id,
                    position, workspace_path, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.execute(
                "UPDATE radar_signals SET status = 'converted', updated_at = ? WHERE id = ?",
                (now, signal_id),
            )

        # Geheugen-loop: het besluit om te targeten hoort in de vault.
        sig = self.get_signal(signal_id)
        if sig and not sig.get("obsidian_path"):
            self.write_trend_note(sig)

        return {"signal_id": signal_id, "workspace": base, "tasks": created}

    # ── NotebookLM-pakket ────────────────────────────────────────────────────

    async def build_notebooklm_package(self, signal_id: str) -> Dict:
        """NotebookLM heeft geen officiële publieke API. Dit genereert het
        volledige bronpakket dat je in NotebookLM (of een lokale TTS-workflow
        zoals ElevenLabs) plakt: brondocument, podcast-dialoogscript (2 hosts),
        infographic-outline en een shorts-script. Opgeslagen in de vault onder
        10_Projects/_trends/notebooklm/."""
        sig = self.get_signal(signal_id)
        if not sig:
            raise LookupError("Signaal niet gevonden")

        titles = sig.get("ai_titles") or []
        chosen_title = titles[0] if titles else sig["title"]
        system = (
            "Je bent een Nederlandstalige multimedia-contentproducent. Je maakt op basis van "
            "een trend-analyse een compleet NotebookLM-bronpakket in Markdown met exact deze "
            "vier secties (## koppen):\n"
            "## Brondocument — feitelijk, gestructureerd achtergronddocument (~400 woorden) "
            "dat als NotebookLM-source dient.\n"
            "## Podcast-dialoog — een audio-overview-script van ~600 woorden tussen twee hosts "
            "(HOST A / HOST B), natuurlijk gesprek, geen opsomming.\n"
            "## Infographic-outline — 5-7 visuele blokken (titel + 1 datapunt/boodschap per blok) "
            "voor Google Image Search.\n"
            "## Shorts-script — 45-60 seconden verticale video (hook in de eerste 2 seconden).\n"
            "Lever direct het pakket, geen meta-uitleg."
        )
        user_content = (
            f"Onderwerp/titel: {chosen_title}\nKeyword: {sig.get('keyword','')}\n"
            f"Hook: {sig.get('ai_hook','')}\nInvalshoek: {sig.get('ai_angle','')}\n"
            f"Bron-snippet:\n{(sig.get('snippet') or '')[:1500]}"
        )
        chunks: List[str] = []
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system,
            agent="hermes",
            use_tools=False,
            max_tokens=2000,
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
        content = "".join(chunks).strip()
        if not content:
            raise RuntimeError("LLM leverde geen NotebookLM-pakket (backend onbereikbaar?)")

        rel_path = None
        if OBSIDIAN_VAULT_PATH and Path(OBSIDIAN_VAULT_PATH).exists():
            vault = Path(OBSIDIAN_VAULT_PATH)
            note_dir = vault / TRENDS_VAULT_DIR / "notebooklm"
            note_dir.mkdir(parents=True, exist_ok=True)
            note_path = note_dir / f"{_slugify(chosen_title)}.md"
            frontmatter = (
                f"---\nkeyword: \"{sig.get('keyword','')}\"\nsource: {sig.get('url','')}\n"
                f"score: {sig.get('signal_score', 0)}\ncreated: {datetime.now().strftime('%Y-%m-%d')}\n---\n\n"
            )
            note_path.write_text(frontmatter + content + "\n", encoding="utf-8")
            rel_path = str(note_path.relative_to(vault))

        return {"signal_id": signal_id, "title": chosen_title,
                "obsidian_path": rel_path, "content": content}

    # ── Listicle → publicatie-wachtrij (sluit de AEO-loop) ───────────────────

    def _chosen_title(self, sig: Dict) -> str:
        """Zelfde titel-keuze als aeo_attack() — nodig om de workspace-basismap
        van een eerdere AEO-aanval te kunnen terugvinden."""
        titles = sig.get("ai_titles") or []
        return titles[0] if titles else (sig.get("ai_angle") or sig["title"])

    def _find_listicle_task(self, sig: Dict) -> Optional[Dict]:
        base = f"radar-aeo-{_slugify(self._chosen_title(sig))}"
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE workspace_path LIKE ? "
                "AND workspace_path LIKE '%-listicle.md' "
                "ORDER BY created_at DESC LIMIT 1",
                (f"{base}/%",),
            ).fetchone()
        return dict(row) if row else None

    def _resolve_site(self, sig: Dict, site_id: Optional[str]) -> Dict:
        """Bepaal naar welke site de listicle-job moet: expliciete site_id,
        anders match op het project van het signaal, anders de enige site."""
        from ..seo import sites as sites_service

        if site_id:
            site = sites_service.get_site(site_id)
            if not site:
                raise LookupError("Site niet gevonden")
            return site

        sites = [sites_service.get_site(s["id"]) for s in sites_service.list_sites()]
        sites = [s for s in sites if s]
        if not sites:
            raise ValueError("Nog geen sites geconfigureerd — voeg eerst een site toe in de SEO-tab.")

        def norm(x: str) -> str:
            return (x or "").lower().replace(" ", "").replace("-", "").replace("_", "")

        project = norm(sig.get("project") or "")
        if project:
            matches = [s for s in sites
                       if norm(s["name"]) == project
                       or project in norm(s["name"]) or norm(s["name"]) in project]
            if len(matches) == 1:
                return matches[0]
        if len(sites) == 1:
            return sites[0]
        raise ValueError(
            "Meerdere sites mogelijk — geef site_id mee. Beschikbaar: "
            + ", ".join(f"{s['name']} ({s['id']})" for s in sites)
        )

    async def queue_listicle(self, signal_id: str, site_id: Optional[str] = None) -> Dict:
        """Zet de afgeronde AEO-listicle van dit signaal als content_job in de
        publicatie-wachtrij (pending_review). Sluit de loop: radar-signaal →
        conveyor-concept → Wachtrij-tab → approve_and_publish(). Er wordt dus
        nog steeds niets gepubliceerd zonder menselijke goedkeuring."""
        sig = self.get_signal(signal_id)
        if not sig:
            raise LookupError("Signaal niet gevonden")
        task = self._find_listicle_task(sig)
        if not task:
            raise LookupError(
                "Geen listicle-taak voor dit signaal gevonden — start eerst een AEO-aanval."
            )
        # 'awaiting_approval' telt ook als klaar: de conveyor zet afgeronde taken
        # daarop, en deze actie is zelf al een menselijke klik — plus de wachtrij
        # heeft z'n eigen review-gate vóór publicatie.
        if task.get("status") not in ("done", "awaiting_approval") or not (task.get("result") or "").strip():
            raise RuntimeError(
                "De listicle-taak is nog niet klaar — wacht tot de Conveyor 'm heeft afgerond."
            )

        site = self._resolve_site(sig, site_id)

        from ..publish import content_pipeline
        keyword = sig.get("keyword") or sig["title"]
        rationale = sig.get("ai_hook") or (
            f"Mission Radar-signaal (score {round(sig.get('signal_score') or 0)})"
        )
        job_id = await content_pipeline.create_job_from_listicle(
            site, keyword, rationale, task["result"]
        )
        return {"signal_id": signal_id, "job_id": job_id, "site": site["name"],
                "task_id": task["id"]}

    # ── Infographic (PNG voor Google Images / social) ────────────────────────

    async def build_infographic(self, signal_id: str) -> Dict:
        """Genereer een kant-en-klare infographic-PNG (1080x1350): de LLM maakt
        5-7 blokken (kop + boodschap) over het signaal, Pillow rendert ze in de
        projectstijl. Opgeslagen in de vault (10_Projects/_trends/infographics/)
        en als base64 teruggegeven voor directe download in de UI."""
        sig = self.get_signal(signal_id)
        if not sig:
            raise LookupError("Signaal niet gevonden")

        chosen_title = self._chosen_title(sig)
        system = (
            "Je bent een Nederlandstalige infographic-ontwerper. Antwoord UITSLUITEND met JSON, "
            "geen markdown of uitleg:\n"
            '{"title": "infographic-titel (max 60 tekens)", "blocks": [{"heading": "korte kop '
            '(max 40 tekens)", "text": "één concrete boodschap of tip (max 90 tekens)"}]}\n'
            "Precies 5 tot 7 blocks. Feitelijk, B1-niveau, geen verzonnen cijfers of bronnen."
        )
        user_content = (
            f"Onderwerp/titel: {chosen_title}\nKeyword: {sig.get('keyword','')}\n"
            f"Hook: {sig.get('ai_hook','')}\nInvalshoek: {sig.get('ai_angle','')}\n"
            f"Bron-snippet:\n{(sig.get('snippet') or '')[:1200]}"
        )
        chunks: List[str] = []
        async for ev in agent_service.run_agent(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system,
            agent="hermes",
            use_tools=False,
            max_tokens=1200,
        ):
            if ev.get("type") == "text":
                chunks.append(ev["text"])
        raw = "".join(chunks).strip()
        try:
            obj = json.loads(_extract_json(raw))
            blocks = [b for b in (obj.get("blocks") or [])
                      if isinstance(b, dict) and (b.get("heading") or b.get("text"))][:7]
            info_title = str(obj.get("title") or chosen_title).strip()
        except Exception:
            raise RuntimeError(f"LLM leverde geen geldige infographic-JSON: {raw[:200]!r}")
        if len(blocks) < 3:
            raise RuntimeError("LLM leverde te weinig infographic-blokken (minimaal 3 nodig)")

        from ...shared.image_gen import generate_infographic
        png_bytes = generate_infographic(info_title, blocks, sig.get("project") or "")

        filename = f"{_slugify(info_title)}.png"
        rel_path = None
        if OBSIDIAN_VAULT_PATH and Path(OBSIDIAN_VAULT_PATH).exists():
            vault = Path(OBSIDIAN_VAULT_PATH)
            out_dir = vault / TRENDS_VAULT_DIR / "infographics"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / filename).write_bytes(png_bytes)
            rel_path = str((out_dir / filename).relative_to(vault))

        import base64
        return {"signal_id": signal_id, "title": info_title, "filename": filename,
                "blocks": len(blocks), "vault_path": rel_path,
                "png_base64": base64.b64encode(png_bytes).decode("ascii")}

    # ── Verdiepende scrape (on demand, niet tijdens de bulk-scan) ────────────

    def scrape_source(self, signal_id: str) -> Optional[Dict]:
        """Trek de volledige paginatekst van de bron leeg (hergebruikt de
        bestaande scraper) en werk de snippet bij — handig vóór een AEO-aanval."""
        sig = self.get_signal(signal_id)
        if not sig:
            return None
        text = scraper.scrape_text(sig["url"])
        if text:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE radar_signals SET snippet = ?, updated_at = ? WHERE id = ?",
                    (text[:2000], _now(), signal_id),
                )
        return self.get_signal(signal_id)


def _days_ago(published_date: Optional[str]) -> int:
    """Tavily's published_date ('Mon, 01 Jul 2026 08:00:00 GMT' of ISO) → dagen geleden."""
    if not published_date:
        return -1
    dt = None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published_date)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return -1
    if dt is None:
        return -1
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


_svc_singleton: Optional[RadarService] = None


def get_service() -> RadarService:
    global _svc_singleton
    if _svc_singleton is None:
        _svc_singleton = RadarService()
    return _svc_singleton


async def scan_the_skies() -> None:
    """Entry point voor de scheduler — consumeert run_scan() over ALLE projecten."""
    # Circuit-breaker: geen dure LLM-scans als de dagbudget op is.
    from ...shared.outcomes import require_llm_budget
    try:
        require_llm_budget("radar-sky")
    except Exception as e:
        log.warning("[radar] Sky-scan overgeslagen: %s", e)
        return
    svc = get_service()
    saved = 0
    async for ev in svc.run_scan():
        if ev.get("type") == "scan_done":
            saved = ev.get("total_saved", 0)
    log.info("[radar] Sky-scan klaar: %s nieuwe signalen", saved)
    if saved:
        from ...shared.outcomes import log_outcome
        log_outcome("Radar", "sky-scan",
                    f"{saved} nieuwe signalen (concurrenten/trends) opgepikt",
                    next_step="Scan de Radar-tab op signalen die actie verdienen")
        # Trend-brug: topsignalen direct als Demand Engine-kans klaarzetten,
        # zodat de contentpijplijn ze meepakt zonder handmatige stap. Faalt
        # zacht — de sky-scan zelf is dan al geslaagd.
        try:
            from ..seo.trends import sync_all_trend_opportunities
            sync_all_trend_opportunities()
        except Exception:
            log.exception("[radar] Trend-sync naar Demand Engine mislukt")
