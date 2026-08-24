"""Eén logging-opzet voor de hele server.

Zonder dit blijft de root-logger op WARNING staan (uvicorn configureert alleen
zijn eigen loggers), waardoor elke `logger.info()` uit de domeinen en de
scheduler spoorloos verdwijnt. Hier zetten we:

  * root op INFO (of $LOG_LEVEL), met tijdstempels;
  * een meegroeiende maar begrensde `logs/impactos.log` (5 MB x 5);
  * een filter op de access-log die het geratel van de UI-pollers wegneemt,
    zodat wat er wél toe doet leesbaar blijft.

Het stdout-spoor blijft intact — `impactos_service.cmd` vangt dat op in
`impactos.log`. Dat bestand blijft de ruwe vangnet-log (ook voor tracebacks die
buiten logging om op stderr belanden); `logs/impactos.log` is de leesbare.
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = (
    Path(os.environ["IMPACTOS_LOG_DIR"]) if os.getenv("IMPACTOS_LOG_DIR")
    else Path(os.environ["AGENTOS_LOG_DIR"]) if os.getenv("AGENTOS_LOG_DIR")
    else Path(__file__).parent.parent.parent / "logs"
)

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
            _LOG_DIR / "impactos.log", maxBytes=5 * 1024 * 1024, backupCount=5,
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
        # uvicorn's loggers hebben hun eigen stdout-handler; zonder dit belanden
        # ze nooit in het roterende bestand. Hang die er dus los aan.
        #
        # `propagate = False` erbij (16 aug 2026), en dat is niet cosmetisch:
        # `uvicorn.error` is een kind van `uvicorn`, dus zonder dit schrijft één
        # bericht zich twee keer weg — één keer via de eigen handler en één keer
        # via die van de ouder. Gemeten stond "Started server process [9652]"
        # letterlijk dubbel in impactos.log, wat zich bij het onderzoeken van een
        # herstart-race voordeed als twéé draaiende servers. Een logboek waarop
        # geteld wordt, moet elke regel precies één keer bevatten — anders is elke
        # telling erop een gok, en dit bestand telt op access-regels.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            if rotating not in lg.handlers:
                lg.addHandler(rotating)
            lg.propagate = False

    if os.getenv("LOG_ACCESS_FILTER", "1") != "0":
        logging.getLogger("uvicorn.access").addFilter(_AccessNoiseFilter())

    # Bibliotheken die op INFO te spraakzaam zijn.
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
