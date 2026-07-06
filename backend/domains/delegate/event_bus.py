"""
Event Bus — in-memory async pub/sub voor het asynchroon terugstromen van
subagent-resultaten naar de UI.

Achtergrond-workers leven langer dan het oorspronkelijke chat-request. Ze kunnen
hun output dus niet via de normale chat-SSE terugsturen. In plaats daarvan
publiceren ze 'self-contained' berichten op deze bus; de UI abonneert zich op
één globale SSE-stream (/api/delegate/stream) en rendert elk bericht als een
zelfstandige chat-bubble / dashboard-kaart.

Bewust simpel (één proces, één event loop). Wil je later schalen naar meerdere
workers/processen, vervang de implementatie door Redis pub/sub of NATS — de
publieke functies (publish / subscribe / unsubscribe) blijven gelijk.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

# Elke subscriber krijgt zijn eigen queue. We houden een lijst van actieve
# queues bij; publish() fan-out't naar allemaal.
_subscribers: List["asyncio.Queue[Dict[str, Any]]"] = []

# Kleine ring-buffer met de laatste events, zodat een UI die net (her)verbindt
# de recent afgeronde workers alsnog ziet i.p.v. ze te missen.
_recent: List[Dict[str, Any]] = []
_RECENT_MAX = 50


def subscribe() -> "asyncio.Queue[Dict[str, Any]]":
    """Open een nieuw abonnement. Retourneert een queue waar events op binnenkomen."""
    q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: "asyncio.Queue[Dict[str, Any]]") -> None:
    """Sluit een abonnement (bij SSE-disconnect)."""
    try:
        _subscribers.remove(q)
    except ValueError:
        pass


def publish(event: Dict[str, Any]) -> None:
    """Publiceer een event naar alle actieve subscribers (non-blocking).

    Veilig om vanuit elke async-context aan te roepen. Een trage of dode
    subscriber blokkeert de producer nooit (we gebruiken put_nowait).
    """
    _recent.append(event)
    if len(_recent) > _RECENT_MAX:
        del _recent[: len(_recent) - _RECENT_MAX]

    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - queues zijn onbegrensd
            pass


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    """Geef de laatste N events terug (voor late subscribers)."""
    return _recent[-limit:]
