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

import asyncio
import json
import logging
import re
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
_MAX_LINKBUILD_RUNS_PER_RUN = 1
_MAX_LEAD_SEARCH_PER_RUN = 1
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
                purpose="iris",
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
            purpose="iris",
        ):
            full += chunk
        if not full.strip():
            logger.warning("[iris] Hermes gaf een lege respons")
        return full.strip()
    except Exception as e:
        logger.warning("[iris] Hermes ook niet beschikbaar: %s", e)
        return ""


def _repair_truncated_json(s: str) -> Optional[Dict[str, Any]]:
    """Red een afgekapte JSON-respons (deepseek kapt geregeld midden in een
    waarde af). Strategie: loop string-bewust door de tekst, knip terug tot het
    einde van de laatste complete waarde en sluit de open structuren. Liever
    een briefing zonder de laatste voorspelling dan een dag zonder manager."""
    stack: List[str] = []
    in_str = False
    escape = False
    # Knippunten: posities waar alles ervóór een complete JSON-prefix is
    # (na een gesloten string, een gesloten {}/[], of vóór een komma), mét de
    # dan nog openstaande sluittekens.
    cuts: List[tuple] = []  # (index_na_element, sluitreeks op dat moment)
    for i, ch in enumerate(s):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
                cuts.append((i + 1, "".join(reversed(stack))))
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None  # structureel kapot, niet te redden
            stack.pop()
            cuts.append((i + 1, "".join(reversed(stack))))
        elif ch == ",":
            cuts.append((i, "".join(reversed(stack))))
    if not stack:
        return None  # gebalanceerd maar toch onparseerbaar — hier niet te redden
    # Knip terug naar het laatste complete element; een bungelende sleutel
    # zonder waarde ("key": <afgekapt>) valt weg door verder terug te knippen.
    for cut, closing in reversed(cuts[-200:]):
        head = s[:cut].rstrip().rstrip(",").rstrip()
        if head.endswith(":"):
            continue  # sleutel zonder waarde — probeer een eerder knippunt
        try:
            return json.loads(head + closing)
        except json.JSONDecodeError:
            continue
    return None


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    s = raw.strip()
    # Codefences eraf (```json ... ```): deepseek verpakt JSON daar graag in.
    fence = s.find("```")
    if fence != -1:
        inner = s[fence + 3:]
        if inner.lower().startswith("json"):
            inner = inner[4:]
        close = inner.find("```")
        if close != -1:
            s = inner[:close].strip()
        else:
            s = inner.strip()  # fence nooit gesloten → waarschijnlijk afgekapt
    start = s.find("{")
    if start == -1:
        return None
    end = s.rfind("}")
    if end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            pass
    # Geen sluitende brace of kapotte JSON: probeer de afgekapte variant te redden.
    return _repair_truncated_json(s[start:])


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


def _lesson_tokens(text: str) -> set:
    """Betekenisdragende woorden uit een lestekst, voor een tolerante match."""
    words = re.findall(r"[a-z0-9]{4,}", (text or "").lower())
    return set(words) - _LESSON_STOPWORDS


# Nederlandse vulwoorden van 4+ tekens: zonder deze lijst matcht elke les op
# elke andere via 'meer', 'wordt', 'voor'.
_LESSON_STOPWORDS = {
    "meer", "minder", "wordt", "worden", "voor", "naar", "door", "over", "deze",
    "voordat", "omdat", "zodat", "maar", "want", "toch", "alleen", "altijd",
    "nooit", "vaak", "soms", "welke", "waar", "wanneer", "moet", "moeten", "kunnen",
    "hebben", "heeft", "zijn", "wordt", "elke", "iedere", "veel", "weinig", "goed",
    "beter", "beste", "slecht", "groot", "klein", "eerst", "daarna", "dus",
}


def _match_lesson(lesson_text: str, lesson_ids: Dict[str, str]) -> str:
    """Zoek het lesson_id dat bij de door de LLM genoemde les hoort.

    Waarom niet gewoon lesson_ids[tekst] (27 jul 2026): dat was een exacte
    stringvergelijking, en die eist dat het model de lestekst woordelijk
    herhaalt. Dat doet het vrijwel nooit — het parafraseert. Resultaat: van de
    51 actieve lessen waren er ooit 2 aan een voorspelling gekoppeld, dus won of
    verloor er nooit een les vertrouwen en bleef `confidence` overal op de
    startwaarde 0,50 staan. De leerlus was gebouwd maar draaide leeg.

    Twee uitbreidingen: (a) tolerante match op woord-overlap i.p.v. exact, en
    (b) óók zoeken in álle actieve lessen, niet alleen in die van vandaag — Iris
    verwijst regelmatig naar een les van vorige week.
    """
    key = (lesson_text or "").strip().lower()
    if not key:
        return ""
    if key in lesson_ids:
        return lesson_ids[key]

    kandidaten = dict(lesson_ids)
    with get_conn() as conn:
        for row in conn.execute(
            "SELECT id, lesson FROM iris_lessons WHERE active = 1"
        ):
            kandidaten.setdefault((row["lesson"] or "").strip().lower(), row["id"])

    doel = _lesson_tokens(key)
    if not doel:
        return ""
    beste_id, beste_score = "", 0.0
    for tekst, lid in kandidaten.items():
        tokens = _lesson_tokens(tekst)
        if not tokens:
            continue
        overlap = doel & tokens
        # Jaccard: straft zowel een te korte als een te lange kandidaat af.
        score = len(overlap) / len(doel | tokens)
        if score > beste_score:
            beste_id, beste_score = lid, score
    # 0.4 is streng genoeg dat twee ongerelateerde lessen elkaar niet vangen, en
    # los genoeg voor een parafrase. Onder de drempel liever géén koppeling dan
    # een verkeerde: een les die krediet krijgt voor andermans voorspelling
    # maakt het vertrouwenscijfer waardeloos.
    return beste_id if beste_score >= 0.4 else ""


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
                  improvements: List, advice: List, metrics_snapshot: Dict,
                  llm_ok: bool = True) -> str:
    """Eén briefing per dag: een nieuwe run op dezelfde dag vervangt de oude.

    llm_ok=False markeert een puur cijfermatige terugval — de herkanselaar
    (scheduler-job iris_briefing_retry) probeert die later alsnog te vervangen
    door een volwaardige analyse."""
    report_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("DELETE FROM iris_reports WHERE report_date = ?", (report_date,))
        conn.execute(
            "INSERT INTO iris_reports (id, report_date, markdown, grades, learned, "
            "improvements, advice, metrics, created_at, llm_ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, report_date, markdown,
             json.dumps(grades, ensure_ascii=False),
             json.dumps(learned, ensure_ascii=False),
             json.dumps(improvements, ensure_ascii=False),
             json.dumps(advice, ensure_ascii=False),
             json.dumps(metrics_snapshot, ensure_ascii=False),
             _now_iso(), 1 if llm_ok else 0),
        )
    return report_id


def _is_fallback_report(rec: Dict[str, Any]) -> bool:
    """Terugval-briefing? Kijkt naar de llm_ok-vlag; rijen van vóór die kolom
    worden herkend aan de vaste marker in de markdown."""
    if not rec.get("llm_ok", 1):
        return True
    return "_LLM niet beschikbaar" in (rec.get("markdown") or "")


def briefing_needs_retry() -> bool:
    """True als de briefing van vandaag een LLM-loze terugval is.

    De herkanselaar draait dan opnieuw; zodra er een volwaardige analyse staat
    (of er vandaag nog geen briefing is — dat is aan de 06:45-run/inhaalslag)
    is er niets te herkansen."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT markdown, llm_ok FROM iris_reports WHERE report_date = ?",
            (_today(),),
        ).fetchone()
    if not row:
        return False
    return _is_fallback_report(dict(row))


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


async def _apply_draft_goal(project: str, title: str, objective: str, reason: str) -> Optional[Dict[str, Any]]:
    """Concept-doel aanmaken — blijft 'draft', wacht in het Actiecentrum op akkoord.

    Retourneert {'detail': ..., 'goal_id': ...} zodat de suggestie zich aan het
    aangemaakte doel kan koppelen (één bron van waarheid op het dashboard).
    """
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
        return {"detail": detail, "goal_id": gid}
    except Exception as e:
        logger.warning("[iris] Concept-doel aanmaken mislukt: %s", e)
        return None


async def _apply_improvements(improvements: List[Dict[str, Any]]) -> List[str]:
    """Voer alleen whitelisted verbeteringen uit; alles wordt gelogd."""
    from . import actions
    from ...shared.outcomes import llm_budget_exceeded
    # De LLM-zware acties (content/outreach/seo) roepen zelf de gateway aan.
    # Is het budget op of de quota-rem actief, dan zouden ze tóch 403'en en per
    # actie een 'iris_actie'-foutkaart loggen (die de échte oorzaak — quota —
    # verhult) plus de gateway rammen terwijl er niets meer doorkomt. Sla die
    # acties dan over; batch_size (pure DB-write) en doel (draft) mogen wél door.
    _LLM_HEAVY = {"content_run", "outreach_run", "seo_refresh", "linkbuilding_run",
                  "lead_search_run"}
    budget_op = llm_budget_exceeded()
    applied: List[str] = []
    goals_created = content_runs = outreach_runs = seo_refreshes = linkbuild_runs = 0
    lead_searches = 0
    for imp in improvements[:8]:
        kind = (imp.get("type") or "").strip().lower()
        reason = (imp.get("reden") or imp.get("reason") or "").strip()[:300]
        done: Optional[str] = None
        if kind in _LLM_HEAVY and budget_op:
            logger.info("[iris] %s overgeslagen: LLM-budget/quota op — advies blijft staan", kind)
            continue
        if kind == "batch_size":
            done = _apply_batch_size(imp.get("site_id", ""), imp.get("waarde") or imp.get("value"), reason)
        elif kind == "doel" and goals_created < _MAX_NEW_GOALS_PER_RUN:
            done = await _apply_draft_goal(
                imp.get("project", ""), (imp.get("titel") or imp.get("title") or "").strip(),
                (imp.get("doelstelling") or imp.get("objective") or "").strip(), reason,
            )
            goals_created += 1 if done else 0
            # _apply_draft_goal levert nu een dict {'detail','goal_id'} — voor
            # de rapportage-string gebruiken we alleen de detail-tekst.
            if done:
                done = done.get("detail", "")
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
        elif kind == "linkbuilding_run" and linkbuild_runs < _MAX_LINKBUILD_RUNS_PER_RUN:
            done = await actions.linkbuilding_run(
                imp.get("aantal") or imp.get("waarde") or imp.get("count"), reason,
            )
            linkbuild_runs += 1 if done else 0
        elif kind == "lead_search_run" and lead_searches < _MAX_LEAD_SEARCH_PER_RUN:
            done = await actions.lead_search_run(
                imp.get("zoekopdrachten") or imp.get("queries"), reason,
                template=str(imp.get("template") or ""),
                lead_type=str(imp.get("lead_type") or ""),
            )
            lead_searches += 1 if done else 0
        # 'aanbeveling' en onbekende types worden niet uitgevoerd — die blijven
        # advies aan Vincent, geen zelfstandige actie.
        if done:
            applied.append(done)
    return applied


async def _apply_rule_based(snapshot: Dict[str, Any]) -> tuple:
    """Regelgebaseerd minimum voor terugval-dagen: als Iris' analyse-brein
    offline is, stopt haar manager-rol niet. De deterministische knelpunten
    dragen kant-en-klare acties aan; de veilige agent-acties (alles landt
    achter een review-gate en dedupet per dag) voert ze direct uit, en wat
    een mens vergt — of wat door de quota-rem niet kan — biedt ze aan als
    'Wil je dat ik dit fix?'-knop. Retourneert (uitgevoerd, aanbiedingen)."""
    from . import actions
    from ...shared.outcomes import llm_budget_exceeded
    applied: List[str] = []
    leftovers: List[Dict[str, Any]] = []
    budget_op = llm_budget_exceeded()
    for b in snapshot.get("bottlenecks") or []:
        sug = b.get("suggestion")
        if not sug:
            continue
        typ = sug.get("type")
        payload = sug.get("payload") or {}
        reason = f"regelgebaseerd (LLM-terugval): {b.get('waarom', '')}"
        if typ == "gsc_connect" or budget_op:
            # Menselijke stap, of de quota-rem staat erop: aanbieden i.p.v. draaien.
            leftovers.append(sug)
            continue
        done: Optional[str] = None
        try:
            if typ == "outreach_run":
                done = await actions.outreach_run(payload.get("aantal"), reason)
            elif typ == "linkbuilding_run":
                done = await actions.linkbuilding_run(payload.get("aantal"), reason)
            elif typ == "content_run":
                done = await actions.content_run(sug.get("target", ""), payload.get("aantal"), reason)
            elif typ == "seo_refresh":
                done = await actions.seo_refresh(sug.get("target", ""), payload.get("aantal"), reason)
            elif typ == "lead_search_run":
                done = await actions.lead_search_run(
                    payload.get("zoekopdrachten") or payload.get("queries"), reason,
                    template=str(payload.get("template") or ""))
            elif typ == "run_job":
                # Een gemiste geplande taak inhalen is het veiligste werk dat
                # Iris kent: het is exact de taak die vanzelf had moeten
                # draaien, met dezelfde review-gates. Juist op een
                # terugval-dag (LLM plat) is dit wat er nog wél kan.
                from ...scheduler import run_job_now
                res = await run_job_now(str(payload.get("job_id") or sug.get("target") or ""))
                done = (f"Gemiste taak '{res.get('label')}' alsnog gestart"
                        if res.get("ok") else None)
            else:
                leftovers.append(sug)
                continue
        except Exception:
            logger.exception("[iris] regelgebaseerde actie %s mislukt", typ)
        if done:
            applied.append(done)
    return applied, leftovers


# ── Context verzamelen en prompt bouwen ─────────────────────────────────────

def _yesterday_activity(limit: int = 40) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT project, action, detail, status, created_at FROM activity_log "
            "WHERE created_at > datetime('now', '-1 day') ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Opgeloste fouten niet aan Iris voeren: anders adviseert ze
            # zombie-fixes voor dingen die allang klaar zijn. De ok-regels
            # (bv. 'LIVE op ...') blijven staan, dus zij ziet wél de uitkomst.
            if d.get("status") == "error" and metrics._error_resolved(conn, d):
                d["status"] = "ok"
                d["detail"] = f"[OPGELOST] {d.get('detail') or ''}"
            out.append(d)
    return out


def _audit_blok() -> str:
    """De waarheidsaudit als tekst voor de prompt.

    Faalt hij, dan liever een expliciete melding in de prompt dan stilte: Iris
    moet kunnen zien dat ze deze ronde blind is, anders concludeert ze uit een
    leeg blok dat alles in orde is — en dat is exact de fout die de audit
    bestrijdt.
    """
    try:
        from . import integrity
        return integrity.prompt_block()
    except Exception as e:  # noqa: BLE001
        logger.exception("[iris] waarheidsaudit-blok bouwen mislukt")
        return (f"Waarheidsaudit: NIET beschikbaar deze ronde ({type(e).__name__}). "
                f"Trek hieruit géén conclusie dat alles in orde is.")


def _weekrapport_blok() -> str:
    """Het 28-daagse weekbeeld als tekst voor de prompt.

    Faalt het, dan expliciet melden in plaats van weglaten: een ontbrekend blok
    leest als 'geen bijzonderheden', en het weekrapport is juist de plek waar
    een langzame daling zichtbaar wordt die in de dagcijfers verdrinkt.
    """
    try:
        from ..analytics import insights
        return insights.prompt_block()
    except Exception as e:  # noqa: BLE001
        logger.exception("[iris] weekrapport-blok bouwen mislukt")
        return (f"Weekrapport: NIET beschikbaar deze ronde ({type(e).__name__}). "
                f"Er is dus géén 28-daags beeld; oordeel alleen op de dagcijfers.")


async def gather_context(snapshot: Optional[Dict[str, Any]] = None,
                      validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bouw de context voor de prompt. Roep dit ná evaluate_due aan, zodat de
    lessen hun bijgewerkte vertrouwen en de ingetrokken lessen weerspiegelen."""
    from . import predictions
    from . import knowledge as knowledge_service
    prev = latest_report()
    # Agenda-context (Fase 1): als Google Agenda is gekoppeld, geef Iris de
    # planning van vandaag mee zodat ze rekening houdt met drukte/rust in haar
    # advies (bv. "drukke dag, plan geen content-run"). Stil als niet gekoppeld.
    agenda_summary = ""
    from ...domains.calendar import service as calendar_service
    try:
        if calendar_service.is_configured():
            agenda_summary = await calendar_service.get_today_summary()
    except Exception as e:
        logger.warning("[iris] agenda-context ophalen mislukt: %s",
                       calendar_service.explain_error(e))
    # Agenda-voorstellen uit mail: aantal openstaande (menselijke goedkeuring).
    agenda_proposals = 0
    try:
        from ...domains.calendar import agent as agenda_agent
        agenda_proposals = len(agenda_agent.pending_proposals())
    except Exception:
        pass
    # Persoonlijke rituelen (ochtend/avond/weekstart/weekreview/wins/doelen):
    # context, geen actiepunt. Iris mag er haar tóón op afstemmen (niet
    # aandringen op een zware run als de energie al dagen laag staat of het
    # ochtendritueel een week is overgeslagen) maar dit hoort nooit in het
    # Actiecentrum — dat is een inbox van beslissingen, dit is geen besluit.
    rituals_context = None
    try:
        from ...domains.rituals import service as rituals_service
        rituals_context = rituals_service.get_service().get_briefing_context()
    except Exception:
        logger.warning("[iris] rituelen-context ophalen mislukt", exc_info=True)
    return {
        "snapshot": snapshot if snapshot is not None else metrics.snapshot(),
        "yesterday_activity": _yesterday_activity(),
        "lessons": active_lessons(),
        "knowledge": knowledge_service.knowledge_prompt_block(),
        "validation": validation,
        "integrity": _audit_blok(),
        "weekrapport": _weekrapport_blok(),
        "agenda": agenda_summary,
        "agenda_proposals": agenda_proposals,
        "rituals": rituals_context,
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
        "## Waarheidsaudit — wat is er stil kapot? (LEES DIT EERST)",
        "Deze toetsen vergelijken wat het systeem over zichzelf beweert met wat er "
        "werkelijk is. Ze staan bovenaan omdat een cijfer dat op een kapot mechanisme "
        "steunt, geen cijfer is. Weeg blokkerende bevindingen zwaarder dan élke "
        "groeikans: een dode pagina of twee artikelen op één zoekwoord doen actief "
        "schade, en die stopzetten gaat vóór iets nieuws beginnen. Noem in je advies "
        "expliciet wat je hiermee doet — negeer je een bevinding bewust, zeg dan waarom.",
        ctx.get("integrity") or "Waarheidsaudit: geen gegevens.",
        "",
        "## Cijfers per project (deterministisch berekend, 0-10)",
        "Elk project heeft een 'trend'-blok met week-over-week GSC-delta's "
        "(clicks/impressies/positie t.o.v. vorige 7 dagen) plus stijgers/dalers "
        "per pagina. trend=null betekent: nog geen historie — geen conclusie. "
        "Positie: LAGER is beter, dus een negatieve delta_position is winst. "
        "Gebruik deze delta's om te toetsen of eerdere bijsturing werkte.",
        json.dumps(snapshot["projects"], ensure_ascii=False, default=str),
        "",
        "## Weekrapport — het trage beeld (28 dagen vs. de 28 daarvóór)",
        "De cijfers hierboven zijn de snelle horizon (7 vs. 7 dagen); dit is de "
        "trage. Ze spreken elkaar niet tegen als ze verschillen — een project dat "
        "deze week zakt maar over 28 dagen stijgt heeft géén probleem, en andersom "
        "is een goede week binnen een dalende lijn geen reden tot rust. Stuur op de "
        "trage lijn en gebruik de snelle alleen als vroeg signaal. De quick wins en "
        "CTR-gaten hieronder zijn gemeten kansen: een CTR-gat is een snippet-probleem "
        "(title/meta herschrijven), géén reden voor een nieuw artikel.",
        ctx.get("weekrapport") or "Weekrapport: geen gegevens.",
        "",
        "## Globale cijfers (funnel, fouten, scheduler, stilstand)",
        "Let op `downtime_gaps`: geplande taken die niet gedraaid hebben omdat de "
        "machine uit stond. Dit is géén prestatiecijfer maar een verklaring — een "
        "agent die niet draaide heeft die dag niet bestaan. Verklaar een tegenvallend "
        "cijfer daarom altijd eerst uit de stilstand voordat je de agent bijstuurt: "
        "een droge funnel na vier dagen zonder outreach-batch vraagt om die batch "
        "alsnog draaien, niet om een nieuwe outreach-strategie. En let op "
        "`pending_review_total`: staat daar een stapel, dan is méér produceren "
        "schadelijk — het verstopt precies de plek waar de opbrengst vandaan moet "
        "komen. Stel in dat geval geen content_run voor.",
        json.dumps(snapshot["global"], ensure_ascii=False, default=str),
        "",
        "## Systeem-knelpunten (deterministisch voorgesorteerd op bedrijfsimpact)",
        "Dit is de minimale prioriteitenlijst; jij mag hem aanscherpen of "
        "overrulen, maar benoem dan waaróm de cijfers dat rechtvaardigen.",
        json.dumps([{k: v for k, v in b.items() if k != "suggestion"}
                    for b in (snapshot.get("bottlenecks") or [])], ensure_ascii=False),
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
                  "", "Vergelijk: is je advies opgevolgd, en wat deed dat met de cijfers?",
                  "", "BELANGRIJK: adviseer ALLEEN acties die nu nog echt nodig zijn. "
                      "Check de actuele stand (activiteit van gisteren, cijfers) vóór je een "
                      "eerder advies herhaalt: regels gemarkeerd met [OPGELOST] of gevolgd door "
                      "een succesvolle publicatie/'LIVE'-melding zijn KLAAR — noem die niet als "
                      "openstaand advies en zeg niet dat het 'nog niet opgevolgd' is."]
    if ctx.get("agenda"):
        parts += ["", "## Je agenda van vandaag (Google Calendar)",
                  "Houd bij je advies rekening met je beschikbaarheid: op een drukke dag "
                  "plan je geen zware autonome runs (content/SEO), op een lege dag juist wel. "
                  "Noem in je advies expliciet of de dag ruimte biedt voor dieptewerk of acquisitie.",
                  ctx["agenda"]]
    if ctx.get("agenda_proposals"):
        parts += ["", f"## Openstaande afspraak-voorstellen uit mail: {ctx['agenda_proposals']}",
                  "Iris heeft uit binnenkomende mail afspraak-verzoeken gedetecteerd en "
                  "voorstellen klaargezet (met reistijd + conflict-check). Die wachten in het "
                  "Actiecentrum op Vincents goedkeuring voordat ze in Google Agenda landen. "
                  "Noem ze kort in je briefing zodat hij ze niet vergeet goed te keuren."]
    if ctx.get("rituals"):
        parts += ["", "## Persoonlijk (ochtend/avond-ritueel, week, wins, doelen)",
                  "Dit is context, geen actiepunt — er hoort geen kaart of aanbeveling uit voort "
                  "te komen. Gebruik het alleen om je tóón te kalibreren: bij een lage energie of "
                  "een ritueel dat al dagen wordt overgeslagen, dring niet aan op een extra zware "
                  "run (content/outreach/seo_refresh); noem het hooguit vriendelijk in je briefing.",
                  json.dumps(ctx["rituals"], ensure_ascii=False, default=str)]
    parts += [
        "",
        "Geef je dagbriefing als JSON met exact deze sleutels:",
        json.dumps({
            "oordeel_per_project": {"<projectnaam>": "één zin: wat is er aan de hand en wat is de belangrijkste hefboom"},
            "evaluatie_gisteren": "wat je vorige advies/lessen waard bleken (of null als eerste run)",
            "geleerd": ["max 3 nieuwe inzichten uit de data van vandaag"],
            "verbeteringen": [{
                "type": "batch_size | doel | content_run | outreach_run | seo_refresh | linkbuilding_run | lead_search_run | aanbeveling",
                "site_id": "(bij batch_size/content_run/seo_refresh) site-id uit de cijfers",
                "waarde": "(bij batch_size) 1-5",
                "aantal": "(bij content_run 1-3, outreach_run 1-15, seo_refresh 1-2, linkbuilding_run 1-10)",
                "zoekopdrachten": ["(bij lead_search_run) 3-6 concrete NL-zoekopdrachten passend bij de doelgroep"],
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
            "actie_voorstellen": [{
                "type": "content_run | seo_refresh | outreach_run | linkbuilding_run | lead_search_run | goal_draft | gsc_connect",
                "scope": "(bij content_run/seo_refresh/gsc_connect) projectnaam, anders 'all'",
                "title": "korte, menselijke actie-omschrijving (knop-tekst, <60 tekens)",
                "detail": "waarom (cijfers) + concreet wat de agent doet — dit toont Iris onder de knop",
                "target": "(bij content_run/seo_refresh) site-id | (bij goal_draft) project | (bij gsc_connect) GSC-property | anders 'all'",
                "priority": 1,
                "payload": {"aantal": "(bij content_run 1-3, outreach_run 1-15, seo_refresh 1-2, linkbuilding_run 1-10)", "zoekopdrachten": ["(bij lead_search_run) 3-6 concrete zoekopdrachten"], "doelstelling": "(bij goal_draft) concreet en meetbaar"}
            }],
        }, ensure_ascii=False, indent=2),
        "",
        "Regels: maximaal 3 adviezen, gesorteerd op impact. Verbeteringen alleen als "
        "de cijfers ze onderbouwen; maximaal 1 nieuw doel. Jij bent de manager: werk "
        "dat een agent kan doen, DOE je zelf via een verbetering in plaats van het "
        "als advies aan Vincent terug te geven — content_run start de contentmotor "
        "(artikelen komen ter review in de Wachtrij), outreach_run zet outreach-"
        "concepten klaar (verstuurt niets, review-gate), seo_refresh verrijkt de "
        "sterkst wegzakkende pagina's (naar de Wachtrij), linkbuilding_run zet "
        "link-outreach-concepten klaar voor gekwalificeerde linkkansen (verstuurt "
        "niets, review-gate — backlinks zijn de hefboom voor positie-verbetering), "
        "en lead_search_run vult de acquisitie-funnel: de agent zoekt, verrijkt en "
        "bewaart nieuwe leads (er wordt niets gemaild). Een droge funnel-voorraad "
        "los je dus ZELF op met lead_search_run mét 3-6 concrete zoekopdrachten — "
        "vraag Vincent NOOIT om handmatig zoekopdrachten in te voeren. "
        "Maximaal 2 content_runs, 1 outreach_run, 1 seo_refresh, 1 "
        "linkbuilding_run en 1 lead_search_run per dag; per doelwit hooguit één keer. "
        "Advies aan Vincent is er alleen voor wat een mens moet doen: goedkeuren in "
        "de Wachtrij/het Actiecentrum, GSC koppelen, strategische keuzes. Alles wat "
        "een agent kan uitvoeren hoort in 'verbeteringen' (direct doen) of "
        "'actie_voorstellen' (knop) — een advies zonder bijbehorende actie terwijl "
        "er wél een hendel bestaat, is een gemiste dag. Wees eerlijk hard: een "
        "project zonder meetdata of zonder output benoem je als probleem nummer één. "
        "Voorspellingen (max 3) maken je aantoonbaar: koppel er waar mogelijk een les "
        "aan en kies alleen meetbare metrieken. Voorspel niets voor projecten met "
        "trend=null — die zijn nog niet te toetsen. Grijp je in op een structurele "
        "daling uit het weekrapport, zet daar dan een voorspelling met "
        "horizon_dagen 28 bij: die interventie werkt op de trage horizon, en een "
        "toets op 7 dagen rekent hem af op ruis — dat leert je het verkeerde. "
        "actie_voorstellen (max 4): dit zijn de knoppen die Vincent in zijn briefing "
        "ziet — elk is één concreet uitvoerbare stap met een agent erachter, "
        "gekoppeld aan de cijfers hierboven. Zet hier de echte fixes neer, niet de "
        "adviezen: bijv. content_run voor de zwakste site, seo_refresh voor een "
        "wegzakkende pagina, gsc_connect voor een project zonder meetdata, of "
        "goal_draft voor een strategische klus. 'gsc_connect' is GEEN agent-actie "
        "maar een menselijke stap — Iris logt dan alleen wat Vincent moet koppelen.",
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


def _fix_offer_section(report_date: str) -> List[str]:
    """De 'Wil je dat ik dit fix?'-aanbiedingen, ook zichtbaar in de gemailde
    briefing — de knoppen zelf staan op de Control Room."""
    from . import fix as fix_module
    pending = [s for s in fix_module.list_pending(report_date)
               if s.get("status") == "pending"]
    if not pending:
        return []
    lines = ["## ⚡ Dit kan ik nu voor je fixen (één klik op het dashboard)"]
    for s in pending[:6]:
        detail = f" — {s['detail']}" if s.get("detail") else ""
        lines.append(f"- **{s['title']}**{detail}")
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
        # Cijfermatige terugval: de deterministische knelpunt-lijst, knelpunt
        # eerst — het laagste rapportcijfer is zelden het echte probleem.
        for b in (snapshot.get("bottlenecks") or [])[:3]:
            lines.append(f"{b['prio']}. **{b['actie']}** — {b['waarom']}.")
    lines.append("")

    lines += _open_predictions_section()
    lines += _fix_offer_section(report_date)

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
            target=_as_float(it.get("doel")),
            lesson_id=_match_lesson(lesson_text, lesson_ids),
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

    # 0b. Ruim eerst op wat ze zelf kan oplossen. Dit moet vóór `snapshot()`:
    #     de hygiëne-pijler telt openstaande fouten mee, en een fout die Iris
    #     vijf seconden later zelf verhelpt hoort haar eigen rapportcijfer niet
    #     omlaag te trekken — noch als "wacht op jou" in het ochtendrapport van
    #     07:00 te belanden.
    try:
        from . import selfheal
        heal = await selfheal.run_selfheal(source="briefing")
        if heal.get("healed"):
            logger.info("[iris] briefing-zelfherstel: %d fout(en) zelf opgelost",
                        heal["healed"])
    except Exception:
        logger.exception("[iris] zelfherstel-ronde mislukt")

    # 0c. En daarna: zoeken wat stíl kapot is. Zelfherstel kijkt naar fouten die
    #     zichzelf hebben gemeld; de waarheidsaudit toetst de beweringen die het
    #     systeem over zichzelf doet ('published' = staat live, 'rejected' = niet
    #     meer online, 'geleerd' = een les die vertrouwen won). Vóór de snapshot,
    #     want blokkerende bevindingen horen in het oordeel van vandaag mee te
    #     wegen — niet pas morgen.
    #
    #     Naar een thread, en dat is geen detail. De audit haalt tientallen
    #     pagina's op met een sýnchrone httpx-client (`_pagina_status`, timeout
    #     15s per URL) — vanuit deze async functie blokkeert dat de event loop,
    #     en daarmee de hele webserver. Bij een koude start om 08:00 loopt de
    #     briefing als inhaalslag, dus stond het dashboard minutenlang op "kan
    #     geen verbinding maken" terwijl de log al "startup complete" zei
    #     (gemeten 6 aug 2026: ruim vier minuten). Hetzelfde geldt voor
    #     `metrics.snapshot()` (~3,4s): op zichzelf klein, maar het telt op en
    #     beide draaien elders al in een threadpool, dus dit is de bestaande
    #     route en geen nieuwe aanname.
    try:
        from . import integrity
        audit = await asyncio.to_thread(integrity.run_audit, source="briefing")
        if audit.get("nieuw"):
            logger.info("[iris] waarheidsaudit: %d nieuwe bevinding(en)", audit["nieuw"])
    except Exception:
        logger.exception("[iris] waarheidsaudit mislukt")

    snapshot = await asyncio.to_thread(metrics.snapshot)

    # 1. Reken eerst de openstaande voorspellingen af tegen de echte cijfers.
    #    Dit werkt het vertrouwen van de lessen bij vóór we de context bouwen.
    validation = predictions.evaluate_due(snapshot["projects"], today=report_date)

    ctx = await gather_context(snapshot=snapshot, validation=validation)

    parsed = await _ask_iris(_build_prompt(ctx))
    if parsed is None:
        # Iris' brein is offline: dat is een fout die Vincent moet zien, geen
        # voetnoot — maar één kaart per dag; de herkanselaar (elke 45 min) mag
        # het Actiecentrum niet vol stapelen met kopieën.
        with get_conn() as conn:
            flagged = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action = 'dagbriefing' "
                "AND status = 'error' AND date(created_at) = date('now')"
            ).fetchone()[0]
        if not flagged:
            log_outcome(
                "Iris", "dagbriefing",
                f"Iris' analyse faalde na {_LLM_ATTEMPTS} pogingen (lege LLM-respons of "
                f"ongeldige JSON) — briefing {report_date} valt terug op puur cijfers",
                artifact="/api/iris/briefing",
                next_step="Niets nodig: de herkanselaar probeert het elke 45 min opnieuw zodra "
                          "de LLM-quota terug is. Handmatig kan ook: 'Analyseer nu'.",
                status="error",
            )
        # Een bestaande briefing van vandaag blijft staan: een vólwaardige mag
        # nooit degraderen, en een terugval nóg eens opslaan (en mailen) voegt
        # niets toe — de herkanselaar probeert later gewoon opnieuw.
        existing = latest_report()
        if existing and existing.get("report_date") == report_date:
            logger.warning("[iris] Herrun zonder LLM — de %s briefing van %s blijft staan",
                           "cijfermatige" if _is_fallback_report(existing) else "volwaardige",
                           report_date)
            return {"date": report_date, "markdown": existing.get("markdown") or "",
                    "grades": existing.get("grades") or {}, "applied": [],
                    "advice": existing.get("advice") or [], "predicted": 0,
                    "validation": validation, "llm_used": False, "kept_existing": True}

    applied: List[str] = []
    predicted = 0
    saved_sugs = 0
    from . import fix as fix_module
    if parsed:
        applied = await _apply_improvements(parsed.get("verbeteringen") or [])
        lesson_ids = _upsert_lessons(parsed.get("lessen") or [])
        predicted = _store_predictions(report_date, parsed.get("voorspellingen") or [],
                                       snapshot, lesson_ids)
        # Actie-voorstellen ("Wil je dat ik dit fix?") — Iris' kant-en-klare
        # fixes, klaargezet voor Vincents goedkeuring. Maximaal 4.
        saved_sugs = fix_module.upsert_suggestions(
            report_date, parsed.get("actie_voorstellen") or [])
    else:
        # Terugval zonder LLM: de manager stopt niet. Veilige knelpunt-acties
        # draaien regelgebaseerd (review-gates + dag-dedupe blijven gelden);
        # wat een mens vergt of niet kan, wordt een fix-aanbieding.
        applied, leftovers = await _apply_rule_based(snapshot)
        saved_sugs = fix_module.upsert_suggestions(report_date, leftovers)

    markdown = _build_markdown(report_date, snapshot, parsed, applied,
                               llm_used=parsed is not None, validation=validation,
                               track_record=ctx.get("track_record"))

    grades = {p["project"]: {"cijfer": p["grade"], "score": p["score"],
                             "oordeel": ((parsed or {}).get("oordeel_per_project") or {}).get(p["project"], "")}
              for p in snapshot["projects"]}
    advice = (parsed or {}).get("advies") or []
    learned = (parsed or {}).get("geleerd") or []
    _store_report(report_date, markdown, grades, learned, applied, advice, snapshot,
                  llm_ok=parsed is not None)

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
            "validation": validation, "llm_used": parsed is not None,
            "saved_suggestions": saved_sugs}


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

