"""De Sparringpartner — service-laag.

Combineert businesscoaching (rol, keuzes, leiderschap) en welzijnscoaching
(energie, herstel, gewoonten) in één persona — in de praktijk lopen die voor
een ondernemer met een holding altijd door elkaar. Kiest deterministisch een
coachtechniek op basis van het signaal (`choose_technique`, geen LLM nodig om
uit te leggen wat er gebeurt), en onthoudt wat blijkt te kloppen via
`coach_lessons` — een observatie is pas een les na herhaald bewijs, niet na
één keer zenden. Zelfde filosofie als `iris/service.py:iris_lessons`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...shared.database import get_conn
from ..rituals.service import get_service as get_rituals_service
from .models import ensure_schema

log = logging.getLogger(__name__)

TECHNIQUE_LABELS: Dict[str, str] = {
    "grow": "GROW (doel, realiteit, opties, actie)",
    "mi": "Motiverende gespreksvoering (OARS)",
    "oplossingsgericht": "Oplossingsgericht (schaalvragen)",
    "cgt": "CGT-geïnformeerd (patroon herkennen)",
    "act": "ACT / waardenwerk",
    "systemisch": "Systemisch (jij en de holding eromheen)",
    "strengths": "Strengths-based",
}

_TECHNIQUE_INSTRUCTIONS: Dict[str, str] = {
    "grow": "Gebruik het GROW-model: help Vincent zijn doel voor vandaag scherp krijgen (Goal), benoem kort de huidige realiteit (Reality), noem één onconventionele optie (Options), en eindig met een concrete vraag over de eerste stap (Will).",
    "mi": 'Gebruik motiverende gespreksvoering (OARS): geen advies, geen "je moet". Stel een open vraag die zijn eigen reden voor verandering naar boven haalt, en erken expliciet wat al goed gaat (affirmatie).',
    "oplossingsgericht": 'Gebruik oplossingsgericht coachen: stel een schaalvraag ("waar sta je nu op een schaal van 0-10, en wat maakt dat je niet lager zit") en een uitzonderingsvraag over een moment dat het al wél lukte.',
    "cgt": "Gebruik een lichte CGT-geïnformeerde reflectie: benoem het patroon tussen wat er gebeurde en de reactie, zonder te diagnosticeren, en test één realistischer werkhypothese.",
    "act": "Gebruik ACT: erken dat onzekerheid of ongemak aanwezig mag zijn, en vraag welke kleine, aan zijn waarden verbonden actie daar toch bij past.",
    "systemisch": "Gebruik een systemische vraag: wat in de holding eromheen (team, projecten, verwachtingen) speelt mee, en welke rol neemt Vincent daar zelf in als spanning ontstaat.",
    "strengths": "Gebruik strengths-based coachen: vraag naar een concreet moment dat het al lukte en welke omstandigheden dat mogelijk maakten, en hoe dat patroon nu te gebruiken is.",
}

_DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
_MONTH_NAMES = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
                "augustus", "september", "oktober", "november", "december"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


# ── Context ──────────────────────────────────────────────────────────────

async def load_context() -> Dict[str, Any]:
    """Alles wat de coach nodig heeft, uit de échte tabellen — nooit een aanname."""
    ensure_schema()
    rit = get_rituals_service()
    today_m = rit.get_morning(_today())
    yesterday_m = rit.get_morning(_yesterday())
    streaks = rit.get_streaks()

    energy_log = list_energy_log(days=30)
    lessons = list_lessons()

    holding = None
    try:
        from ..coach_bridge.context import build_holding_context  # in-process, geen HTTP meer nodig
        holding = await build_holding_context()
    except Exception as e:  # noqa: BLE001
        log.warning("[coach] holding-context laden mislukt: %s", e)

    return {
        "today": today_m or {},
        "yesterday": yesterday_m,
        "streak": streaks.get("morning", 0),
        "energy_log": energy_log,
        "lessons": lessons,
        "holding": holding,
    }


def recent_morning_energy(days: int = 5) -> List[int]:
    rit = get_rituals_service()
    out: List[int] = []
    d = datetime.now()
    for _ in range(days):
        m = rit.get_morning(d.strftime("%Y-%m-%d"))
        if m and m.get("energy_level") is not None:
            out.append(int(m["energy_level"]))
        d -= timedelta(days=1)
    return out


# ── Energie-attributie ──────────────────────────────────────────────────

def save_energy_log(date: str, entries: List[Dict[str, str]]) -> int:
    ensure_schema()
    created = 0
    with get_conn() as conn:
        for e in entries:
            activity = (e.get("activity") or "").strip()
            direction = e.get("direction")
            if not activity or direction not in ("gain", "cost"):
                continue
            conn.execute(
                "INSERT INTO coach_energy_log (date, activity, category, direction, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (date, activity, e.get("category", ""), direction, _now_iso()),
            )
            created += 1
    return created


def list_energy_log(days: int = 30) -> List[Dict[str, Any]]:
    ensure_schema()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, activity, category, direction FROM coach_energy_log "
            "WHERE date >= ? ORDER BY date DESC, id DESC LIMIT 60",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Techniekkeuze (deterministisch, geen LLM) ───────────────────────────

def choose_technique(ctx: Dict[str, Any]) -> Tuple[str, str]:
    today_energy = ctx["today"].get("energy_level")
    yesterday_energy = ctx["yesterday"].get("energy_level") if ctx["yesterday"] else None
    streak = ctx["streak"]
    energy_log = ctx["energy_log"]

    energy_drop = 0
    if today_energy is not None and yesterday_energy is not None:
        energy_drop = today_energy - yesterday_energy

    cost_count = sum(1 for e in energy_log if e["direction"] == "cost")
    gain_count = sum(1 for e in energy_log if e["direction"] == "gain")

    if (today_energy if today_energy is not None else 10) <= 3 and streak >= 3:
        return "oplossingsgericht", "Lage energie ondanks een lopende streak — eerst een schaalvraag, geen advies."
    if energy_drop <= -3:
        return "cgt", "Scherpe energieval t.o.v. gisteren — patroon eerst zichtbaar maken."
    if cost_count >= 3 and cost_count > gain_count:
        return "mi", "Meer activiteiten die energie kosten dan geven deze periode — verandering vergt eigen motivatie, geen advies van buiten."
    if streak <= 1 and (today_energy if today_energy is not None else 5) >= 7:
        return "strengths", "Hoge energie, nieuw begonnen ritueel — bouwen op wat al werkt."
    holding = ctx.get("holding") or {}
    if holding.get("waarheidsaudit", {}).get("blokkerend", 0) >= 5 or holding.get("gemiste_runs", {}).get("aantal_jobs", 0) >= 3:
        return "systemisch", "De holding staat onder duidelijke druk — kijk naar wat er om Vincent heen speelt, niet alleen naar zijn agenda."
    return "grow", "Geen uitschieter — een gewone dag verdient een gewone scherpe vraag."


# ── Prompt ───────────────────────────────────────────────────────────────

def _holding_block(holding: Optional[Dict[str, Any]]) -> str:
    if not holding:
        return ""
    parts: List[str] = []
    proj = holding.get("projecten") or {}
    stilstaand = proj.get("stilstaand") or []
    if stilstaand:
        namen = ", ".join(p["project"] for p in stilstaand)
        parts.append(f"{len(stilstaand)} van {proj.get('totaal', '?')} projecten in de holding staan er zwak voor ({namen}).")
    audit = holding.get("waarheidsaudit") or {}
    if audit.get("blokkerend", 0) > 0:
        parts.append(f"{audit['blokkerend']} blokkerende bevinding(en) in de waarheidsaudit.")
    gaps = holding.get("gemiste_runs") or {}
    if gaps.get("aantal_jobs", 0) > 0:
        parts.append(f"{gaps['aantal_jobs']} taak/taken staan al even stil.")
    agenda = holding.get("agenda") or {}
    if agenda.get("status") == "ok" and "vandaag_afspraken" in agenda:
        parts.append(f"Vandaag {agenda['vandaag_afspraken']} afspraak/afspraken op de agenda.")
    if not parts:
        return ""
    return "\nDE HOLDING VANDAAG (van Iris, gebruik dit als aanleiding, niet als opdracht):\n" + "\n".join(f"- {p}" for p in parts) + "\n"


def build_prompt(ctx: Dict[str, Any], technique: str) -> str:
    now = datetime.now()
    day_name = _DAY_NAMES[now.weekday()]
    # Geen strftime("%-d %B"): %-d is een Unix-only extensie en crasht op Windows
    # (waar deze backend draait) met "Invalid format string".
    today_date = f"{now.day} {_MONTH_NAMES[now.month - 1]}"

    today = ctx["today"]
    yesterday = ctx["yesterday"]

    lessons = ctx["lessons"]
    lessons_block = (
        "\n".join(f"- {l['insight']} (trefkans {round(l['confidence'] * 100)}%)" for l in lessons)
        if lessons else "Nog geen geleerde patronen — dit kan een van de eerste analyses zijn."
    )

    energy_log = ctx["energy_log"][:10]

    def _energy_line(e: Dict[str, Any]) -> str:
        richting = "+ gaf energie" if e["direction"] == "gain" else "- kostte energie"
        categorie = f" ({e['category']})" if e.get("category") else ""
        return f"- {e['date']}: {richting} — {e['activity']}{categorie}"

    energy_block = (
        "\n".join(_energy_line(e) for e in energy_log)
        if energy_log else "Nog geen energie-attributie ingevuld."
    )

    return f"""Je bent De Sparringpartner: Vincents persoonlijke business- én welzijnscoach, niet gescheiden maar gecombineerd — precies zoals dat in de praktijk voor een ondernemer met een holding (WeAreImpact, met projecten als BewaardVoorJou eronder) altijd door elkaar loopt. Je bent niet zijn klantenservice-bot en je coacht niemand anders dan hem.

Belangrijke grens: je diagnosticeert of behandelt nooit psychische of medische klachten. Zie je een signaal van aanhoudende uitputting, burn-out, angst of iets vergelijkbaars dat langer dan een paar dagen aanhoudt, benoem dat expliciet en adviseer professionele hulp — coach dan niet verder met een techniek.

GEKOZEN TECHNIEK VOOR VANDAAG: {TECHNIQUE_LABELS[technique]}
{_TECHNIQUE_INSTRUCTIONS[technique]}

SESSIE VAN VANDAAG ({today_date}, {day_name}):
- Energie: {today.get('energy_level', 'onbekend')}/10
- Slaap: {today.get('sleep_quality', 'onbekend')}/10
- Wakker om: {today.get('wake_time') or 'onbekend'}
- Intentie: "{today.get('intentie', '')}"
- Huidige streak: {ctx['streak']} dag(en)

{f"GISTEREN: energie {yesterday.get('energy_level')}/10, slaap {yesterday.get('sleep_quality')}/10" if yesterday else "GISTEREN: geen sessie."}

RECENTE ENERGIE-ATTRIBUTIE (wat gaf/kostte energie):
{energy_block}

GELEERDE PATRONEN OVER VINCENT (gebruik deze, herhaal ze niet letterlijk):
{lessons_block}
{_holding_block(ctx.get("holding"))}
Schrijf een coach-reflectie van 120-180 woorden in het Nederlands, in de jij-vorm, warm maar scherp. Volg de aangewezen techniek. Eindig met precies één concrete vraag aan Vincent — geen waslijst, geen bullet points, gewone paragrafen."""


# ── LLM-laag (Claude eerst, Hermes-terugval — zelfde patroon als iris/service.py) ──

async def _llm(prompt: str, max_tokens: int = 400) -> str:
    from ..chat import claude as claude_service
    if claude_service.is_configured():
        try:
            out = (await claude_service.get_response(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="",
                max_tokens=max_tokens,
                purpose="coach",
            )).strip()
            if out:
                return out
            log.warning("[coach] Claude gaf een lege respons — terugval op Hermes")
        except Exception as e:  # noqa: BLE001
            log.warning("[coach] Claude niet beschikbaar (%s) — terugval op Hermes", e)
    try:
        from ..chat import hermes as hermes_service
        full = ""
        async for chunk in hermes_service.stream_response(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="",
            max_tokens=max_tokens,
            purpose="coach",
        ):
            full += chunk
        return full.strip()
    except Exception as e:  # noqa: BLE001
        log.warning("[coach] Hermes ook niet beschikbaar: %s", e)
        return ""


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


# ── Geheugen ─────────────────────────────────────────────────────────────

def remember_lesson(pattern_key: str, technique: str, insight: str) -> None:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, times_confirmed FROM coach_lessons WHERE pattern_key = ?", (pattern_key,)
        ).fetchone()
        if row:
            confirmed = row["times_confirmed"] + 1
            confidence = (confirmed + 1) / (confirmed + 2)  # Laplace smoothing
            conn.execute(
                "UPDATE coach_lessons SET insight=?, technique=?, times_confirmed=?, "
                "confidence=?, updated_at=? WHERE id=?",
                (insight, technique, confirmed, confidence, _now_iso(), row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO coach_lessons (pattern_key, technique, insight, confidence, "
                "times_confirmed, active, created_at, updated_at) VALUES (?, ?, ?, 0.5, 1, 1, ?, ?)",
                (pattern_key, technique, insight, _now_iso(), _now_iso()),
            )


def list_lessons() -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, pattern_key, technique, insight, confidence, times_confirmed, "
            "times_disproven, updated_at FROM coach_lessons WHERE active = 1 "
            "ORDER BY confidence DESC, updated_at DESC LIMIT 25"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["technique_label"] = TECHNIQUE_LABELS.get(d["technique"], d["technique"])
        out.append(d)
    return out


# ── Orkestratie ──────────────────────────────────────────────────────────

async def run_analysis() -> Dict[str, Any]:
    """Eén coach-reflectie: cijfers eerst (deterministisch gekozen techniek), dan pas
    het LLM-oordeel erbovenop — zelfde volgorde als Iris' briefing."""
    ctx = await load_context()

    if ctx["today"].get("energy_level") is None:
        return {
            "ok": False, "status": 409,
            "error": "Nog geen ochtendritueel van vandaag — de coach heeft een echte meting nodig, geen aanname.",
        }

    technique, reason = choose_technique(ctx)
    prompt = build_prompt(ctx, technique)

    analysis = await _llm(prompt)
    if not analysis:
        return {
            "ok": False, "status": 502,
            "error": "De coach-reflectie kon niet gegenereerd worden. De cijfers hieronder blijven wel geldig.",
            "technique": technique, "reason": reason,
        }

    pattern_key = f"{technique}:{_slugify(reason)}"
    remember_lesson(pattern_key, technique, reason)

    return {
        "ok": True,
        "technique": technique,
        "technique_label": TECHNIQUE_LABELS[technique],
        "reason": reason,
        "analysis": analysis,
        "streak": ctx["streak"],
    }


# ── Proactief signaal (deterministisch, gebruikt door de scheduler-job) ──

def detect_proactive_signal() -> Dict[str, Any]:
    energy = recent_morning_energy(days=5)
    energy_log = list_energy_log(days=14)

    if len(energy) >= 3 and all(e <= 4 for e in energy[:3]):
        return {
            "signal": True,
            "pattern_key": "cgt:energie-drie-dagen-laag",
            "message": "Je energie staat nu drie dagen op rij laag. Niets om nu meteen op te lossen — maar wat zou vandaag al iets makkelijker maken?",
        }

    cost_count = sum(1 for e in energy_log if e["direction"] == "cost")
    gain_count = sum(1 for e in energy_log if e["direction"] == "gain")
    if cost_count >= 4 and cost_count - gain_count >= 3:
        return {
            "signal": True,
            "pattern_key": "mi:energie-kost-meer-dan-geeft",
            "message": f"De laatste tijd noteer je vaker wat energie kost dan wat het geeft ({cost_count} tegen {gain_count}). Wat zou dat evenwicht al een klein beetje terugbrengen?",
        }

    return {"signal": False, "pattern_key": "", "message": ""}


def _whatsapp_already_sent(pattern_key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM coach_whatsapp_sent WHERE pattern_key=? AND date=?",
            (pattern_key, _today()),
        ).fetchone()
    return row is not None


def _whatsapp_mark_sent(pattern_key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO coach_whatsapp_sent (pattern_key, date, sent_at) VALUES (?, ?, ?)",
            (pattern_key, _today(), _now_iso()),
        )


async def check_and_send_whatsapp() -> bool:
    """Retourneert True als er een proactief bericht is verstuurd.

    Haalt het signaal op bij mijn-ondernemers-os (bron van waarheid sinds de
    multi-tenant-migratie — Vincents rituelen leven daar in Neon, niet meer
    lokaal in ImpactOS' eigen tabellen) via coach_bridge/whatsapp.py, in
    plaats van de lokale detect_proactive_signal() hierboven te herberekenen
    tegen mogelijk verouderde lokale data. ensure_schema() blijft nodig voor
    de lokale dedupe-tabel (coach_whatsapp_sent) hieronder — dát blijft wél
    lokaal, alleen de signaal-brón verhuist. Verzenden zelf loopt nog wel via
    de bestaande bridge naar Iris Remote (WhatsApp-token leeft alleen daar,
    zie CLAUDE.md §14e-b) — dat verandert niet."""
    from ..coach_bridge.whatsapp import fetch_remote_signal

    ensure_schema()
    result = await fetch_remote_signal()
    if not result or not result["signal"]:
        return False
    if _whatsapp_already_sent(result["pattern_key"]):
        return False

    from ..bridge import service as bridge_service
    if not bridge_service.enabled():
        return False
    ok = await bridge_service.send_whatsapp_reminder(result["message"])
    if ok:
        _whatsapp_mark_sent(result["pattern_key"])
        log.info("[coach-whatsapp] proactief bericht verstuurd (%s)", result["pattern_key"])
        return True
    log.warning("[coach-whatsapp] versturen mislukt, blijft openstaan: %s", result["pattern_key"])
    return False
