"""
Agenda-integratie voor Agent OS — backend-dispatcher.

CALENDAR_BACKEND kiest de agenda-provider per instance: 'google'
(serviceaccount, default — WeAreImpact) of 'outlook' (Microsoft Graph via
dezelfde delegated OAuth-login als Mail — voor klanten die zelf Outlook
gebruiken, bv. Nicole @ WE SHAPE THE FUTURE, zie service_outlook.py).

Beide modules leveren exact dezelfde functiesignaturen (is_configured,
get_week_events, get_events_range, get_busy_times, block_time,
get_today_summary, verify_access, explain_error), dus alles daarboven
(agent.py, bridge/context.py, scheduler.py, iris/service.py, health/router.py)
blijft ongewijzigd — één laag verandert, precies zoals bij een gate die een
gevaarlijke actie afschermt (zie de les in CLAUDE.md 7a-bis over "twee
antwoorden op dezelfde vraag"): er mag hier maar één implementatie tegelijk
actief zijn per instance, gekozen bij het opstarten, niet per aanroep.
"""
from ...shared.config import CALENDAR_BACKEND

if CALENDAR_BACKEND == "outlook":
    from .service_outlook import (
        is_configured,
        get_week_events,
        get_events_range,
        get_busy_times,
        block_time,
        get_today_summary,
        verify_access,
        explain_error,
        client_email,
    )
else:
    from .service_google import (
        is_configured,
        get_week_events,
        get_events_range,
        get_busy_times,
        block_time,
        get_today_summary,
        verify_access,
        explain_error,
        client_email,
    )

__all__ = [
    "is_configured",
    "get_week_events",
    "get_events_range",
    "get_busy_times",
    "block_time",
    "get_today_summary",
    "verify_access",
    "explain_error",
    "client_email",
]
