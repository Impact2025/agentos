"""
Finance-rapport orchestrator: draait de financiële expert-agent (mét tools) tot een
compleet rapport en levert het af in dashboard + Obsidian + e-mail.

- run_daily_report()  : het strategische €10.000-dagrapport.
- run_weekly_report() : het diepe wekelijkse macro-/liquiditeitsrapport.

Beide hergebruiken het patroon van analytics_reporter, maar draaien op de agentic
tool-loop (get_market_data / fetch_financial_news / web_search) i.p.v. een kale
LLM-call, zodat het rapport op geverifieerde data is gebouwd.
"""
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ...shared.config import OBSIDIAN_VAULT_PATH
from ...domains.chat import service as memory_service
from ...shared.agent_runner import run_agent
from ...shared.email_service import send_report, is_configured as email_configured
from .prompts import FINANCE_DAILY_SYSTEM, FINANCE_WEEKLY_SYSTEM

# Het rapport leunt op meerdere tool-rondes; geef de agent ruimte per beurt.
_MAX_TOKENS = 8192


async def _agent_complete(system: str, user: str) -> str:
    """Draai de agentic tool-loop tot het einde en vang alle tekst op tot één rapport."""
    messages = [{"role": "user", "content": user}]
    chunks: list[str] = []
    last_error: Optional[str] = None
    async for event in run_agent(messages, system, agent="finance", max_tokens=_MAX_TOKENS):
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
    print(f"[Finance] Start {kind}-rapport…")
    try:
        analysis = await _agent_complete(system, prompt)
        print(f"[Finance] {kind}-analyse gereed ({len(analysis)} tekens)")
    except Exception as e:
        msg = f"{kind}-analyse mislukt: {e}"
        print(f"[Finance] {msg}")
        return {"success": False, "error": msg}

    if not analysis:
        print(f"[Finance] {kind}-rapport leeg — overgeslagen")
        return {"success": False, "error": "leeg rapport"}

    results: dict = {"success": True, "kind": kind}

    try:
        sid = _save_dashboard(session_name, prompt, analysis)
        results["session_id"] = sid
        print(f"[Finance] Sessie aangemaakt: {sid}")
    except Exception as e:
        print(f"[Finance] Dashboard opslaan mislukt: {e}")

    try:
        note = _save_obsidian(analysis, obsidian_subfolder, obsidian_title, obsidian_tags)
        if note:
            results["obsidian_note"] = str(note)
            print(f"[Finance] Obsidian note: {note}")
    except Exception as e:
        print(f"[Finance] Obsidian opslaan mislukt: {e}")

    if email_configured():
        try:
            body = f"{email_subject}\n{'=' * 50}\n\n{analysis}"
            sent = send_report(email_subject, body)
            results["email_sent"] = sent
            print(f"[Finance] E-mail {'verstuurd' if sent else 'mislukt'}")
        except Exception as e:
            print(f"[Finance] E-mail versturen mislukt: {e}")
            results["email_sent"] = False
    else:
        results["email_sent"] = False
        print("[Finance] SMTP niet geconfigureerd, e-mail overgeslagen")

    print(f"[Finance] {kind}-rapport voltooid")
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
        email_subject=f"📈 Finance Dagrapport — {today}",
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
        email_subject=f"🌊 Finance Weekrapport (macro & liquiditeit) — {week_label}",
    )
