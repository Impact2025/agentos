"""
Weekrapport-bevindingen bewaren en teruggeven aan het systeem.

Waarom dit bestaat (4 aug 2026). Het weekrapport (`analytics/reporter.py`)
berekende elke maandag per project de zoekprestaties over 28 dagen tegen de 28
daarvóór, mét afgeleide bevindingen: quick wins (positie 4-15 met volume),
CTR-gaten, stijgers en dalers. Dat rapport ging naar de mail, naar Obsidian en
naar een chat-sessie — drie plekken waar alléén een mens kijkt. Geen enkele
agent kon het lezen. Het antwoord op "gebruikt het systeem dit voor betere
suggesties, en leert Iris ervan?" was dus: nee, en nee. Een analyse die nergens
in terugkomt is een mening, geen mechanisme; dat is dezelfde fout als een taak
die "voltooid" heet zonder artefact.

Wat dit toevoegt bovenop `seo/history.py` — want die twee lijken op elkaar en
verwarring hier levert dubbele waarheden op:

  * `gsc_history` bewaart **dagcijfers** en levert het snelle beeld: 7 dagen
    tegen de 7 daarvóór (`site_trend`). Goed voor "wat gebeurde er deze week",
    gevoelig voor ruis (één feestdag kantelt de delta).
  * `weekly_insights` bewaart het **trage beeld**: 28 tegen 28. Een project dat
    7-op-7 daalt maar 28-op-28 stijgt heeft geen probleem, en zonder beide
    horizons naast elkaar stuurt Iris op ruis. Daarnaast bewaart het de
    bevindingen zelf, en dát maakt zichtbaar dat dezelfde quick win drie weken
    op rij blijft liggen — uit dagcijfers is dat niet af te leiden.

De functies hier zijn deterministisch en faalveilig: geen LLM (een blok dat de
gateway nodig heeft ontbreekt precies op de dag dat je het nodig hebt) en bij
een lege tabel een expliciete "geen weekrapport"-melding in plaats van stilte —
Iris leest een leeg blok als "alles in orde", en dat is de duurste leugen die
dit bestand kan vertellen.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# Een project heet 'structureel dalend' als het over 28 dagen zowel volume als
# positie verliest. Twee signalen, niet één: alleen minder klikken kan seizoen
# zijn, alleen een lagere positie kan een nieuw zoekwoord met veel impressies
# zijn. Samen is het een echte achteruitgang.
_DAAL_KLIK_PCT = -20.0
_DAAL_POSITIE = -1.0   # positie-delta: negatief = verder weggezakt


def week_label(d: Optional[date] = None) -> str:
    iso = (d or date.today()).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ── Schrijven ──────────────────────────────────────────────────────────────

def store_week(analyses: List[Dict[str, Any]], label: Optional[str] = None) -> int:
    """Leg de per-project analyses van deze week vast. Idempotent per (week, site).

    Een herrun van het weekrapport hoort de rij te overschrijven, niet te
    verdubbelen — anders telt 'drie weken blijven liggen' de herruns mee.
    """
    label = label or week_label()
    rows = 0
    with get_conn() as conn:
        for a in analyses:
            site_id = str(a.get("site_id") or "")
            if not site_id:
                # Zonder stabiele sleutel kunnen we deze week niet aan de vorige
                # knopen; dan is opslaan erger dan overslaan.
                logger.warning("[insights] analyse zonder site_id overgeslagen: %s", a.get("name"))
                continue
            agg, comp = a.get("aggregate") or {}, a.get("comparison") or {}
            conn.execute(
                """INSERT INTO weekly_insights
                   (id, week_label, site_id, project, gsc_property,
                    clicks, impressions, ctr, position,
                    clicks_prev, impressions_prev, position_prev,
                    quick_wins, ctr_fix, risers, fallers, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(week_label, site_id) DO UPDATE SET
                     project=excluded.project, gsc_property=excluded.gsc_property,
                     clicks=excluded.clicks, impressions=excluded.impressions,
                     ctr=excluded.ctr, position=excluded.position,
                     clicks_prev=excluded.clicks_prev,
                     impressions_prev=excluded.impressions_prev,
                     position_prev=excluded.position_prev,
                     quick_wins=excluded.quick_wins, ctr_fix=excluded.ctr_fix,
                     risers=excluded.risers, fallers=excluded.fallers,
                     created_at=excluded.created_at""",
                (
                    str(uuid.uuid4()), label, site_id, a.get("name") or site_id,
                    a.get("property") or "",
                    agg.get("clicks", 0), agg.get("impressions", 0),
                    agg.get("ctr", 0.0), agg.get("position", 0.0),
                    (comp.get("clicks") or {}).get("prev", 0),
                    (comp.get("impressions") or {}).get("prev", 0),
                    (comp.get("position") or {}).get("prev", 0.0),
                    json.dumps(a.get("quick_wins") or [], ensure_ascii=False),
                    json.dumps(a.get("ctr_fix") or [], ensure_ascii=False),
                    json.dumps(a.get("risers") or [], ensure_ascii=False),
                    json.dumps(a.get("fallers") or [], ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            rows += 1
    return rows


# ── Lezen ──────────────────────────────────────────────────────────────────

def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for veld in ("quick_wins", "ctr_fix", "risers", "fallers"):
        try:
            d[veld] = json.loads(d.get(veld) or "[]")
        except (ValueError, TypeError):
            d[veld] = []
    prev_c = d.get("clicks_prev") or 0
    prev_i = d.get("impressions_prev") or 0
    d["clicks_pct"] = round((d["clicks"] - prev_c) / prev_c * 100, 1) if prev_c else None
    d["impressions_pct"] = round((d["impressions"] - prev_i) / prev_i * 100, 1) if prev_i else None
    # Positie: lager is beter, dus een positieve delta betekent winst.
    d["position_delta"] = round((d.get("position_prev") or 0) - (d.get("position") or 0), 1) \
        if d.get("position_prev") else None
    return d


def latest_label() -> Optional[str]:
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT week_label FROM weekly_insights ORDER BY week_label DESC LIMIT 1"
            ).fetchone()
        return r["week_label"] if r else None
    except Exception:  # noqa: BLE001 — een ontbrekende tabel velt geen briefing
        logger.exception("[insights] laatste week ophalen mislukt")
        return None


def portfolio(label: Optional[str] = None) -> List[Dict[str, Any]]:
    """De per-project bevindingen van één week (default: de laatst opgeslagen)."""
    label = label or latest_label()
    if not label:
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM weekly_insights WHERE week_label = ? "
                "ORDER BY impressions DESC", (label,)
            ).fetchall()
        return [_row(r) for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("[insights] portfolio ophalen mislukt")
        return []


def _weken_oud(label: str) -> Optional[int]:
    try:
        jaar, week = label.split("-W")
        maandag = date.fromisocalendar(int(jaar), int(week), 1)
    except (ValueError, TypeError):
        return None
    return max(0, (date.today() - maandag).days // 7)


def summary() -> Dict[str, Any]:
    """Compact weekbeeld voor Iris' cijfers en voor de UI.

    `state` is expliciet: 'ok' | 'geen' (nog nooit een rapport) | 'verouderd'.
    "Geen weekrapport" en "een rustige week" zien er in cijfers hetzelfde uit en
    zijn tegenovergestelde situaties.
    """
    label = latest_label()
    if not label:
        return {"state": "geen", "week": None, "projects": [], "structureel_dalend": []}
    rijen = portfolio(label)
    oud = _weken_oud(label)
    projects = [{
        "site_id": r["site_id"], "project": r["project"],
        "clicks": r["clicks"], "impressions": r["impressions"],
        "ctr": r["ctr"], "position": r["position"],
        "clicks_pct": r["clicks_pct"], "impressions_pct": r["impressions_pct"],
        "position_delta": r["position_delta"],
        "quick_wins": len(r["quick_wins"]), "ctr_fix": len(r["ctr_fix"]),
        "fallers": len(r["fallers"]),
        "top_quick_win": (r["quick_wins"] or [None])[0],
    } for r in rijen]
    return {
        "state": "verouderd" if (oud or 0) >= 2 else "ok",
        "week": label,
        "weken_oud": oud,
        "projects": projects,
        "structureel_dalend": [p["project"] for p in _dalers(rijen)],
    }


def _dalers(rijen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    uit = []
    for r in rijen:
        klik = r.get("clicks_pct")
        pos = r.get("position_delta")
        if klik is None or pos is None:
            continue  # geen vergelijkingsperiode = geen oordeel
        if klik <= _DAAL_KLIK_PCT and pos <= _DAAL_POSITIE:
            uit.append(r)
    return sorted(uit, key=lambda r: r["impressions"], reverse=True)


def structural_decliners() -> List[Dict[str, Any]]:
    """Projecten die over 28 dagen zowel volume als positie verliezen."""
    return _dalers(portfolio())


def stale_quick_wins(min_weken: int = 3) -> List[Dict[str, Any]]:
    """Quick wins die al `min_weken` weken op rij in het rapport staan.

    Dit is de meetlat voor "het rapport verandert niets": een zoekwoord dat drie
    maandagen achter elkaar als kans wordt aangeboden en nog steeds op dezelfde
    positie staat, is een advies dat niemand oppakt.
    """
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT week_label, site_id, project, quick_wins FROM weekly_insights "
                "ORDER BY week_label DESC LIMIT 200"
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("[insights] quick wins ophalen mislukt")
        return []

    weken = sorted({r["week_label"] for r in rows}, reverse=True)[:min_weken]
    if len(weken) < min_weken:
        return []  # nog niet genoeg historie om iets te beweren

    per_query: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        if r["week_label"] not in weken:
            continue
        try:
            qw = json.loads(r["quick_wins"] or "[]")
        except (ValueError, TypeError):
            qw = []
        for q in qw:
            sleutel = (r["site_id"], str(q.get("query") or "").lower())
            item = per_query.setdefault(sleutel, {
                "site_id": r["site_id"], "project": r["project"],
                "query": q.get("query"), "weken": set(), "posities": {},
            })
            item["weken"].add(r["week_label"])
            item["posities"][r["week_label"]] = q.get("position")

    uit = []
    for item in per_query.values():
        if len(item["weken"]) < min_weken:
            continue
        posities = [item["posities"][w] for w in sorted(item["posities"]) if item["posities"].get(w)]
        if len(posities) >= 2 and posities[-1] < posities[0] - 1.0:
            continue  # beweegt de goede kant op: dan wórdt er iets gedaan
        uit.append({
            "site_id": item["site_id"], "project": item["project"],
            "query": item["query"], "weken": len(item["weken"]),
            "positie": posities[-1] if posities else None,
        })
    return sorted(uit, key=lambda x: x["weken"], reverse=True)


# ── Voor Iris' prompt ──────────────────────────────────────────────────────

def prompt_block() -> str:
    """Het weekbeeld als tekst, met de horizon er expliciet bij.

    Iris krijgt uit `gsc_history` het 7-vs-7-beeld. Dit blok is het 28-vs-28
    beeld. Ze moet weten dat het twee horizonnen op dezelfde werkelijkheid zijn,
    anders leest ze een tegenspraak waar een tijdschaalverschil staat.
    """
    s = summary()
    if s["state"] == "geen":
        return ("Weekrapport: nog nooit opgeslagen. Er is dus géén 28-daags beeld — "
                "trek uit deze afwezigheid geen enkele conclusie over de prestaties.")
    regels = [f"Weekrapport {s['week']} (28 dagen vs. de 28 daarvóór)."]
    if s["state"] == "verouderd":
        regels.append(f"LET OP: dit rapport is {s['weken_oud']} weken oud — de maandagrun "
                      "heeft sindsdien niet gedraaid. Behandel het als achtergrond, "
                      "niet als de stand van vandaag.")
    for p in s["projects"]:
        def _p(v, suffix="%"):
            return "n/b" if v is None else f"{v:+.1f}{suffix}"
        regel = (f"- {p['project']}: {p['clicks']} klikken ({_p(p['clicks_pct'])}), "
                 f"{p['impressions']} impressies ({_p(p['impressions_pct'])}), "
                 f"positie {p['position']} ({_p(p['position_delta'], '')} = winst als positief)")
        extra = []
        if p["quick_wins"]:
            qw = p["top_quick_win"] or {}
            extra.append(f"{p['quick_wins']} quick win(s), grootste: "
                         f"'{qw.get('query')}' op positie {qw.get('position')} "
                         f"met {qw.get('impressions')} impressies")
        if p["ctr_fix"]:
            extra.append(f"{p['ctr_fix']} zoekwoord(en) met veel vertoningen en <2% CTR "
                         "(snippet-probleem, geen contentprobleem)")
        if p["fallers"]:
            extra.append(f"{p['fallers']} daler(s) in positie")
        if extra:
            regel += " — " + "; ".join(extra)
        regels.append(regel)
    dalers = s["structureel_dalend"]
    if dalers:
        regels.append("Structureel dalend over 28 dagen (volume én positie): "
                      + ", ".join(dalers)
                      + ". Dit is geen weekruis; hier is een interventie op zijn plaats.")
    blijvers = stale_quick_wins()
    if blijvers:
        regels.append("Deze kansen staan al weken onopgepakt in het rapport: "
                      + "; ".join(f"'{b['query']}' ({b['project']}, {b['weken']} weken)"
                                  for b in blijvers[:5])
                      + ". Een advies dat zich herhaalt zonder dat er iets verandert, "
                        "is geen advies meer — pak het op of leg uit waarom niet.")
    return "\n".join(regels)
