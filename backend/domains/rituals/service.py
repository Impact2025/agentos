"""
Persoonlijke rituelen — service-laag: CRUD op ochtend/avond/weekstart/
weekreview/wins/focus/doelen, plus afgeleide functies (status vandaag,
streaks, briefing-context voor Iris).

Overgezet uit impactreis3 (waar dit alleen client-side in localStorage stond).
Datum-/weeknummerlogica is 1-op-1 overgenomen van weekflow.service.ts, maar nu
server-side zodat Iris — en een herstart van ImpactOS — er zicht op hebben.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from .models import ensure_schema

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _iso_week(d: Optional[datetime] = None) -> tuple[int, int]:
    """(jaar, weeknummer) — zelfde berekening als getCurrentWeekNumber() in
    weekflow.service.ts (kalenderweek, niet strikt ISO 8601)."""
    d = d or datetime.now()
    start = datetime(d.year, 1, 1)
    days = (d - start).days
    # JS Date.getDay(): zondag=0..zaterdag=6; Python weekday(): maandag=0..zondag=6
    js_day = 0 if start.weekday() == 6 else start.weekday() + 1
    week = -(-(days + js_day + 1) // 7)  # ceil((days + start.getDay() + 1) / 7)
    return d.year, week


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def _parse_json_fields(d: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Decodeert de JSON-kolommen van een rij. Corrupte/lege waarden vallen
    terug op een leeg object van hetzelfde type als de default in models.py
    (steeds '[]' of '{}'), nooit een exception naar de aanroeper."""
    out = dict(d)
    for f in fields:
        raw = out.get(f)
        if not raw:
            out[f] = []
            continue
        try:
            out[f] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            out[f] = []
    return out


class RitualsService:
    def __init__(self):
        ensure_schema()

    # ---------------------------------------------------------------- morning
    def save_morning(self, date: str, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO ritual_morning
                   (date, intentie, affirmatie, dankbaarheid, energy_level,
                    sleep_quality, sleep_time, wake_time, focus_blok1, focus_blok2, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                     intentie=excluded.intentie, affirmatie=excluded.affirmatie,
                     dankbaarheid=excluded.dankbaarheid, energy_level=excluded.energy_level,
                     sleep_quality=excluded.sleep_quality, sleep_time=excluded.sleep_time,
                     wake_time=excluded.wake_time, focus_blok1=excluded.focus_blok1,
                     focus_blok2=excluded.focus_blok2""",
                (date, data.get("intentie", ""), data.get("affirmatie", ""),
                 json.dumps(data.get("dankbaarheid", [])), int(data.get("energyLevel", 7)),
                 int(data.get("sleepQuality", 7)), data.get("sleepTime", ""),
                 data.get("wakeTime", ""), json.dumps(data.get("focusBlok1", {})),
                 json.dumps(data.get("focusBlok2", {})), _now()),
            )
        return self.get_morning(date) or {}

    def get_morning(self, date: str) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_morning WHERE date = ?", (date,)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["dankbaarheid", "focus_blok1", "focus_blok2"])

    # ---------------------------------------------------------------- evening
    def save_evening(self, date: str, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO ritual_evening
                   (date, what_went_well, biggest_win, what_learned, challenges,
                    energy_level, tomorrow_top3, gratitude, adhd_scores, focus_check, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                     what_went_well=excluded.what_went_well, biggest_win=excluded.biggest_win,
                     what_learned=excluded.what_learned, challenges=excluded.challenges,
                     energy_level=excluded.energy_level, tomorrow_top3=excluded.tomorrow_top3,
                     gratitude=excluded.gratitude, adhd_scores=excluded.adhd_scores,
                     focus_check=excluded.focus_check""",
                (date, data.get("whatWentWell", ""), data.get("biggestWin", ""),
                 data.get("whatLearned", ""), data.get("challenges", ""),
                 int(data.get("energyLevel", 5)), json.dumps(data.get("tomorrowTop3", [])),
                 data.get("gratitude", ""), json.dumps(data.get("adhdScores", {})),
                 json.dumps(data.get("focusCheck", [])), _now()),
            )
        return self.get_evening(date) or {}

    def get_evening(self, date: str) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_evening WHERE date = ?", (date,)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["tomorrow_top3", "adhd_scores", "focus_check"])

    def get_morning_focus_blocks(self, date: str) -> List[Dict[str, Any]]:
        """Focusblokken van de ochtend van `date`, alleen de ingevulde — voor de
        avond-terugkoppeling ("is focusblok 1 gelukt?"). Geen ochtend of een
        leeg blok levert een lege lijst, nooit een placeholder-vraag over niets."""
        morning = self.get_morning(date)
        if not morning:
            return []
        out = []
        for key in ("focus_blok1", "focus_blok2"):
            blok = morning.get(key) or {}
            onderwerp = (blok.get("onderwerp") or "").strip()
            if onderwerp:
                out.append({"onderwerp": onderwerp})
        return out

    # ----------------------------------------------------------- weekly start
    def save_weekly_start(self, year: int, week: int, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO ritual_weekly_start
                   (year, week_number, week_intention, main_goals, focus_areas,
                    learning_goal, obstacles, success_metrics, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(year, week_number) DO UPDATE SET
                     week_intention=excluded.week_intention, main_goals=excluded.main_goals,
                     focus_areas=excluded.focus_areas, learning_goal=excluded.learning_goal,
                     obstacles=excluded.obstacles, success_metrics=excluded.success_metrics""",
                (year, week, data.get("weekIntention", ""), json.dumps(data.get("mainGoals", [])),
                 json.dumps(data.get("focusAreas", {})), data.get("learningGoal", ""),
                 data.get("obstacles", ""), data.get("successMetrics", ""), _now()),
            )
        return self.get_weekly_start(year, week) or {}

    def get_weekly_start(self, year: int, week: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ritual_weekly_start WHERE year = ? AND week_number = ?",
                (year, week)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["main_goals", "focus_areas"])

    # ---------------------------------------------------------- weekly review
    def save_weekly_review(self, year: int, week: int, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO ritual_weekly_review
                   (year, week_number, wins, challenges, learnings, productivity_score,
                    energy_score, carry_forward, leave_behind, growth_moment,
                    what_gave, what_learned, how_contributed, how_make_better, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(year, week_number) DO UPDATE SET
                     wins=excluded.wins, challenges=excluded.challenges,
                     learnings=excluded.learnings, productivity_score=excluded.productivity_score,
                     energy_score=excluded.energy_score, carry_forward=excluded.carry_forward,
                     leave_behind=excluded.leave_behind, growth_moment=excluded.growth_moment,
                     what_gave=excluded.what_gave, what_learned=excluded.what_learned,
                     how_contributed=excluded.how_contributed, how_make_better=excluded.how_make_better""",
                (year, week, json.dumps(data.get("wins", [])), data.get("challenges", ""),
                 data.get("learnings", ""), int(data.get("productivityScore", 7)),
                 int(data.get("energyScore", 7)), data.get("carryForward", ""),
                 data.get("leaveBehind", ""), data.get("growthMoment", ""),
                 data.get("whatGave", ""), data.get("whatLearned", ""),
                 data.get("howContributed", ""), data.get("howMakeBetter", ""), _now()),
            )
        return self.get_weekly_review(year, week) or {}

    def get_weekly_review(self, year: int, week: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ritual_weekly_review WHERE year = ? AND week_number = ?",
                (year, week)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["wins"])

    # ---------------------------------------------------------------- wins
    def add_win(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO ritual_wins (title, description, category, impact_level, date, tags, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (data["title"], data.get("description", ""), data.get("category", "personal"),
                 int(data.get("impactLevel", 1)), data.get("date") or _today(),
                 json.dumps(data.get("tags", [])), _now()),
            )
            win_id = cur.lastrowid
        return self.get_win(win_id) or {}

    def get_win(self, win_id: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_wins WHERE id = ?", (win_id,)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["tags"])

    def list_wins(self, limit: int = 50, category: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM ritual_wins WHERE category = ? ORDER BY date DESC, id DESC LIMIT ?",
                    (category, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ritual_wins ORDER BY date DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_parse_json_fields(_row_to_dict(r), ["tags"]) for r in rows]

    def delete_win(self, win_id: int) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM ritual_wins WHERE id = ?", (win_id,))

    # ------------------------------------------------------------- focus
    def start_focus_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO ritual_focus_sessions (date, start_time, goal, completed, created_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (data.get("date") or _today(), data["startTime"], data.get("goal", ""), _now()),
            )
            fid = cur.lastrowid
        return self.get_focus_session(fid) or {}

    def complete_focus_session(self, fid: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            conn.execute("UPDATE ritual_focus_sessions SET completed = 1 WHERE id = ?", (fid,))
        return self.get_focus_session(fid)

    def get_focus_session(self, fid: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_focus_sessions WHERE id = ?", (fid,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_focus_sessions(self, date: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with get_conn() as conn:
            if date:
                rows = conn.execute(
                    "SELECT * FROM ritual_focus_sessions WHERE date = ? ORDER BY start_time ASC",
                    (date,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ritual_focus_sessions ORDER BY date DESC, start_time DESC LIMIT ?",
                    (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------- doelen
    def add_goal(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO ritual_goals
                   (title, description, why, pain_if_not, pleasure_if_done, next_actions,
                    category, progress, completed, deadline, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                (data["title"], data.get("description", ""), data.get("why", ""),
                 data.get("painIfNot", ""), data.get("pleasureIfDone", ""),
                 json.dumps([a for a in data.get("nextActions", []) if a]),
                 data.get("category", "personal"), data.get("deadline", ""), _now(), _now()),
            )
            gid = cur.lastrowid
        return self.get_goal(gid) or {}

    def update_goal(self, gid: int, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.get_goal(gid)
        if not existing:
            return None
        fields = {
            "title": patch.get("title", existing["title"]),
            "description": patch.get("description", existing["description"]),
            "why": patch.get("why", existing["why"]),
            "pain_if_not": patch.get("painIfNot", existing["pain_if_not"]),
            "pleasure_if_done": patch.get("pleasureIfDone", existing["pleasure_if_done"]),
            "next_actions": json.dumps(patch.get("nextActions", existing["next_actions"])),
            "category": patch.get("category", existing["category"]),
            "progress": int(patch.get("progress", existing["progress"])),
            "completed": 1 if patch.get("completed", bool(existing["completed"])) else 0,
            "deadline": patch.get("deadline", existing["deadline"]),
        }
        with get_conn() as conn:
            conn.execute(
                """UPDATE ritual_goals SET title=?, description=?, why=?, pain_if_not=?,
                   pleasure_if_done=?, next_actions=?, category=?, progress=?, completed=?,
                   deadline=?, updated_at=? WHERE id=?""",
                (*fields.values(), _now(), gid),
            )
        return self.get_goal(gid)

    def get_goal(self, gid: int) -> Optional[Dict[str, Any]]:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_goals WHERE id = ?", (gid,)).fetchone()
        if not row:
            return None
        return _parse_json_fields(_row_to_dict(row), ["next_actions"])

    def list_goals(self, include_completed: bool = True) -> List[Dict[str, Any]]:
        with get_conn() as conn:
            if include_completed:
                rows = conn.execute("SELECT * FROM ritual_goals ORDER BY completed ASC, created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ritual_goals WHERE completed = 0 ORDER BY created_at DESC").fetchall()
        return [_parse_json_fields(_row_to_dict(r), ["next_actions"]) for r in rows]

    def delete_goal(self, gid: int) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM ritual_goals WHERE id = ?", (gid,))

    # ------------------------------------------------------------ afgeleid
    def get_today_status(self) -> Dict[str, Any]:
        today = _today()
        year, week = _iso_week()
        return {
            "date": today,
            "morning_done": self.get_morning(today) is not None,
            "evening_done": self.get_evening(today) is not None,
            "weekly_start_done": self.get_weekly_start(year, week) is not None,
            "weekly_review_done": self.get_weekly_review(year, week) is not None,
            "year": year,
            "week_number": week,
        }

    def _streak(self, table: str) -> int:
        """Aantal opeenvolgende dagen (vanaf vandaag terugtellend, of vanaf
        gisteren als vandaag nog niet ingevuld is) met een rij in `table`."""
        with get_conn() as conn:
            rows = conn.execute(f"SELECT date FROM {table} ORDER BY date DESC").fetchall()
        dates = {r["date"] for r in rows}
        cursor = datetime.now()
        if cursor.strftime("%Y-%m-%d") not in dates:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor.strftime("%Y-%m-%d") in dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def get_streaks(self) -> Dict[str, int]:
        return {
            "morning": self._streak("ritual_morning"),
            "evening": self._streak("ritual_evening"),
        }

    def get_recent_wins(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self.list_wins(limit=limit)

    def get_focus_completion(self, date: str) -> Optional[Dict[str, Any]]:
        """Hoeveel van de ochtend-focusblokken zijn 's avonds als 'gelukt' afgevinkt.
        None (niet 0) zolang er geen avondritueel is — 'nog niet afgesloten' is
        iets anders dan 'niets gehaald'."""
        evening = self.get_evening(date)
        if evening is None:
            return None
        checks = evening.get("focus_check") or []
        if not checks:
            return None
        done = sum(1 for c in checks if c.get("done"))
        return {"total": len(checks), "done": done}

    def get_open_goals(self) -> List[Dict[str, Any]]:
        return self.list_goals(include_completed=False)

    # ----------------------------------------------------- verplichte gate
    def get_next_required(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Bepaalt het volgende verplichte ritueel op basis van dag-type en een
        17:00-time-gate. Server-side vertaling van weekflow.service.ts uit
        impactreis3 (daar draaide dit client-side op localStorage, dus de gate
        was onzichtbaar voor ImpactOS/Iris). Retourneert exact dezelfde shape als
        `getNextRequiredRitual()` in impactreis3:

          {path, title, isRequired, isAvailable, reason}

        `path` wijst naar een frontend-actie i.p.v. een URL (SPA heeft geen
        routes): 'morning' | 'evening' | 'weekly-start' | 'weekly-review'.
        `isAvailable` is False als het ritueel verplicht is maar (nog) niet mag
        (avond vóór 17:00) — dan toont de UI een zachte banner, geen blokkade.
        Geen verplicht ritueel → {"isRequired": False, "next": None}.
        """
        now = now or datetime.now()
        js_day = now.weekday()  # ma=0 .. zo=6
        day_type = "monday" if js_day == 0 else ("weekday" if 1 <= js_day <= 4 else "weekend")
        after_5pm = now.hour >= 17
        status = self.get_today_status()

        # Maandag: weekstart verplicht vóór alles
        if day_type == "monday" and not status["weekly_start_done"]:
            return {
                "isRequired": True,
                "next": {"path": "weekly-start", "title": "Week Start",
                         "isRequired": True, "isAvailable": True,
                         "reason": "Start je nieuwe week met intentie"},
            }

        # Werkdagen (ma-vr): ochtend → avond
        if day_type in ("weekday", "monday"):
            if not status["morning_done"]:
                return {
                    "isRequired": True,
                    "next": {"path": "morning", "title": "Ochtend Ritueel",
                             "isRequired": True, "isAvailable": True,
                             "reason": "Begin je dag met focus en intentie"},
                }
            if not status["evening_done"]:
                if after_5pm:
                    return {
                        "isRequired": True,
                        "next": {"path": "evening", "title": "Avond Ritueel",
                                 "isRequired": True, "isAvailable": True,
                                 "reason": "Sluit je dag af met reflectie"},
                    }
                return {
                    "isRequired": True,
                    "next": {"path": "evening", "title": "Avond Ritueel",
                             "isRequired": True, "isAvailable": False,
                             "reason": "Beschikbaar na 17:00"},
                }

        # Weekend: weekreview
        if day_type == "weekend" and not status["weekly_review_done"]:
            return {
                "isRequired": True,
                "next": {"path": "weekly-review", "title": "Week Review",
                         "isRequired": True, "isAvailable": True,
                         "reason": "Sluit je week af met reflectie"},
            }

        return {"isRequired": False, "next": None}

    def get_briefing_context(self) -> Dict[str, Any]:
        """Klein, foutbestendig bundeltje voor Iris' promptcontext. Wordt
        altijd door iris/service.py in een try/except aangeroepen — deze
        functie hoeft dus geen eigen defensieve laag te hebben, maar mag ook
        nooit een halve/corrupte state teruggeven."""
        status = self.get_today_status()
        streaks = self.get_streaks()
        wins = self.get_recent_wins(limit=3)
        goals = self.get_open_goals()
        evening = self.get_evening(status["date"])
        morning = self.get_morning(status["date"])
        return {
            "status": status,
            "streaks": streaks,
            "recent_wins": [{"title": w["title"], "date": w["date"]} for w in wins],
            "open_goals": [{"title": g["title"], "progress": g["progress"]} for g in goals],
            "energy_today": (evening or morning or {}).get("energy_level"),
            "focus_completion_today": self.get_focus_completion(status["date"]),
        }


_service: Optional[RitualsService] = None


def get_service() -> RitualsService:
    global _service
    if _service is None:
        _service = RitualsService()
    return _service
