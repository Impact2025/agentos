"""
Finance-rapport orchestrator: draait de financiële expert-agent (mét tools) tot een
compleet rapport en levert het af in dashboard + Obsidian + e-mail.

- run_daily_report()  : het strategische €10.000-dagrapport.
- run_weekly_report() : het diepe wekelijkse macro-/liquiditeitsrapport.

Beide hergebruiken het patroon van analytics_reporter, maar draaien op de agentic
tool-loop (get_market_data / fetch_financial_news / web_search) i.p.v. een kale
LLM-call, zodat het rapport op geverifieerde data is gebouwd.
"""
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ...shared.config import OBSIDIAN_VAULT_PATH
from ...domains.chat import service as memory_service
from ...shared.agent_runner import run_agent
from ...shared.email_service import send_report, is_configured as email_configured
from ...shared.failures import describe_exception, note_success, should_escalate
from ...shared.outcomes import log_outcome
from .prompts import FINANCE_DAILY_SYSTEM, FINANCE_WEEKLY_SYSTEM

logger = logging.getLogger(__name__)

# Het rapport leunt op meerdere tool-rondes; geef de agent ruimte per beurt.
_MAX_TOKENS = 8192


async def _agent_complete(system: str, user: str) -> str:
    """Draai de agentic tool-loop tot het einde en vang alle tekst op tot één rapport."""
    messages = [{"role": "user", "content": user}]
    chunks: list[str] = []
    last_error: Optional[str] = None
    async for event in run_agent(
        messages, system, agent="finance", max_tokens=_MAX_TOKENS, purpose="finance",
    ):
        etype = event.get("type")
        if etype == "text":
            chunks.append(event.get("text", ""))
        elif etype == "error":
            last_error = event.get("message", "onbekende agent-fout")
    text = "".join(chunks).strip()
    if not text and last_error:
        raise RuntimeError(last_error)
    return text


def _save_obsidian(analysis: str, subfolder: str, title: str, tags: list[str]) -> Optional[Path]:
    if not OBSIDIAN_VAULT_PATH:
        return None
    vault = Path(OBSIDIAN_VAULT_PATH)
    if not vault.exists():
        return None
    folder = vault / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"{title}.md"
    frontmatter = (
        f"---\n"
        f"date: {date.today().isoformat()}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"gegenereerd_door: Finance Strateeg\n"
        f"---\n\n"
    )
    note.write_text(frontmatter + analysis, encoding="utf-8")
    return note


def _save_dashboard(session_name: str, prompt: str, analysis: str) -> str:
    session = memory_service.create_session(name=session_name, agent="finance")
    sid = session["id"]
    memory_service.add_message(sid, "user", prompt)
    memory_service.add_message(sid, "assistant", analysis)
    return sid


async def _run_report(
    *, kind: str, system: str, prompt: str, session_name: str,
    obsidian_subfolder: str, obsidian_title: str, obsidian_tags: list[str],
    email_subject: str,
) -> dict:
    logger.info("[Finance] Start %s-rapport…", kind)
    # Een mislukt rapport was tot 2 aug 2026 alleen een print() plus een
    # dict met success=False: de scheduler-job slaagde, er kwam geen kaart, en
    # een rapport dat weken niet meer verscheen was nergens te zien. Dat is
    # precies het patroon van het dode Meta-token. Nu loopt het via
    # failures.py — een nachtelijke blip escaleert niet, een structurele storing
    # meteen — en elke geslaagde run sluit de reeks weer.
    faalsleutel = f"finance_report:{kind}"
    try:
        analysis = await _agent_complete(system, prompt)
        logger.info("[Finance] %s-analyse gereed (%d tekens)", kind, len(analysis))
    except Exception as e:
        msg = f"{kind}-analyse mislukt: {describe_exception(e)}"
        logger.warning("[Finance] %s", msg)
        if should_escalate(faalsleutel, e):
            log_outcome(
                "Finance Expert", f"{kind}rapport", msg,
                next_step="Controleer de LLM-gateway (OpenModel-credits/quota) en draai het "
                          f"rapport opnieuw via GET /api/finance/{'daily' if kind == 'dag' else 'weekly'}-report.",
                status="error",
            )
        return {"success": False, "error": msg}

    if not analysis:
        logger.warning("[Finance] %s-rapport leeg — overgeslagen", kind)
        # Een leeg antwoord is geen uitzondering maar wél een mislukking: er is
        # vandaag geen rapport, en dat mag niet als geslaagde run doorgaan.
        if should_escalate(faalsleutel, RuntimeError("leeg rapport")):
            log_outcome(
                "Finance Expert", f"{kind}rapport",
                f"Het {kind}rapport kwam meerdere keren leeg terug uit de agent-loop.",
                next_step="Controleer of de tools (get_market_data / fetch_financial_news) nog "
                          "antwoorden en of de gateway niet op quota staat.",
                status="error",
            )
        return {"success": False, "error": "leeg rapport"}
    note_success(faalsleutel)

    results: dict = {"success": True, "kind": kind}

    try:
        sid = _save_dashboard(session_name, prompt, analysis)
        results["session_id"] = sid
        logger.info("[Finance] Sessie aangemaakt: %s", sid)
    except Exception as e:
        logger.warning("[Finance] Dashboard opslaan mislukt: %s", e)

    try:
        note = _save_obsidian(analysis, obsidian_subfolder, obsidian_title, obsidian_tags)
        if note:
            results["obsidian_note"] = str(note)
            logger.info("[Finance] Obsidian note: %s", note)
    except Exception as e:
        logger.warning("[Finance] Obsidian opslaan mislukt: %s", e)

    if email_configured():
        try:
            body = f"{email_subject}\n{'=' * 50}\n\n{analysis}"
            sent = send_report(email_subject, body)
            results["email_sent"] = sent
            logger.info("[Finance] E-mail %s", "verstuurd" if sent else "mislukt")
        except Exception as e:
            logger.warning("[Finance] E-mail versturen mislukt: %s", e)
            results["email_sent"] = False
    else:
        results["email_sent"] = False
        logger.info("[Finance] SMTP niet geconfigureerd, e-mail overgeslagen")

    logger.info("[Finance] %s-rapport voltooid", kind)
    # Uitkomstkaart mét artefact: een run die "klaar" claimt hoort aanwijsbaar
    # iets te hebben opgeleverd (CLAUDE.md). Zonder vault is de chat-sessie het
    # artefact — nooit een lege verwijzing.
    artefact = results.get("obsidian_note") or (
        f"/chat/{results['session_id']}" if results.get("session_id") else ""
    )
    log_outcome(
        "Finance Expert", f"{kind}rapport",
        f"{kind.capitalize()}rapport gegenereerd ({len(analysis)} tekens)"
        + (" en gemaild" if results.get("email_sent") else ""),
        artifact=artefact,
        next_step="Lees het rapport; concrete posities lopen via de Beursmeester-voorstellen.",
    )
    return results


async def run_daily_report() -> dict:
    today = date.today().isoformat()
    prompt = (
        f"Stel het strategische €10.000-dagrapport op voor vandaag ({today}). "
        "Volg je vaste rapportstructuur en haal eerst de actuele marktdata en het nieuws op "
        "voordat je adviseert."
    )
    return await _run_report(
        kind="dag",
        system=FINANCE_DAILY_SYSTEM,
        prompt=prompt,
        session_name=f"Finance Dagrapport {today}",
        obsidian_subfolder="Financieel/Dagrapporten",
        obsidian_title=f"Dagrapport {today}",
        obsidian_tags=["financieel", "rapport", "dagrapport", "beleggen"],
        email_subject=f"Finance Dagrapport — {today}",
    )


async def run_weekly_report() -> dict:
    today = date.today()
    iso = today.isocalendar()
    week_label = f"{iso[0]}-W{iso[1]:02d}"
    prompt = (
        f"Stel het wekelijkse macro- & liquiditeitsrapport op (week {week_label}, "
        f"peildatum {today.isoformat()}). Behandel alle vier de verplichte delen — "
        "wereldwijde liquiditeit, institutionele geldstromen, macro-correlaties en de "
        "samenvatting van topbanken & analisten — en vertaal het door naar de "
        "€10.000-portefeuille. Haal eerst data en bronnen op via je tools."
    )
    return await _run_report(
        kind="week",
        system=FINANCE_WEEKLY_SYSTEM,
        prompt=prompt,
        session_name=f"Finance Weekrapport {week_label}",
        obsidian_subfolder="Financieel/Weekrapporten",
        obsidian_title=f"Weekrapport {week_label}",
        obsidian_tags=["financieel", "rapport", "weekrapport", "macro", "liquiditeit"],
        email_subject=f"Finance Weekrapport (macro & liquiditeit) — {week_label}",
    )
