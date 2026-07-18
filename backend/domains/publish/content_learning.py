"""Content-leerlus — welke artikel-kenmerken leveren aantoonbaar GSC-clicks op.

De schrijvers publiceren al maanden artikelen en `gsc_history` (scope='page')
meet al hoe elke pagina presteert; deze module sluit die lus. Elk gepubliceerd
artikel wordt achteraf ingedeeld op observeerbare kenmerken (lengte, titelvorm,
titelintentie) en per kenmerk-cohort vergelijken we de gemeten clicks. Duidelijke
verschillen worden een les die via `lessons_block("content")` terugstroomt in de
schrijf-prompts (naast Iris' kennisbank-principes). Niet te verwarren met
`publish/learning.py` (onder-de-grens-lessen naar de vault): dát leert van
kwaliteitsscores vóór publicatie, dít van gemeten prestaties erná.

Eerlijkheids-kanttekening: dit zijn observationele cohorten, geen gerandomiseerd
experiment zoals de outreach-varianten — een verschil kán door het onderwerp
komen i.p.v. de vorm. Daarom krijgt elke les een falsifieerbare voorspelling
("de kloof blijft staan nu er nieuwe artikelen bijkomen"): houdt het patroon
geen stand, dan daalt het vertrouwen en wordt de les vanzelf ingetrokken.
Evaluatie is puur regelgebaseerd (medianen uit gsc_history), nooit een LLM-oordeel.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared import learning
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

AGENT = "content"

DIMENSIONS: Dict[str, tuple[str, str]] = {
    "lengte": ("uitgebreid", "compact"),          # >= / < _LONG_WORDS woorden
    "titelvorm": ("lijst", "regulier"),           # getal in de titel (listicle) of niet
    "titelintentie": ("vraag", "statement"),      # vraag-titel (AEO) of bewering
}
_LONG_WORDS = 1200
_QUESTION_RE = re.compile(r"^\s*(hoe|wat|waarom|welke|wanneer|waar|wie)\b", re.IGNORECASE)

# Drempels. Een artikel telt pas mee als het lang genoeg live staat om een
# volledig 28-daags GSC-venster te hebben (de page-snapshots zíjn dat venster).
RIPEN_DAYS = 28
MIN_ARTICLES_PER_VALUE = 5
# Les-drempel op de mediaan-kloof: winnaar minstens 1.5× de verliezer
# (+1 aan beide kanten zodat een 0-mediaan geen deling door nul of een
# oneindige ratio geeft) én zelf boven de ruis.
MIN_RATIO = 1.5
MIN_WINNER_MEDIAN = 3.0
PREDICTION_HORIZON_DAYS = 28
PREDICTION_KEEP_RATIO = 1.2


def article_dimensions(title: str, blog_html: str) -> Dict[str, str]:
    """Deel één artikel in op observeerbare vorm-kenmerken (deterministisch)."""
    plain = re.sub(r"<[^>]+>", " ", blog_html or "")
    words = len(plain.split())
    title = (title or "").strip()
    is_question = title.endswith("?") or bool(_QUESTION_RE.match(title))
    return {
        "lengte": "uitgebreid" if words >= _LONG_WORDS else "compact",
        "titelvorm": "lijst" if re.search(r"\d", title) else "regulier",
        "titelintentie": "vraag" if is_question else "statement",
    }


# ── Meting ─────────────────────────────────────────────────────────────────

def _ripe_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RIPEN_DAYS)).isoformat()


def _page_clicks(conn, site_id: str, slug: str) -> Optional[float]:
    """Trailing-28d clicks van dit artikel: de nieuwste page-snapshot.

    Slug-match is bewust padgrens-strak ('%/slug' of '%/slug/…') zodat 'ai'
    nooit op 'ai-zorg' matcht. Heeft de site wél page-historie maar deze
    pagina niet, dan is dat een echte nul (GSC laat 0-impressie-pagina's weg)."""
    row = conn.execute(
        "SELECT clicks FROM gsc_history WHERE site_id = ? AND scope = 'page' "
        "AND (page_url LIKE ? OR page_url LIKE ? OR page_url LIKE ?) "
        "ORDER BY date DESC LIMIT 1",
        (site_id, f"%/{slug}", f"%/{slug}/", f"%/{slug}?%"),
    ).fetchone()
    if row:
        return float(row["clicks"])
    has_history = conn.execute(
        "SELECT 1 FROM gsc_history WHERE site_id = ? AND scope = 'page' LIMIT 1",
        (site_id,),
    ).fetchone()
    return 0.0 if has_history else None


def cohort_stats() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per dimensie per waarde: aantal artikelen + mediaan trailing-28d clicks,
    over alle gerijpte gepubliceerde artikelen met meetbare GSC."""
    with get_conn() as conn:
        jobs = conn.execute(
            "SELECT site_id, title, blog_html, slug FROM content_jobs "
            "WHERE status = 'published' AND slug != '' "
            "AND reviewed_at != '' AND reviewed_at <= ?",
            (_ripe_cutoff(),),
        ).fetchall()
        samples: Dict[str, Dict[str, List[float]]] = {
            dim: {v: [] for v in values} for dim, values in DIMENSIONS.items()
        }
        for job in jobs:
            clicks = _page_clicks(conn, job["site_id"], job["slug"])
            if clicks is None:
                continue  # site zonder GSC-page-historie: niet meetbaar
            dims = article_dimensions(job["title"], job["blog_html"])
            for dim, value in dims.items():
                samples[dim][value].append(clicks)
    return {
        dim: {
            value: {
                "n": len(clicks_list),
                "median_clicks": round(statistics.median(clicks_list), 1) if clicks_list else None,
            }
            for value, clicks_list in per_value.items()
        }
        for dim, per_value in samples.items()
    }


def _ratio(dim: str, winner: str, loser: str) -> Optional[float]:
    """Actuele mediaan-clicks-ratio (winnaar+1)/(verliezer+1); None onder de
    minimum-steekproef — dan is de kloof niet eerlijk te meten."""
    stats = cohort_stats().get(dim, {})
    w, l = stats.get(winner), stats.get(loser)
    if not w or not l or w["n"] < MIN_ARTICLES_PER_VALUE or l["n"] < MIN_ARTICLES_PER_VALUE:
        return None
    return round((w["median_clicks"] + 1) / (l["median_clicks"] + 1), 2)


def _resolver(metric: str, context: str) -> Optional[float]:
    """Resolver voor het leer-raamwerk: 'median_clicks_ratio' met context
    'dim:winnaar>verliezer' → de actuele ratio."""
    if metric != "median_clicks_ratio" or ">" not in context or ":" not in context:
        return None
    dim, _, pair = context.partition(":")
    winner, _, loser = pair.partition(">")
    if dim not in DIMENSIONS:
        return None
    return _ratio(dim, winner, loser)


# ── Evaluatie (wekelijkse job) ─────────────────────────────────────────────

def run_content_learning_eval() -> Dict[str, Any]:
    """Wekelijks: (1) reken vervallen voorspellingen af tegen de actuele
    GSC-cohortcijfers, (2) destilleer nieuwe/bevestigde vorm-lessen, elk met
    een verse toetsbare voorspelling. Meldt zich alleen in het Actiecentrum
    als er echt iets gebeurd is — een wekelijkse "nog niets te leren"-kaart
    is ruis."""
    verdict = learning.evaluate_due(AGENT, _resolver)
    stats = cohort_stats()
    new_lessons: List[str] = []

    for dim, values in DIMENSIONS.items():
        v1, v2 = values
        s1, s2 = stats[dim][v1], stats[dim][v2]
        if s1["n"] < MIN_ARTICLES_PER_VALUE or s2["n"] < MIN_ARTICLES_PER_VALUE:
            continue
        ratio = (s1["median_clicks"] + 1) / (s2["median_clicks"] + 1)
        if ratio >= 1:
            winner, loser, w, l = v1, v2, s1, s2
        else:
            winner, loser, w, l = v2, v1, s2, s1
            ratio = 1 / ratio
        if ratio < MIN_RATIO or w["median_clicks"] < MIN_WINNER_MEDIAN:
            continue
        # Stabiele les-tekst (dedupe!); de wisselende cijfers gaan in evidence.
        lesson_text = (f"Artikelen met {dim} '{winner}' halen meer GSC-clicks "
                       f"dan '{loser}'.")
        lesson_id = learning.upsert_lesson(
            AGENT, lesson_text, category="content-vorm",
            evidence={
                "dimensie": dim,
                winner: {"artikelen": w["n"], "mediaan_clicks_28d": w["median_clicks"]},
                loser: {"artikelen": l["n"], "mediaan_clicks_28d": l["median_clicks"]},
                "ratio": round(ratio, 2),
                "kanttekening": "observationeel cohort — de voorspelling toetst of het patroon standhoudt",
            },
        )
        if lesson_id:
            new_lessons.append(lesson_text)
            learning.record_prediction(
                AGENT,
                metric="median_clicks_ratio",
                context=f"{dim}:{winner}>{loser}",
                direction="up",
                comparison="threshold",
                baseline=round(ratio, 2),
                target=PREDICTION_KEEP_RATIO,
                horizon_days=PREDICTION_HORIZON_DAYS,
                lesson_id=lesson_id,
                statement=(f"Over {PREDICTION_HORIZON_DAYS} dagen halen artikelen met "
                           f"{dim}='{winner}' nog ≥ {PREDICTION_KEEP_RATIO:g}× de mediaan-clicks "
                           f"van '{loser}'."),
            )

    happened = bool(new_lessons or verdict["evaluated"])
    if happened:
        parts = []
        if verdict["evaluated"]:
            parts.append(f"{verdict['correct']} voorspelling(en) correct, "
                         f"{verdict['wrong']} fout, {verdict['unclear']} onduidelijk")
        if new_lessons:
            parts.append(f"{len(new_lessons)} les(sen) vastgelegd/bevestigd")
        log_outcome(
            "Content", "content_leerlus",
            "Content-leerlus: " + " · ".join(parts),
            artifact="/api/learning/content",
            next_step="Niets — de geleerde vorm-lessen stromen automatisch in de volgende artikelen.",
        )
    logger.info("[content-leerlus] evaluatie klaar: %d afgerekend, %d les(sen)",
                len(verdict["evaluated"]), len(new_lessons))
    return {"verdict": verdict, "lessons": new_lessons, "stats": stats}
