"""Claude Code als denkende partner — headless, in een eigen werkmap.

Waarom dit naast `chat/claude.py` bestaat: dat is één API-call over de
OpenModel-gateway, die per token betaalt en alleen tekst terug kan geven. Dit
start de Claude Code-CLI (Pro-abonnement, geen tokenkosten) op een map met
bestanden, en Claude mág daar wérken: hij leest de snapshot, schrijft een
Python-script, draait het, ziet dat zijn idee over drie jaar historie niet
standhoudt, en verwerpt het zélf vóórdat het een voorstel wordt. Dat kan een
enkele API-call per definitie niet.

Vier dingen die dit veilig en eerlijk houden:

(a) **Een eigen werkmap per run.** Claude Code draait nooit in de projectmap
    van Impact OS. Hij ziet de snapshot en schrijft zijn uitvoer; hij kan niet
    bij `.env`, de database of de publicatiecode. De map is meteen het
    artefact: wie later vraagt "waarom dit voorstel?" krijgt het script, de
    uitkomst en de redenering te zien.

(b) **Dit is een CLI, geen server.** Een run duurt minuten, dus hij hoort
    thuis in een achtergrondtaak — nooit in een request-handler. Er zit een
    harde timeout op en een dagteller, want het Pro-abonnement heeft
    vensterlimieten die je niet wilt opbranden aan een lus.

(c) **Uitvallen mag nooit stil.** Ontbreekt de CLI, is het venster op, of
    loopt de run in de timeout, dan komt dat als `beschikbaar=False` mét reden
    terug. De aanroeper valt dan terug op de gateway en labelt zijn uitkomst
    als terugval — precies zoals `iris_reports.llm_ok=0`. Een terugval die
    zich voordoet als het echte werk is erger dan geen werk.

(d) **Claude Code beslist niets.** Hij levert een voorstel af in een bestand.
    Alles wat daarna gebeurt — risicotoets, gate, goedkeuring — draait in
    Python, waar het te lezen en te testen is.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from .config import (
    CLAUDE_CODE_BIN, CLAUDE_CODE_ENABLED, CLAUDE_CODE_MAX_RUNS_PER_DAY, CLAUDE_CODE_TIMEOUT,
)
from .database import get_conn
from .outcomes import log_llm_usage

logger = logging.getLogger(__name__)

# Tekst in de uitvoer die op een bereikte abonnementslimiet wijst. Dit is geen
# storing maar een grens: opnieuw proberen helpt niet, wachten of ingrijpen wel.
# 'spend limit' staat erbij omdat de CLI op 3 aug 2026 meldde "You've hit your
# monthly spend limit · raise it at claude.ai/…" — die viel door geen enkel
# ander signaal en werd daardoor geclassificeerd als een gewone storing, met
# de bijbehorende nutteloze suggestie om het nog eens te proberen.
_LIMIET_SIGNALEN = (
    "usage limit", "rate limit", "quota", "limit reached", "try again later",
    "spend limit", "monthly limit",
)


class Resultaat(NamedTuple):
    ok: bool
    tekst: str                    # de laatste tekstuitvoer van Claude
    reden: str = ""               # waaróm het misging (leeg als het goed ging)
    limiet_bereikt: bool = False  # abonnementsvenster op → later opnieuw, niet harder
    workspace: str = ""
    duur_ms: int = 0
    bestanden: Optional[List[str]] = None


def _bin() -> str:
    return CLAUDE_CODE_BIN or "claude"


def beschikbaar() -> Dict[str, Any]:
    """Kan Claude Code nú gebruikt worden? Altijd mét reden, zodat de
    aanroeper hem kan doorgeven aan een mens in plaats van stil terug te
    vallen."""
    if not CLAUDE_CODE_ENABLED:
        return {"ok": False, "reden": "CLAUDE_CODE_ENABLED staat uit in .env"}
    pad = shutil.which(_bin())
    if not pad:
        return {"ok": False,
                "reden": f"'{_bin()}' staat niet in het PATH van dit proces — "
                         "zet CLAUDE_CODE_BIN in .env op het volledige pad."}
    gebruikt = runs_vandaag()
    if gebruikt >= CLAUDE_CODE_MAX_RUNS_PER_DAY:
        return {"ok": False,
                "reden": f"dagelijkse limiet bereikt ({gebruikt}/{CLAUDE_CODE_MAX_RUNS_PER_DAY})"}
    return {"ok": True, "reden": "", "pad": pad, "runs_vandaag": gebruikt}


def runs_vandaag() -> int:
    """Hoeveel Claude Code-sessies vandaag zijn gestart. Telt uit `llm_usage`,
    dezelfde tabel als elke andere modelaanroep — één plek waar het verbruik
    van het hele systeem staat."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM llm_usage WHERE backend = 'claude_code' "
                "AND date(created_at) = date('now')"
            ).fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def maak_werkmap(naam: str, basis: Optional[Path] = None) -> Path:
    """Een verse map per run: `data/claude_code/<naam>/<datum>-<kort-id>/`."""
    basis = basis or (Path(__file__).parent.parent.parent / "data" / "claude_code")
    map_ = basis / naam / f"{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    map_.mkdir(parents=True, exist_ok=True)
    return map_


def _parse_uitvoer(stdout: str) -> Dict[str, Any]:
    """De CLI geeft met --output-format json één object terug, maar oudere
    versies streamen een lijst. Beide vormen komen voor; een derde vorm (kale
    tekst) hoort niet te bestaan maar mag deze functie niet laten vallen."""
    stdout = (stdout or "").strip()
    if not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Kale tekst: gebruik hem als resultaat in plaats van te doen alsof er
        # niets is. Een leeg antwoord en een onparseerbaar antwoord zijn niet
        # hetzelfde probleem.
        return {"result": stdout, "is_error": False}
    if isinstance(data, list):
        laatste = next((d for d in reversed(data) if isinstance(d, dict) and d.get("result")), None)
        return laatste or (data[-1] if data and isinstance(data[-1], dict) else {})
    return data if isinstance(data, dict) else {}


def run(
    prompt: str,
    *,
    workspace: Path,
    doel: str = "claude_code",
    allowed_tools: str = "Read,Write,Edit,Glob,Grep,Bash",
    timeout: Optional[int] = None,
    verwacht_bestand: str = "",
) -> Resultaat:
    """Draai één Claude Code-sessie in `workspace` en geef het resultaat terug.

    Synchroon en blokkerend — roep dit aan via `asyncio.to_thread` of vanuit
    een achtergrondtaak, nooit vanuit een request-handler.

    `verwacht_bestand`: het bestand dat de sessie moet opleveren. Ontbreekt het
    na een schijnbaar geslaagde run, dan is de run níét geslaagd. Een model dat
    "klaar" zegt zonder artefact is precies het patroon dat dit project overal
    afvangt.
    """
    status = beschikbaar()
    if not status["ok"]:
        return Resultaat(False, "", status["reden"],
                         limiet_bereikt="limiet" in status["reden"],
                         workspace=str(workspace))

    workspace.mkdir(parents=True, exist_ok=True)
    cmd = [
        _bin(), "-p", prompt,
        "--output-format", "json",
        "--allowedTools", allowed_tools,
        "--permission-mode", "acceptEdits",
    ]
    # De sessie erft de omgeving, maar niet de sleutels van Impact OS: Claude
    # Code heeft ze niet nodig en een werkmap is geen plek voor secrets.
    omgeving = {k: v for k, v in os.environ.items()
                if not any(g in k.upper() for g in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"))}

    logger.info("[claude-code] sessie '%s' start in %s", doel, workspace)
    try:
        proc = subprocess.run(
            cmd, cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout or CLAUDE_CODE_TIMEOUT, env=omgeving,
        )
    except subprocess.TimeoutExpired:
        reden = (f"Claude Code-sessie liep in de timeout van "
                 f"{timeout or CLAUDE_CODE_TIMEOUT}s zonder af te ronden.")
        logger.warning("[claude-code] %s", reden)
        log_llm_usage(backend="claude_code", model="claude-code-cli", route=doel,
                      status="error", error="timeout")
        return Resultaat(False, "", reden, workspace=str(workspace))
    except FileNotFoundError:
        return Resultaat(False, "", f"'{_bin()}' niet gevonden bij het starten van de sessie.",
                         workspace=str(workspace))

    data = _parse_uitvoer(proc.stdout)
    tekst = (data.get("result") or "").strip()
    duur = int(data.get("duration_ms") or 0)
    gebruik = data.get("usage") or {}
    fout_tekst = (proc.stderr or "").strip()
    mislukt = proc.returncode != 0 or bool(data.get("is_error"))

    limiet = any(sig in (tekst + " " + fout_tekst).lower() for sig in _LIMIET_SIGNALEN)

    # Abonnementslimiet (Claude Code Pro monthly spend limit): geen harde fout,
    # maar een grens die om de reset-tijd wacht. Schrijf status='quota' zodat de
    # healthcheck deze niet telt als LLM-fout én llm_quota_backoff_active() de
    # route pauzeert tot de volgende run — precies als de OpenModel 403-quota
    # handling in agent_runner.py. Behoudt wel de limiet-signalering voor
    # analyst.py (via resultaat.limiet_bereikt) zodat de gateway-terugval blijft.
    if mislukt and limiet:
        log_llm_usage(
            backend="claude_code", model="claude-code-cli", route=doel,
            prompt_tokens=int(gebruik.get("input_tokens") or 0),
            completion_tokens=int(gebruik.get("output_tokens") or 0),
            total_tokens=int(gebruik.get("input_tokens") or 0) + int(gebruik.get("output_tokens") or 0),
            status="quota",
            error=(fout_tekst or tekst)[:400],
        )
    else:
        log_llm_usage(
            backend="claude_code", model="claude-code-cli", route=doel,
            prompt_tokens=int(gebruik.get("input_tokens") or 0),
            completion_tokens=int(gebruik.get("output_tokens") or 0),
            total_tokens=int(gebruik.get("input_tokens") or 0) + int(gebruik.get("output_tokens") or 0),
            status="error" if mislukt else "ok",
            error=(fout_tekst or tekst)[:400] if mislukt else "",
        )

    if mislukt:
        reden = fout_tekst or tekst or f"exit-code {proc.returncode} zonder uitleg"
        logger.warning("[claude-code] sessie '%s' mislukt: %s", doel, reden[:300])
        return Resultaat(False, tekst, reden[:600], limiet_bereikt=limiet,
                         workspace=str(workspace), duur_ms=duur)

    if verwacht_bestand and not (workspace / verwacht_bestand).exists():
        # Geslaagde exit-code, geen artefact. Dat is geen succes.
        return Resultaat(
            False, tekst,
            f"De sessie rondde af maar leverde '{verwacht_bestand}' niet op.",
            workspace=str(workspace), duur_ms=duur,
        )

    bestanden = sorted(p.name for p in workspace.iterdir() if p.is_file())
    logger.info("[claude-code] sessie '%s' klaar in %dms (%d bestanden)", doel, duur, len(bestanden))
    return Resultaat(True, tekst, "", workspace=str(workspace), duur_ms=duur, bestanden=bestanden)
