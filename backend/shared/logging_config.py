"""Eén logging-opzet voor de hele server.

Zonder dit blijft de root-logger op WARNING staan (uvicorn configureert alleen
zijn eigen loggers), waardoor elke `logger.info()` uit de domeinen en de
scheduler spoorloos verdwijnt. Hier zetten we:

  * root op INFO (of $LOG_LEVEL), met tijdstempels;
  * een meegroeiende maar begrensde `logs/agentos.log` (5 MB x 5);
  * een filter op de access-log die het geratel van de UI-pollers wegneemt,
    zodat wat er wél toe doet leesbaar blijft.

Het stdout-spoor blijft intact — `agentos_service.cmd` vangt dat op in
`agentos.log`. Dat bestand blijft de ruwe vangnet-log (ook voor tracebacks die
buiten logging om op stderr belanden); `logs/agentos.log` is de leesbare.
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent.parent / "logs"

# Endpoints die de SPA elke paar seconden pollt. Een geslaagde poll is geen
# informatie; een mislukte wel — die blijft dus staan.
_POLL_PATHS = (
    "/api/goals",
    "/api/action-center",
    "/api/strategist/control-room",
    "/api/status",
    "/api/scheduler/status",
    "/api/conveyor/status",
)

_configured = False


class _AccessNoiseFilter(logging.Filter):
    """Laat 2xx-antwoorden op de poll-endpoints niet door."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn.access formatteert met (client, method, path, http_version, status).
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path, status = args[2], args[4]
        try:
            is_ok = 200 <= int(status) < 300
        except (TypeError, ValueError):
            return True
        return not (is_ok and str(path).startswith(_POLL_PATHS))


def _rotating_handler(fmt: logging.Formatter) -> logging.Handler | None:
    """Het begrensde logbestand, of None als dat niet kan/hoort.

    Onder pytest wordt dezelfde app geïmporteerd; die testrun hoort niet in het
    logboek van de draaiende server terecht te komen.
    """
    if "pytest" in sys.modules:
        return None
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_DIR / "agentos.log", maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
    except OSError as e:  # bv. een volle of read-only schijf — stdout is dan genoeg
        print(f"[WARN] Kon logbestand niet openen ({e}) — alleen stdout", file=sys.stderr)
        return None
    handler.setFormatter(fmt)
    return handler


def setup_logging() -> None:
    """Idempotent — veilig om meerdere keren aan te roepen (tests, reload)."""
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(stream)

    rotating = _rotating_handler(fmt)
    if rotating is not None:
        root.addHandler(rotating)
        # uvicorn's loggers hebben propagate=False en hun eigen stdout-handler; ze
        # zouden dus nooit in het roterende bestand belanden. Hang die er los aan.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).addHandler(rotating)

    if os.getenv("LOG_ACCESS_FILTER", "1") != "0":
        logging.getLogger("uvicorn.access").addFilter(_AccessNoiseFilter())

    # Bibliotheken die op INFO te spraakzaam zijn.
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
