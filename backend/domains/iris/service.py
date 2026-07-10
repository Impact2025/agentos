"""Iris — de manager-agent van Agent OS.

Elke ochtend (06:45, vóór het ochtendrapport) doet Iris drie dingen:
1. ANALYSEREN — de harde cijfers per project (metrics.snapshot) plus wat er
   gisteren gebeurde, vergeleken met haar vorige briefing (leer-loop).
2. VERBETEREN — binnen een strikte whitelist stuurt ze de andere agents bij
   én zet ze ze aan het werk: batch-groottes van de contentmotor, maximaal
   één concept-doel per dag (blijft 'draft' — Vincent keurt het in het
   Actiecentrum goed), en de uitvoer-acties uit actions.py (content-run,
   outreach-batch, SEO-refresh — resultaat landt altijd achter een
   review-gate). Ze publiceert of verstuurt NOOIT zelf iets; de
   Wachtrij-gate blijft heilig.
3. RAPPORTEREN — een dagbriefing met rapportcijfers per project, wat ze
   geleerd heeft, wat ze verbeterd heeft en het beste advies voor vandaag.

Haar geheugen zit in twee tabellen: iris_reports (één briefing per dag) en
iris_lessons (lessen die over dagen heen meewegen; vaker bevestigd = zwaarder).
Zonder LLM valt ze terug op een puur cijfermatige briefing — nooit stil.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import metrics

logger = logging.getLogger(__name__)

_TZ = pytz.timezone("Europe/Amsterdam")

# Whitelist-grenzen voor zelfstandige bijsturing.
_BATCH_MIN, _BATCH_MAX = 1, 5
_MAX_NEW_GOALS_PER_RUN = 1
# Uitvoer-acties (actions.py): Iris mag agents aan het werk zetten, maar
# gedoseerd — de Wachtrij moet voor Vincent bij te benen blijven.
_MAX_CONTENT_RUNS_PER_RUN = 2
_MAX_OUTREACH_RUNS_PER_RUN = 1
_MAX_SEO_REFRESH_PER_RUN = 1
_MAX_ACTIVE_LESSONS_IN_PROMPT = 20

# De analyse-JSON is fors (oordeel + advies + voorspellingen); een wispelturig
# backend kapt bij een krappe limiet halverwege af en dan is de hele analyse
# weg. Ruim nemen en opnieuw proberen is goedkoper dan een dag zonder manager.
_LLM_MAX_TOKENS = 6000
_LLM_ATTEMPTS = 3

_IRIS_SYSTEM = (
    "Je bent Iris, de manager van Agent OS — een autonoom AI-systeem dat voor "
    "Vincent content schrijft, SEO doet, leads werft en doelen uitvoert. Je bent "
    "een wereldklasse SEO-expert en een nuchtere Nederlandse operationeel manager. "
    "Je bent geen adviseur maar een uitvoerder: wat agents kunnen doen zet je zelf "
    "in gang (binnen je whitelist; alles landt achter de review-gate), en alleen "
    "wat écht een mens vergt leg je bij Vincent neer. "
    "Je oordeelt op harde cijfers, niet op goede bedoelingen. 'Goed genoeg' bestaat "
    "niet: alles onder wereldklasse benoem je, met de concrete oorzaak en de fix. "
    "Je leert elke dag: je toetst je eerdere lessen en adviezen aan wat er "
    "daadwerkelijk gebeurde. Je verzint nooit cijfers — alles wat je beweert moet "
    "uit de aangeleverde data volgen. Antwoord uitsluitend met geldige JSON."
)


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


# ── LLM-laag (Claude eerst, Hermes-terugval — zelfde patroon als de pipeline) ──

async def _llm(system: str, prompt: str, max_tokens: int = 3000) -> str:
    from ..chat import claude as claude_service
    if claude_service.is_configured():
        try:
            out = (await claude_service.get_response(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=max_tokens,
            )).strip()
            if out:
                return out
            logger.warning("[iris] Claude gaf een lege respons — terugval op Hermes")
        except Exception as e:
            logger.warning("[iris] Claude niet beschikbaar (%s) — terugval op Hermes", e)
    try:
        from ..chat import hermes as hermes_service
        full = ""
        async for chunk in hermes_service.stream_response(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system,
            max_tokens=max_tokens,
        ):
            full += chunk
        if not full.strip():
            logger.warning("[iris] Hermes gaf een lege respons")
        return full.strip()
    except Exception as e:
        logger.warning("[iris] Hermes ook niet beschikbaar: %s", e)
        return ""


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


async def _ask_iris(prompt: str) -> Optional[Dict[str, Any]]:
    """Vraag de analyse op, met retries over de hele keten.

    Het backend achter Hermes is wispelturig (soms een lege respons, soms
    JSON die halverwege afkapt). Eén mislukte poging mag de briefing niet
    naar de cijfermatige terugval duwen zolang een verse poging het wél haalt.
    """
    for attempt in range(1, _LLM_ATTEMPTS + 1):
        raw = await _llm(_IRIS_SYSTEM, prompt, max_tokens=_LLM_MAX_TOKENS)
        if not raw:
            logger.warning("[iris] Lege LLM-respons (poging %d/%d)", attempt, _LLM_ATTEMPTS)
            continue
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed
        logger.warning("[iris] LLM-antwoord geen geldige JSON (poging %d/%d, %d tekens)",
                       attempt, _LLM_ATTEMPTS, len(raw))
    return None


# ── Geheugen: lessen en eerdere rapporten ────────────────────────────────────

def active_lessons(limit: int = _MAX_ACTIVE_LESSONS_IN_PROMPT) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, lesson, category, times_confirmed, predictions_made, "
            "predictions_correct, confidence FROM iris_lessons WHERE active = 1 "
            "ORDER BY confidence DESC, times_confirmed DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _upsert_lessons(lessons: List[Dict[str, Any]]) -> Dict[str, str]:
    """Nieuwe lessen opslaan; bestaande (zelfde tekst) zwaarder laten wegen.

    Retourneert een map {genormaliseerde lestekst: lesson_id} zodat
    voorspellingen aan de juiste les gekoppeld kunnen worden.
    """
    ids: Dict[str, str] = {}
    now = _now_iso()
    with get_conn() as conn:
        for item in lessons[:10]:
            text = (item.get("les") or item.get("lesson") or "").strip()
            if not text or len(text) < 10:
                continue
            row = conn.execute(
                "SELECT id FROM iris_lessons WHERE lower(lesson) = lower(?)", (text,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE iris_lessons SET times_confirmed = times_confirmed + 1, "
                    "updated_at = ? WHERE id = ?", (now, row["id"]),
                )
                ids[text.lower()] = row["id"]
            else:
                lid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO iris_lessons (id, lesson, category, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (lid, text, (item.get("categorie") or item.get("category") or "")[:40],
                     "dagbriefing", now, now),
                )
                ids[text.lower()] = lid
    return ids


def latest_report() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM iris_reports ORDER BY report_date DESC, created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    rec = dict(row)
    for key in ("grades", "learned", "improvements", "advice", "metrics"):
        try:
            rec[key] = json.loads(rec.get(key) or "null")
        except json.JSONDecodeError:
            rec[key] = None
    return rec


def report_history(limit: int = 14) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, report_date, grades, learned, improvements, advice, created_at "
            "FROM iris_reports ORDER BY report_date DESC LIMIT ?", (limit,),
        ).fetchall()
    out = []
    for r in rows:
        rec = dict(r)
        for key in ("grades", "learned", "improvements", "advice"):
            try:
                rec[key] = json.loads(rec.get(key) or "null")
            except json.JSONDecodeError:
                rec[key] = None
        out.append(rec)
    return out


def _store_report(report_date: str, markdown: str, grades: Dict, learned: List,
                  improvements: List, advice: List, metrics_snapshot: Dict) -> str:
    """Eén briefing per dag: een nieuwe run op dezelfde dag vervangt de oude."""
    report_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("DELETE FROM iris_reports WHERE report_date = ?", (report_date,))
        conn.execute(
            "INSERT INTO iris_reports (id, report_date, markdown, grades, learned, "
            "improvements, advice, metrics, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, report_date, markdown,
             json.dumps(grades, ensure_ascii=False),
             json.dumps(learned, ensure_ascii=False),
             json.dumps(improvements, ensure_ascii=False),
             json.dumps(advice, ensure_ascii=False),
             json.dumps(metrics_snapshot, ensure_ascii=False),
             _now_iso()),
        )
    return report_id


# ── Zelfstandige bijsturing (strikte whitelist) ─────────────────────────────

def _apply_batch_size(site_id: str, value: Any, reason: str) -> Optional[str]:
    try:
        n = max(_BATCH_MIN, min(_BATCH_MAX, int(value)))
    except (TypeError, ValueError):
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, content_batch_size FROM sites WHERE id = ?", (site_id,)
        ).fetchone()
        if not row or row["content_batch_size"] == n:
            return None
        conn.execute("UPDATE sites SET content_batch_size = ? WHERE id = ?", (n, site_id))
    detail = f"Contentmotor {row['name']}: batch {row['content_batch_size']} → {n}. Reden: {reason}"
    log_outcome(row["name"], "iris_bijsturing", detail, artifact="/api/iris/briefing")
    return detail


async def _apply_draft_goal(project: str, title: str, objective: str, reason: str) -> Optional[str]:
    """Concept-doel aanmaken — blijft 'draft', wacht in het Actiecentrum op akkoord."""
    if not title or not objective:
        return None
    try:
        from ..goal import service as goal_service
        plan = await goal_service.create_and_plan(
            title=f"[Iris] {title}"[:120], objective=objective, project=project or "WeAreImpact",
        )
        gid = plan.get("goal_id") if isinstance(plan, dict) else None
        if not gid:
            return None
        detail = f"Concept-doel voorgesteld voor {project}: {title}. Reden: {reason}"
        log_outcome(project or "Agent OS", "iris_bijsturing", detail,
                    artifact=f"/api/goals/{gid}",
                    next_step="Beoordeel Iris' concept-doel in het Actiecentrum")
        return detail
    except Exception as e:
        logger.warning("[iris] Concept-doel aanmaken mislukt: %s", e)
        return None


async def _apply_improvements(improvements: List[Dict[str, Any]]) -> List[str]:
    """Voer alleen whitelisted verbeteringen uit; alles wordt gelogd."""
    from . import actions
    applied: List[str] = []
    goals_created = content_runs = outreach_runs = seo_refreshes = 0
    for imp in improvements[:8]:
        kind = (imp.get("type") or "").strip().lower()
        reason = (imp.get("reden") or imp.get("reason") or "").strip()[:300]
        done: Optional[str] = None
        if kind == "batch_size":
            done = _apply_batch_size(imp.get("site_id", ""), imp.get("waarde") or imp.get("value"), reason)
        elif kind == "doel" and goals_created < _MAX_NEW_GOALS_PER_RUN:
            done = await _apply_draft_goal(
                imp.get("project", ""), (imp.get("titel") or imp.get("title") or "").strip(),
                (imp.get("doelstelling") or imp.get("objective") or "").strip(), reason,
            )
            goals_created += 1 if done else 0
        elif kind == "content_run" and content_runs < _MAX_CONTENT_RUNS_PER_RUN:
            done = await actions.content_run(
                imp.get("site_id") or imp.get("project") or "",
                imp.get("aantal") or imp.get("waarde") or imp.get("count"), reason,
            )
            content_runs += 1 if done else 0
        elif kind == "outreach_run" and outreach_runs < _MAX_OUTREACH_RUNS_PER_RUN:
            done = await actions.outreach_run(
                imp.get("aantal") or imp.get("waarde") or imp.get("count"), reason,
            )
            outreach_runs += 1 if done else 0
        elif kind == "seo_refresh" and seo_refreshes < _MAX_SEO_REFRESH_PER_RUN:
            done = await actions.seo_refresh(
                imp.get("site_id") or imp.get("project") or "",
                imp.get("aantal") or imp.get("waarde") or imp.get("count"), reason,
            )
            seo_refreshes += 1 if done else 0
        # 'aanbeveling' en onbekende types worden niet uitgevoerd — die blijven
        # advies aan Vincent, geen zelfstandige actie.
        if done:
            applied.append(done)
    return applied


# ── Context verzamelen en prompt bouwen ─────────────────────────────────────

def _yesterday_activity(limit: int = 40) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT project, action, detail, status, created_at FROM activity_log "
            "WHERE created_at > datetime('now', '-1 day') ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def gather_context(snapshot: Optional[Dict[str, Any]] = None,
                   validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bouw de context voor de prompt. Roep dit ná evaluate_due aan, zodat de
    lessen hun bijgewerkte vertrouwen en de ingetrokken lessen weerspiegelen."""
    from . import predictions
    from . import knowledge as knowledge_service
    prev = latest_report()
    return {
        "snapshot": snapshot if snapshot is not None else metrics.snapshot(),
        "yesterday_activity": _yesterday_activity(),
        "lessons": active_lessons(),
        "knowledge": knowledge_service.knowledge_prompt_block(),
        "validation": validation,
        "track_record": predictions.track_record(),
        "previous": {
            "date": prev["report_date"],
            "grades": prev.get("grades"),
            "advice": prev.get("advice"),
        } if prev else None,
    }


def _build_prompt(ctx: Dict[str, Any]) -> str:
    snapshot = ctx["snapshot"]
    parts = [
        f"Datum: {_today()}. Hieronder de actuele stand van Agent OS.",
        "",
        "## Cijfers per project (deterministisch berekend, 0-10)",
        "Elk project heeft een 'trend'-blok met week-over-week GSC-delta's "
        "(clicks/impressies/positie t.o.v. vorige 7 dagen) plus stijgers/dalers "
        "per pagina. trend=null betekent: nog geen historie — geen conclusie. "
        "Positie: LAGER is beter, dus een negatieve delta_position is winst. "
        "Gebruik deze delta's om te toetsen of eerdere bijsturing werkte.",
        json.dumps(snapshot["projects"], ensure_ascii=False, default=str),
        "",
        "## Globale cijfers (funnel, fouten, scheduler)",
        json.dumps(snapshot["global"], ensure_ascii=False, default=str),
        "",
        "## Activiteit laatste 24 uur",
        json.dumps(ctx["yesterday_activity"], ensure_ascii=False, default=str),
    ]
    val = ctx.get("validation")
    if val and val.get("evaluated"):
        parts += ["", "## Toetsing van je eerdere voorspellingen (afgerekend tegen de echte cijfers)",
                  "Dit is je gesloten leer-lus: voorspellingen waarvan de horizon "
                  "verstreek, vergeleken met de werkelijke uitkomst. Correcte "
                  "voorspellingen versterken de bijbehorende les; foute laten haar "
                  "vertrouwen dalen. Wees eerlijk over wat niet klopte.",
                  json.dumps({"accuracy_pct": val.get("accuracy"), "correct": val["correct"],
                              "wrong": val["wrong"], "unclear": val["unclear"],
                              "details": [{"project": e["project"], "metric": e["metric"],
                                           "verwacht": e["direction"], "uitspraak": e.get("statement", ""),
                                           "uitkomst": e["status"], "toelichting": e["note"]}
                                          for e in val["evaluated"]]}, ensure_ascii=False)]
    tr = ctx.get("track_record") or {}
    if tr.get("accuracy") is not None:
        parts += ["", f"## Jouw trefkans tot nu toe: {tr['accuracy']}% "
                  f"({tr['correct']} raak, {tr['wrong']} mis, {tr['open']} nog open)"]
    if ctx.get("knowledge"):
        parts += ["", "## Kennisbank (door Vincent aangeleverd onderzoek — dit is leidend)",
                  "Deze principes komen uit onderzoek dat Vincent je gaf (bijv. GEO/AEO/SEO). "
                  "Weeg ze zwaar: pas ze toe in je oordeel, advies en bijsturing, en toets waar "
                  "mogelijk of ze in de cijfers terugkomen.",
                  ctx["knowledge"]]
    if ctx["lessons"]:
        parts += ["", "## Jouw eerdere lessen (confidence = bewezen trefkans, 0-1; hoger = betrouwbaarder)",
                  json.dumps(ctx["lessons"], ensure_ascii=False)]
    if ctx["previous"]:
        parts += ["", f"## Jouw vorige briefing ({ctx['previous']['date']}) — cijfers en advies van toen",
                  json.dumps({"grades": ctx["previous"]["grades"],
                              "advice": ctx["previous"]["advice"]}, ensure_ascii=False),
                  "", "Vergelijk: is je advies opgevolgd, en wat deed dat met de cijfers?"]
    parts += [
        "",
        "Geef je dagbriefing als JSON met exact deze sleutels:",
        json.dumps({
            "oordeel_per_project": {"<projectnaam>": "één zin: wat is er aan de hand en wat is de belangrijkste hefboom"},
            "evaluatie_gisteren": "wat je vorige advies/lessen waard bleken (of null als eerste run)",
            "geleerd": ["max 3 nieuwe inzichten uit de data van vandaag"],
            "verbeteringen": [{
                "type": "batch_size | doel | content_run | outreach_run | seo_refresh | aanbeveling",
                "site_id": "(bij batch_size/content_run/seo_refresh) site-id uit de cijfers",
                "waarde": "(bij batch_size) 1-5",
                "aantal": "(bij content_run 1-3, outreach_run 1-15, seo_refresh 1-2)",
                "project": "(bij doel) projectnaam",
                "titel": "(bij doel) korte titel",
                "doelstelling": "(bij doel) concreet en meetbaar",
                "tekst": "(bij aanbeveling) wat Vincent zelf moet veranderen",
                "reden": "waarom, op basis van welke cijfers",
            }],
            "advies": [{"prio": 1, "actie": "concrete actie voor Vincent vandaag",
                        "waarom": "welke cijfers dit onderbouwen"}],
            "lessen": [{"les": "herbruikbaar inzicht in één zin", "categorie": "seo|content|funnel|systeem|proces"}],
            "voorspellingen": [{
                "project": "projectnaam (exact zoals in de cijfers)",
                "metric": "clicks | position | impressions | ctr | live_content",
                "richting": "up | down (positie: up = beter = lager getal)",
                "horizon_dagen": 7,
                "doel": "(optioneel) verwachte getalswaarde na de horizon",
                "les": "(optioneel) exacte tekst van de les uit 'lessen' die deze voorspelling toetst",
                "uitspraak": "de voorspelling in één zin, in mensentaal",
            }],
        }, ensure_ascii=False, indent=2),
        "",
        "Regels: maximaal 3 adviezen, gesorteerd op impact. Verbeteringen alleen als "
        "de cijfers ze onderbouwen; maximaal 1 nieuw doel. Jij bent de manager: werk "
        "dat een agent kan doen, DOE je zelf via een verbetering in plaats van het "
        "als advies aan Vincent terug te geven — content_run start de contentmotor "
        "(artikelen komen ter review in de Wachtrij), outreach_run zet outreach-"
        "concepten klaar (verstuurt niets, review-gate), seo_refresh verrijkt de "
        "sterkst wegzakkende pagina's (naar de Wachtrij). Maximaal 2 content_runs, "
        "1 outreach_run en 1 seo_refresh per dag; per doelwit hooguit één keer. "
        "Advies aan Vincent is er alleen voor wat een mens moet doen: goedkeuren in "
        "de Wachtrij/het Actiecentrum, GSC koppelen, strategische keuzes. Wees eerlijk hard: een "
        "project zonder meetdata of zonder output benoem je als probleem nummer één. "
        "Voorspellingen (max 3) maken je aantoonbaar: koppel er waar mogelijk een les "
        "aan en kies alleen meetbare metrieken. Voorspel niets voor projecten met "
        "trend=null — die zijn nog niet te toetsen.",
    ]
    return "\n".join(parts)


# ── De briefing zelf ────────────────────────────────────────────────────────

def _trend_arrow(p: Dict[str, Any]) -> str:
    """Compacte week-over-week-indicator voor de cijfertabel."""
    trend = p.get("trend")
    if not trend or not trend.get("site"):
        return "—"
    s = trend["site"]
    dc = s.get("delta_clicks")
    if dc is None:
        return "—"
    if dc > 0:
        arrow = f"▲ +{dc} clicks"
    elif dc < 0:
        arrow = f"▼ {dc} clicks"
    else:
        arrow = "= 0 clicks"
    dp = s.get("delta_position")
    if dp is not None and dp != 0:
        # Lagere positie = beter: toon dat expliciet met ↑/↓.
        arrow += f", pos {'↑' if dp < 0 else '↓'}{abs(dp)}"
    return arrow


def _fallback_judgment(p: Dict[str, Any]) -> str:
    """Deterministisch oordeel uit de pijler-cijfers, voor als de LLM (voor dit
    project) niets teruggaf — de Oordeel-kolom mag nooit leeg zijn."""
    pil = p["pillars"]
    issues: List[str] = []
    seo_note = pil["seo"].get("note")
    if seo_note:
        issues.append(seo_note)
    c = pil["content"]
    if c.get("live_30d", 0) == 0:
        issues.append("geen live content in 30 dagen")
    elif c["live_30d"] < c.get("target_30d", 0):
        issues.append(f"content {c['live_30d']}/{c['target_30d']} van het maanddoel")
    if c.get("stale_review"):
        issues.append(f"{c['stale_review']} stuk(s) >3 dagen in de Wachtrij")
    if c.get("needs_work"):
        issues.append(f"{c['needs_work']} job(s) needs_work")
    if pil["uitvoering"].get("failed_30d"):
        issues.append(f"{pil['uitvoering']['failed_30d']} doel(en) mislukt")
    if pil["hygiene"].get("errors_7d"):
        issues.append(f"{pil['hygiene']['errors_7d']} fout(en) deze week")
    if not issues:
        dc = ((p.get("trend") or {}).get("site") or {}).get("delta_clicks")
        if dc and dc > 0:
            return f"groeit (+{dc} clicks w-o-w) — vasthouden"
        return "op koers — geen acute blokkade"
    return "; ".join(issues[:2])


def _grade_table(projects: List[Dict[str, Any]], judgments: Dict[str, str]) -> List[str]:
    lines = ["| Project | Cijfer | Trend (7d) | Content | SEO | Uitvoering | Hygiëne | Oordeel |",
             "|---|---|---|---|---|---|---|---|"]
    for p in sorted(projects, key=lambda x: x["score"]):
        pil = p["pillars"]
        judgment = (judgments.get(p["project"]) or judgments.get(p["site_id"])
                    or _fallback_judgment(p))
        lines.append(
            f"| {p['project']} | **{p['grade']}** | {_trend_arrow(p)} | "
            f"{pil['content']['score']}/25 | "
            f"{pil['seo']['score']}/35 | {pil['uitvoering']['score']}/20 | "
            f"{pil['hygiene']['score']}/20 | {judgment[:160]} |"
        )
    return lines


def _trend_section(projects: List[Dict[str, Any]]) -> List[str]:
    """Concrete stijgers/dalers per pagina — het bewijs of interventies werken."""
    risers, fallers = [], []
    for p in projects:
        trend = p.get("trend")
        if not trend:
            continue
        for m in trend.get("risers", []):
            if m["delta_clicks"] > 0:
                risers.append((p["project"], m))
        for m in trend.get("fallers", []):
            if m["delta_clicks"] < 0:
                fallers.append((p["project"], m))
    if not risers and not fallers:
        return []
    risers.sort(key=lambda x: x[1]["delta_clicks"], reverse=True)
    fallers.sort(key=lambda x: x[1]["delta_clicks"])
    lines = ["## 📈 Bewegers deze week (GSC, pagina-niveau)"]
    for proj, m in risers[:5]:
        q = m.get("query") or m["url"]
        lines.append(f"- ▲ _{proj}_: '{q}' +{m['delta_clicks']} clicks")
    for proj, m in fallers[:5]:
        q = m.get("query") or m["url"]
        lines.append(f"- ▼ _{proj}_: '{q}' {m['delta_clicks']} clicks — check waarom")
    lines.append("")
    return lines


def _validation_section(validation: Optional[Dict[str, Any]],
                        track_record: Optional[Dict[str, Any]]) -> List[str]:
    """De gesloten leer-lus, zichtbaar: welke voorspellingen kwamen uit?"""
    lines: List[str] = []
    tr = track_record or {}
    if tr.get("accuracy") is not None:
        lines.append(f"## 🎯 Mijn trefkans: {tr['accuracy']}% "
                     f"({tr['correct']} raak · {tr['wrong']} mis · {tr['open']} nog open)")
    val = validation or {}
    evaluated = val.get("evaluated") or []
    if evaluated:
        if not lines:
            lines.append("## 🎯 Voorspellingen getoetst")
        icon = {"correct": "✅", "wrong": "❌", "unclear": "➖"}
        for e in evaluated[:8]:
            stmt = e.get("statement") or f"{e['project']} {e['metric']} {e['direction']}"
            lines.append(f"- {icon.get(e['status'], '·')} {stmt} — {e['note']}")
    if lines:
        lines.append("")
    return lines


def _open_predictions_section() -> List[str]:
    """Wat Iris nu voorspelt en wanneer het afgerekend wordt — zo is ze
    aanspreekbaar."""
    from . import predictions
    openp = predictions.open_predictions()
    if not openp:
        return []
    lines = ["## 🔮 Wat ik nu voorspel (wordt automatisch getoetst)"]
    for p in openp[:6]:
        stmt = p.get("statement") or f"{p['project']}: {p['metric']} {p['direction']}"
        lines.append(f"- {stmt} _(afrekening {p['due_date']})_")
    lines.append("")
    return lines


def _build_markdown(report_date: str, snapshot: Dict[str, Any], parsed: Optional[Dict[str, Any]],
                    applied: List[str], llm_used: bool,
                    validation: Optional[Dict[str, Any]] = None,
                    track_record: Optional[Dict[str, Any]] = None) -> str:
    projects = snapshot["projects"]
    glob = snapshot["global"]
    judgments = (parsed or {}).get("oordeel_per_project") or {}
    lines = [f"# Iris — dagbriefing {report_date}", ""]

    if not llm_used:
        lines += ["_LLM niet beschikbaar — dit is de puur cijfermatige briefing._", ""]

    lines += ["## 📊 Cijfers per project", *_grade_table(projects, judgments), ""]
    lines += _trend_section(projects)
    lines += _validation_section(validation, track_record)

    # Staande systeem-blokkade: zonder meetbare SEO is elk SEO-oordeel giswerk.
    # Alleen écht onmeetbaar: geen GSC-koppeling, óf geen enkele data (geen
    # pagina's én geen site-trend). Projecten mét een site-trend tellen niet mee.
    def _unmeasurable(p: Dict[str, Any]) -> bool:
        note = p["pillars"]["seo"].get("note") or ""
        has_trend = bool(p.get("trend") and p["trend"].get("site"))
        return "GSC" in note or (p["pillars"]["seo"]["pages"] == 0 and not has_trend)
    unmeasurable = [p["project"] for p in projects if _unmeasurable(p)]
    if unmeasurable:
        lines += ["## ⚠ Niet meetbaar — koppel eerst GSC",
                  f"- {len(unmeasurable)} project(en) hebben geen bruikbare Search Console-data "
                  f"({', '.join(unmeasurable[:8])}). Zolang dat zo is, is hun SEO-cijfer een "
                  "ondergrens en is elk SEO-advies giswerk. Dit is probleem nummer één.", ""]

    evaluation = (parsed or {}).get("evaluatie_gisteren")
    if evaluation:
        lines += ["## 🔁 Terugblik op mijn vorige advies", f"- {evaluation}", ""]

    learned = (parsed or {}).get("geleerd") or []
    lines.append("## 🧠 Wat ik geleerd heb")
    if learned:
        lines += [f"- {item}" for item in learned[:5]]
    else:
        lines.append("- (geen nieuwe lessen vandaag)")
    lines.append("")

    lines.append("## 🔧 Wat ik heb opgepakt")
    if applied:
        lines += [f"- {item}" for item in applied]
    else:
        lines.append("- (geen zelfstandige actie nodig vandaag)")
    recommendations = [i for i in ((parsed or {}).get("verbeteringen") or [])
                       if (i.get("type") or "").lower() == "aanbeveling" and i.get("tekst")]
    for rec in recommendations[:3]:
        lines.append(f"- 💡 Aanbeveling: {rec['tekst']}" + (f" ({rec.get('reden', '')})" if rec.get("reden") else ""))
    lines.append("")

    advice = (parsed or {}).get("advies") or []
    lines.append("## 🎯 Beste stappen voor vandaag")
    if advice:
        for a in sorted(advice, key=lambda x: x.get("prio", 9))[:3]:
            lines.append(f"{a.get('prio', '•')}. **{a.get('actie', '')}** — {a.get('waarom', '')}")
    else:
        # Cijfermatige terugval: zwakste project + openstaand werk.
        if projects:
            weakest = projects[0]
            lines.append(f"1. **Til {weakest['project']} omhoog (cijfer {weakest['grade']})** — "
                         f"zwakste project van dit moment: {_fallback_judgment(weakest)}.")
        if glob.get("pending_review_total"):
            lines.append(f"2. **Keur de Wachtrij goed** — {glob['pending_review_total']} stuk(s) wachten; "
                         "content die blijft liggen levert niets op.")
        if glob.get("scheduler_failures"):
            lines.append(f"3. **Fix de scheduler** — {len(glob['scheduler_failures'])} job(s) faalden: "
                         + "; ".join(f["job"] for f in glob["scheduler_failures"][:3]))
    lines.append("")

    lines += _open_predictions_section()

    funnel = glob.get("funnel") or {}
    if funnel.get("formula"):
        lines += ["## 📈 De formule", f"- {funnel['formula']}", ""]

    lines.append("_Iris draait elke ochtend 06:45 — geschiedenis via /api/iris/history._")
    return "\n".join(lines)


def _store_predictions(report_date: str, items: List[Dict[str, Any]],
                       snapshot: Dict[str, Any], lesson_ids: Dict[str, str]) -> int:
    """Leg de nieuwe voorspellingen vast. De baseline komt uit de échte
    snapshot (nooit uit de LLM), zodat de latere afrekening eerlijk is."""
    from . import predictions
    by_name = {p["project"]: p for p in snapshot["projects"]}
    saved = 0
    for it in (items or [])[:3]:
        proj = (it.get("project") or "").strip()
        snap = by_name.get(proj)
        if not snap:
            continue
        metric = (it.get("metric") or "").strip().lower()
        baseline = predictions.metric_value(snap, metric)
        if baseline is None:
            continue  # niet meetbaar → geen eerlijke voorspelling mogelijk
        lesson_text = (it.get("les") or "").strip().lower()
        pid = predictions.create_prediction(
            report_date=report_date, project=proj, site_id=snap["site_id"],
            metric=metric, direction=(it.get("richting") or "").strip().lower(),
            baseline=baseline, statement=(it.get("uitspraak") or "")[:400],
            horizon_days=int(it.get("horizon_dagen") or 7),
            target=_as_float(it.get("doel")), lesson_id=lesson_ids.get(lesson_text, ""),
        )
        if pid:
            saved += 1
    return saved


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def run_morning_briefing() -> Dict[str, Any]:
    """Scheduler entry-point: toets voorspellingen, analyseer, verbeter,
    voorspel, rapporteer, onthoud — de gesloten leer-lus."""
    from . import predictions
    from . import knowledge as knowledge_service
    report_date = _today()

    # 0. Neem eerst nieuwe kennis van Vincent op (vault-map): zo weerspiegelt de
    #    briefing van vandaag meteen wat hij net heeft aangeleverd.
    try:
        await knowledge_service.sync_knowledge()
    except Exception:
        logger.exception("[iris] kennis-sync mislukt")

    snapshot = metrics.snapshot()

    # 1. Reken eerst de openstaande voorspellingen af tegen de echte cijfers.
    #    Dit werkt het vertrouwen van de lessen bij vóór we de context bouwen.
    validation = predictions.evaluate_due(snapshot["projects"], today=report_date)

    ctx = gather_context(snapshot=snapshot, validation=validation)

    parsed = await _ask_iris(_build_prompt(ctx))
    if parsed is None:
        # Iris' brein is offline: dat is een fout die Vincent moet zien, geen
        # voetnoot. En een eerdere vólwaardige briefing van vandaag (bv. van de
        # ochtendrun) mag een mislukte herrun niet stilletjes degraderen.
        log_outcome(
            "Iris", "dagbriefing",
            f"Iris' analyse faalde na {_LLM_ATTEMPTS} pogingen (lege LLM-respons of "
            f"ongeldige JSON) — briefing {report_date} valt terug op puur cijfers",
            artifact="/api/iris/briefing",
            next_step="Controleer de LLM-backend (.env: ANTHROPIC_API_KEY / OPENROUTER_API_KEY / "
                      "OpenModel) en draai daarna 'Analyseer nu' opnieuw",
            status="error",
        )
        existing = latest_report()
        if (existing and existing.get("report_date") == report_date
                and "_LLM niet beschikbaar" not in (existing.get("markdown") or "")):
            logger.warning("[iris] Herrun zonder LLM — de volwaardige briefing van "
                           "%s blijft staan", report_date)
            return {"date": report_date, "markdown": existing.get("markdown") or "",
                    "grades": existing.get("grades") or {}, "applied": [],
                    "advice": existing.get("advice") or [], "predicted": 0,
                    "validation": validation, "llm_used": False, "kept_existing": True}

    applied: List[str] = []
    predicted = 0
    if parsed:
        applied = await _apply_improvements(parsed.get("verbeteringen") or [])
        lesson_ids = _upsert_lessons(parsed.get("lessen") or [])
        predicted = _store_predictions(report_date, parsed.get("voorspellingen") or [],
                                       snapshot, lesson_ids)

    markdown = _build_markdown(report_date, snapshot, parsed, applied,
                               llm_used=parsed is not None, validation=validation,
                               track_record=ctx.get("track_record"))

    grades = {p["project"]: {"cijfer": p["grade"], "score": p["score"],
                             "oordeel": ((parsed or {}).get("oordeel_per_project") or {}).get(p["project"], "")}
              for p in snapshot["projects"]}
    advice = (parsed or {}).get("advies") or []
    learned = (parsed or {}).get("geleerd") or []
    _store_report(report_date, markdown, grades, learned, applied, advice, snapshot)

    # Mail de briefing mee zodra SMTP is ingesteld (zelfde kanaal als de digest).
    mailed = False
    try:
        from ...shared import email_service
        if email_service.is_configured():
            weakest = snapshot["projects"][0]["project"] if snapshot["projects"] else "-"
            mailed = email_service.send_report(
                f"Iris dagbriefing {report_date} — focus: {weakest}", markdown,
            )
    except Exception as e:
        logger.warning("[iris] Briefing mailen mislukt: %s", e)

    top_advice = advice[0]["actie"] if advice else "Open de Iris-briefing op het dashboard"
    acc = validation.get("accuracy")
    acc_txt = f", trefkans {acc}%" if acc is not None else ""
    log_outcome(
        "Iris", "dagbriefing",
        f"Dagbriefing {report_date}: {len(applied)} bijsturing(en), {len(learned)} les(sen), "
        f"{len(advice)} advies(en), {predicted} voorspelling(en){acc_txt}",
        artifact="/api/iris/briefing",
        next_step=top_advice[:200],
    )
    logger.info("[iris] Dagbriefing %s klaar (LLM: %s, gemaild: %s, bijgestuurd: %d, "
                "voorspeld: %d, getoetst: %d raak/%d mis)",
                report_date, parsed is not None, mailed, len(applied), predicted,
                validation["correct"], validation["wrong"])
    return {"date": report_date, "markdown": markdown, "grades": grades,
            "applied": applied, "advice": advice, "predicted": predicted,
            "validation": validation, "llm_used": parsed is not None}


async def run_iris_prediction_eval() -> Dict[str, Any]:
    """Reken openstaande Iris-voorspellingen af — ZONDER LLM, idempotent.

    Dit is de robuuste, losgekoppelde variant van de toetsing die binnen
    `run_morning_briefing` gebeurt. Voorheen werd elke voorspelling alleen
    afgerekend als de ochtendbriefing daadwerkelijk draaide; viel die uit (lege
    LLM, dode quota), dan bleven voorspellingen eeuwig 'open' en leerde Iris
    nooit. Deze job draait op een eigen scheduler-moment en heeft geen LLM
    nodig, dus hij werkt ook tijdens een provider-storing. Idempotent: een
    voorspelling wordt maar één keer gesloten.
    """
    from . import predictions
    from . import metrics as iris_metrics
    try:
        snapshot = iris_metrics.snapshot()
    except Exception as e:
        logger.warning("[iris] prediction-eval: snapshot mislukt (%s) — sla over", e)
        return {"evaluated": 0, "correct": 0, "wrong": 0, "unclear": 0, "error": str(e)[:120]}
    try:
        result = predictions.evaluate_due(snapshot["projects"])
    except Exception as e:
        logger.exception("[iris] prediction-eval mislukt")
        return {"evaluated": 0, "correct": 0, "wrong": 0, "unclear": 0, "error": str(e)[:120]}
    if result["evaluated"]:
        logger.info("[iris] prediction-eval: %d getoetst (%d raak / %d mis / %d onduidelijk)",
                     result["evaluated"], result["correct"], result["wrong"], result["unclear"])
    return result

