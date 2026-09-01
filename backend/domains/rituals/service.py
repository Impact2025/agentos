"""
Persoonlijke rituelen — service-laag: CRUD op ochtend/avond/weekstart/
weekreview/wins/focus, plus afgeleide functies (status vandaag, streaks,
briefing-context voor Iris). `doelen` (Robbins-stijl why/pain/pleasure) blijft
lokaal, zie onderaan — mijn-ondernemers-os heeft daar geen equivalent voor.

Praat via de gedeelde bridge (shared/bridge_client.py) met mijn-ondernemers-os
(Next.js/Neon) — dié is de bron van waarheid voor Vincents dagelijkse rituelen,
niet een lokale SQLite-kopie hier. Dit was eerder omgekeerd (zie git-historie
van dit bestand): ImpactOS hield zijn eigen kopie bij omdat het toen alleen bij
ritueeldata kon via de browser-localStorage van mijn-ondernemers-os. Nu die app
een volwaardige API heeft, is die kopie overbodig en zorgde 'm voor precies het
probleem waar dit voor gebouwd was: twee databronnen die uit elkaar liepen.

Twee verschillende faalstrategieën, bewust:
  - Leespaden die de verplichte ritueel-gate voeden (get_today_status,
    get_next_required, get_streaks, get_briefing_context) falen OPEN: bij een
    onbereikbare bridge loggen we en geven "niets verplicht"/lege data terug.
    Een netwerkstoring mag Vincent nooit buitensluiten uit zijn eigen Control
    Room — zelfde filosofie als coach_bridge/whatsapp.py:fetch_remote_signal().
  - Schrijfpaden (save_morning, save_evening, add_win, ...) falen LUID: de
    HTTPException van bridge_client.call_mijn_ondernemers_os() propageert door
    naar de frontend, zodat Vincent ziet dat iets niet is opgeslagen i.p.v. dat
    stilzwijgend te verliezen.

Datum-/weeknummerlogica is nog steeds 1-op-1 overgenomen van weekflow.service.ts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared.bridge_client import call_mijn_ondernemers_os
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
    js_day = 0 if start.weekday() == 6 else start.weekday() + 1
    week = -(-(days + js_day + 1) // 7)
    return d.year, week


async def _bridge_get(path: str, fallback: Any) -> Any:
    """Voor leespaden: bij elke fout (niet geconfigureerd, onbereikbaar, foutstatus)
    loggen en de fallback teruggeven — nooit de aanroeper (en dus de gate) blokkeren."""
    try:
        return await call_mijn_ondernemers_os("GET", path)
    except Exception as e:  # noqa: BLE001
        log.warning("[rituals] bridge-read faalde, val terug op leeg (%s): %s", path, e)
        return fallback


class RitualsService:
    def __init__(self):
        ensure_schema()  # alleen nog nodig voor ritual_goals

    # ---------------------------------------------------------------- morning
    async def save_morning(self, date: str, data: Dict[str, Any]) -> Dict[str, Any]:
        await call_mijn_ondernemers_os("POST", "/api/logs", json={"type": "morning", "date": date, **data})
        return await self.get_morning(date) or {}

    async def get_morning(self, date: str) -> Optional[Dict[str, Any]]:
        payload = await self._get_log_payload("morning", date)
        if payload is None:
            return None
        return {
            "date": date,
            "intentie": payload.get("intentie", ""),
            "affirmatie": payload.get("affirmatie", ""),
            "dankbaarheid": payload.get("dankbaarheid", []),
            "energy_level": payload.get("energyLevel", 7),
            "sleep_quality": payload.get("sleepQuality", 7),
            "sleep_time": payload.get("sleepTime", ""),
            "wake_time": payload.get("wakeTime", ""),
            "focus_blok1": payload.get("focusBlok1", {}),
            "focus_blok2": payload.get("focusBlok2", {}),
        }

    # ---------------------------------------------------------------- evening
    async def save_evening(self, date: str, data: Dict[str, Any]) -> Dict[str, Any]:
        await call_mijn_ondernemers_os("POST", "/api/logs", json={"type": "evening", "date": date, **data})
        return await self.get_evening(date) or {}

    async def get_evening(self, date: str) -> Optional[Dict[str, Any]]:
        payload = await self._get_log_payload("evening", date)
        if payload is None:
            return None
        return {
            "date": date,
            "what_went_well": payload.get("whatWentWell", ""),
            "biggest_win": payload.get("biggestWin", ""),
            "what_learned": payload.get("whatLearned", ""),
            "challenges": payload.get("challenges", ""),
            "energy_level": payload.get("energyLevel", 5),
            "tomorrow_top3": payload.get("tomorrowTop3", []),
            "gratitude": payload.get("gratitude", ""),
            "adhd_scores": payload.get("adhdScores", {}),
            "focus_check": payload.get("focusCheck", []),
        }

    @staticmethod
    async def _get_log_payload(log_type: str, date: str) -> Optional[Dict[str, Any]]:
        """Rauwe camelCase-vorm zoals mijn-ondernemers-os 'm opslaat (daily_logs.data) —
        alleen de get_morning/get_evening hierboven normaliseren naar de snake_case-vorm
        die de rest van ImpactOS (coach/service.py, iris) al verwachtte van vóór de bridge."""
        rows = await _bridge_get(f"/api/logs?type={log_type}&date={date}", [])
        if not rows:
            return None
        payload = rows[0].get("data") or {}
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        return payload

    async def get_morning_focus_blocks(self, date: str) -> List[Dict[str, Any]]:
        """Focusblokken van de ochtend van `date`, alleen de ingevulde — voor de
        avond-terugkoppeling ("is focusblok 1 gelukt?")."""
        morning = await self.get_morning(date)
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
    async def save_weekly_start(self, year: int, week: int, data: Dict[str, Any]) -> Dict[str, Any]:
        await call_mijn_ondernemers_os(
            "POST", "/api/weekly-reviews",
            json={"weekNumber": week, "type": "weekly-start", "year": year, "data": data},
        )
        return await self.get_weekly_start(year, week) or {}

    async def get_weekly_start(self, year: int, week: int) -> Optional[Dict[str, Any]]:
        # mijn-ondernemers-os' weekly-start page wrapt de echte velden een niveau dieper:
        # row.data = {type: 'weekly-start', year, data: {weekIntention, mainGoals, ...}} —
        # zie src/app/weekly-start/page.tsx:handleSave / src/app/api/weekly-reviews/route.ts.
        rows = await _bridge_get(f"/api/weekly-reviews?weekNumber={week}", [])
        candidates = [r for r in rows if (r.get("data") or {}).get("type") == "weekly-start"]
        if not candidates:
            return None
        return (candidates[0].get("data") or {}).get("data") or {}

    # ----------------------------------------------------------- weekly review
    async def save_weekly_review(self, year: int, week: int, data: Dict[str, Any]) -> Dict[str, Any]:
        await call_mijn_ondernemers_os(
            "POST", "/api/weekly-reviews", json={"weekNumber": week, **data},
        )
        return await self.get_weekly_review(year, week) or {}

    async def get_weekly_review(self, year: int, week: int) -> Optional[Dict[str, Any]]:
        rows = await _bridge_get(f"/api/weekly-reviews?weekNumber={week}", [])
        candidates = [r for r in rows if (r.get("data") or {}).get("type") != "weekly-start"]
        if not candidates:
            return None
        return candidates[0].get("data") or {}

    # ---------------------------------------------------------------- wins
    async def add_win(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await call_mijn_ondernemers_os("POST", "/api/wins", json={
            "title": data["title"],
            "description": data.get("description", ""),
            "category": data.get("category", "personal"),
            "impactLevel": int(data.get("impactLevel", 1)),
            "date": data.get("date") or _today(),
            "tags": data.get("tags", []),
        })

    async def list_wins(self, limit: int = 50, category: Optional[str] = None) -> List[Dict[str, Any]]:
        path = f"/api/wins?limit={limit}" if not category else f"/api/wins?limit={limit}&category={category}"
        return await _bridge_get(path, [])

    async def delete_win(self, win_id: int) -> None:
        await call_mijn_ondernemers_os("DELETE", f"/api/wins/{win_id}")

    # ------------------------------------------------------------- focus
    async def start_focus_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await call_mijn_ondernemers_os("POST", "/api/focus", json={
            "date": data.get("date") or _today(),
            "startTime": data["startTime"],
            "goal": data.get("goal", ""),
        })

    async def complete_focus_session(self, fid: int) -> Optional[Dict[str, Any]]:
        try:
            return await call_mijn_ondernemers_os("PUT", f"/api/focus/{fid}", json={"completed": True})
        except Exception as e:  # noqa: BLE001
            log.warning("[rituals] focus-sessie afronden mislukt (%s): %s", fid, e)
            return None

    async def list_focus_sessions(self, date: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        path = f"/api/focus?date={date}" if date else "/api/focus"
        return await _bridge_get(path, [])

    # ------------------------------------------------------------- doelen
    # Robbins-stijl persoonlijke doelen (why/pain/pleasure) blijven lokaal —
    # mijn-ondernemers-os heeft hier geen equivalent voor (bewust buiten scope).
    def add_goal(self, data: Dict[str, Any]) -> Dict[str, Any]:
        import json
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
        import json
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
        import json
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM ritual_goals WHERE id = ?", (gid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["next_actions"] = json.loads(d.get("next_actions") or "[]")
        except (TypeError, ValueError):
            d["next_actions"] = []
        return d

    def list_goals(self, include_completed: bool = True) -> List[Dict[str, Any]]:
        import json
        with get_conn() as conn:
            if include_completed:
                rows = conn.execute("SELECT * FROM ritual_goals ORDER BY completed ASC, created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ritual_goals WHERE completed = 0 ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["next_actions"] = json.loads(d.get("next_actions") or "[]")
            except (TypeError, ValueError):
                d["next_actions"] = []
            out.append(d)
        return out

    def delete_goal(self, gid: int) -> None:
        with get_conn() as conn:
            conn.execute("DELETE FROM ritual_goals WHERE id = ?", (gid,))

    # ------------------------------------------------------------ afgeleid
    async def get_today_status(self) -> Dict[str, Any]:
        today = _today()
        year, week = _iso_week()
        morning, evening, weekly_start, weekly_review = (
            await self.get_morning(today),
            await self.get_evening(today),
            await self.get_weekly_start(year, week),
            await self.get_weekly_review(year, week),
        )
        return {
            "date": today,
            "morning_done": morning is not None,
            "evening_done": evening is not None,
            "weekly_start_done": weekly_start is not None,
            "weekly_review_done": weekly_review is not None,
            "year": year,
            "week_number": week,
        }

    async def _streak(self, ritual_type: str) -> int:
        """Aantal opeenvolgende dagen (vanaf vandaag terugtellend, of vanaf
        gisteren als vandaag nog niet ingevuld is) met een log van dit type."""
        rows = await _bridge_get(f"/api/logs?type={ritual_type}", [])
        dates = {r.get("date_string") or r.get("dateString") for r in rows}
        cursor = datetime.now()
        if cursor.strftime("%Y-%m-%d") not in dates:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor.strftime("%Y-%m-%d") in dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    async def get_streaks(self) -> Dict[str, int]:
        return {
            "morning": await self._streak("morning"),
            "evening": await self._streak("evening"),
        }

    async def get_recent_wins(self, limit: int = 5) -> List[Dict[str, Any]]:
        return await self.list_wins(limit=limit)

    async def get_focus_completion(self, date: str) -> Optional[Dict[str, Any]]:
        """Hoeveel van de ochtend-focusblokken zijn 's avonds als 'gelukt' afgevinkt.
        None (niet 0) zolang er geen avondritueel is."""
        evening = await self.get_evening(date)
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
    async def get_next_required(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Bepaalt het volgende verplichte ritueel op basis van dag-type en een
        17:00-time-gate. Retourneert exact dezelfde shape als
        `getNextRequiredRitual()` in mijn-ondernemers-os:

          {path, title, isRequired, isAvailable, reason}

        Bij een onbereikbare bridge (via get_today_status's fail-open leespaden)
        wordt hier nooit geblokkeerd — hoogstens ten onrechte "niets verplicht".
        """
        now = now or datetime.now()
        js_day = now.weekday()
        day_type = "monday" if js_day == 0 else ("weekday" if 1 <= js_day <= 4 else "weekend")
        after_5pm = now.hour >= 17
        status = await self.get_today_status()

        if day_type == "monday" and not status["weekly_start_done"]:
            return {
                "isRequired": True,
                "next": {"path": "weekly-start", "title": "Week Start",
                         "isRequired": True, "isAvailable": True,
                         "reason": "Start je nieuwe week met intentie"},
            }

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

        if day_type == "weekend" and not status["weekly_review_done"]:
            return {
                "isRequired": True,
                "next": {"path": "weekly-review", "title": "Week Review",
                         "isRequired": True, "isAvailable": True,
                         "reason": "Sluit je week af met reflectie"},
            }

        return {"isRequired": False, "next": None}

    async def get_briefing_context(self) -> Dict[str, Any]:
        """Klein, foutbestendig bundeltje voor Iris' promptcontext. Wordt altijd
        door iris/service.py in een try/except aangeroepen, maar de leespaden
        hieronder falen zelf ook al open (lege data), dus dit geeft nooit een
        halve/corrupte state terug — hoogstens een lege."""
        status = await self.get_today_status()
        streaks = await self.get_streaks()
        wins = await self.get_recent_wins(limit=3)
        goals = self.get_open_goals()
        evening = await self.get_evening(status["date"])
        morning = await self.get_morning(status["date"])
        energy = (evening or morning or {}).get("energy_level")
        return {
            "status": status,
            "streaks": streaks,
            "recent_wins": [{"title": w.get("title"), "date": w.get("date")} for w in wins],
            "open_goals": [{"title": g["title"], "progress": g["progress"]} for g in goals],
            "energy_today": energy,
            "focus_completion_today": await self.get_focus_completion(status["date"]),
        }


_service: Optional[RitualsService] = None


def get_service() -> RitualsService:
    global _service
    if _service is None:
        _service = RitualsService()
    return _service
