"""
Weekrapport orchestrator: GA4 data → Hermes analyse → Obsidian + e-mail + dashboard.
"""
from datetime import date
from pathlib import Path
from typing import Optional

from ...shared.config import OBSIDIAN_VAULT_PATH, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...domains.chat import service as memory_service
from .ga_service import fetch_weekly_data, is_configured as ga_configured
from ...shared.email_service import send_report, is_configured as email_configured

# Het rapport draait op het "Analytics Analist"-expertprofiel (model + brein) als
# dat bestaat; anders op deze ingebouwde fallback-persona. De rapportstructuur
# blijft in beide gevallen gelijk (zie _REPORT_STRUCTURE).
ANALYST_PROFILE_NAME = "Analytics Analist"

_REPORT_STRUCTURE = """Gebruik altijd deze structuur:

## Samenvatting
Beknopte samenvatting van de week (2-3 zinnen met de meest opvallende punten).

## Kerncijfers & Trends
Analyseer de kerncijfers. Wat valt op? Zijn er positieve of negatieve trends?

## Topcontent
Welke pagina's presteren goed? Wat verklaart dit succes?

## Verkeersbronnen
Waar komt het verkeer vandaan? Kansen en risico's per kanaal.

## Gebruikersgedrag
Engagement, sessieduur, bounce rate — wat zegt dit over de bezoekerservaring?

## Aandachtspunten
Wat vraagt directe aandacht of nader onderzoek?

## Aanbevelingen voor komende week
3 tot 5 concrete, uitvoerbare acties.

Wees analytisch, concreet en gebruik de cijfers om je inzichten te onderbouwen."""

_FALLBACK_PERSONA = (
    "Je bent Hermes, een data-analist gespecialiseerd in Google Analytics 4. "
    "Analyseer de wekelijkse websitedata grondig en schrijf een helder rapport in het Nederlands."
)
_HERMES_SYSTEM = _FALLBACK_PERSONA + "\n\n" + _REPORT_STRUCTURE


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → waarde die de actieve backend snapt (alleen openrouter krijgt override)."""
    if not profile_model:
        return None
    m = profile_model.strip()
    if hermes_backend() == "openrouter":
        return m[len("openrouter/"):] if m.startswith("openrouter/") else m
    return None


def _analyst_config() -> tuple[str, Optional[str]]:
    """Bouw (system_prompt, model_override) voor de analyse.

    Gebruikt het 'Analytics Analist'-expertprofiel (brein + model) gecombineerd met
    de vaste rapportstructuur; valt terug op de ingebouwde persona als het profiel
    (nog) niet bestaat.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT system_prompt, model FROM agent_profiles WHERE name = ?",
                (ANALYST_PROFILE_NAME,),
            ).fetchone()
    except Exception:  # noqa: BLE001 — DB-hapering mag het rapport niet slopen
        row = None
    if row and (row["system_prompt"] or "").strip():
        system = row["system_prompt"].strip() + "\n\n" + _REPORT_STRUCTURE
        return system, _resolve_model_override(row["model"])
    return _HERMES_SYSTEM, None


def _format_ga_data(data: dict) -> str:
    p = data["period"]
    s = data["summary"]
    lines = [
        f"# Google Analytics Data: {p['start']} t/m {p['end']}",
        "",
        "## Kerncijfers",
        f"- Sessies: {s.get('sessions', 'n/b'):,}",
        f"- Unieke gebruikers: {s.get('users', 'n/b'):,}",
        f"- Paginaweergaven: {s.get('pageviews', 'n/b'):,}",
        f"- Engagementrate: {s.get('engagement_rate', 'n/b')}%",
        f"- Gemiddelde sessieduur: {s.get('avg_session_duration', 0)} seconden",
        f"- Bounce rate: {s.get('bounce_rate', 'n/b')}%",
        "",
    ]

    if data.get("daily"):
        lines += [
            "## Dagelijks overzicht",
            "| Datum | Sessies | Gebruikers | Paginaweergaven |",
            "|-------|---------|------------|-----------------|",
        ]
        for d in data["daily"]:
            lines.append(f"| {d['date']} | {d['sessions']:,} | {d['users']:,} | {d['pageviews']:,} |")
        lines.append("")

    if data.get("top_pages"):
        lines += [
            "## Top 10 Pagina's",
            "| Pagina | Weergaven | Gebruikers | Gem. duur (s) |",
            "|--------|-----------|------------|----------------|",
        ]
        for p in data["top_pages"]:
            path = p["path"][:55] + "…" if len(p["path"]) > 55 else p["path"]
            lines.append(f"| {path} | {p['pageviews']:,} | {p['users']:,} | {p['avg_duration']} |")
        lines.append("")

    if data.get("channels"):
        total = sum(c["sessions"] for c in data["channels"]) or 1
        lines += [
            "## Verkeersbronnen",
            "| Kanaal | Sessies | % |",
            "|--------|---------|---|",
        ]
        for c in data["channels"]:
            pct = round(c["sessions"] / total * 100, 1)
            lines.append(f"| {c['channel']} | {c['sessions']:,} | {pct}% |")
        lines.append("")

    if data.get("devices"):
        lines += ["## Apparaattypen", "| Apparaat | Sessies |", "|----------|---------|"]
        for d in data["devices"]:
            lines.append(f"| {d['device']} | {d['sessions']:,} |")
        lines.append("")

    if data.get("countries"):
        lines += ["## Top Landen", "| Land | Sessies |", "|------|---------|"]
        for c in data["countries"]:
            lines.append(f"| {c['country']} | {c['sessions']:,} |")
        lines.append("")

    return "\n".join(lines)


async def _run_analysis(user_content: str, system: str, model_override: Optional[str]) -> str:
    """Draai de analyse via de agent-loop (tool-loos), met automatische 429-fallback."""
    chunks: list[str] = []
    async for ev in agent_service.run_agent(
        messages=[{"role": "user", "content": user_content}],
        system_prompt=system,
        agent="hermes",
        model_override=model_override,
        use_tools=False,
        max_tokens=4096,
    ):
        if ev.get("type") == "error":
            raise RuntimeError(ev.get("message") or "Onbekende agent-fout")
        if ev.get("type") == "text":
            chunks.append(ev["text"])
    return "".join(chunks).strip()


def _save_obsidian(analysis: str, period: dict, week_label: str) -> Optional[Path]:
    if not OBSIDIAN_VAULT_PATH:
        return None
    vault = Path(OBSIDIAN_VAULT_PATH)
    if not vault.exists():
        return None
    folder = vault / "Analytics"
    folder.mkdir(exist_ok=True)
    note = folder / f"Wekelijks rapport {week_label}.md"
    frontmatter = (
        f"---\n"
        f"date: {date.today().isoformat()}\n"
        f"week: {week_label}\n"
        f"periode: {period['start']} t/m {period['end']}\n"
        f"tags: [analytics, rapport, google-analytics]\n"
        f"gegenereerd_door: Hermes\n"
        f"---\n\n"
    )
    note.write_text(frontmatter + analysis, encoding="utf-8")
    return note


def _save_dashboard(data_md: str, analysis: str, week_label: str) -> str:
    session = memory_service.create_session(name=f"GA Rapport {week_label}", agent="hermes")
    sid = session["id"]
    memory_service.add_message(sid, "user", data_md)
    memory_service.add_message(sid, "assistant", analysis)
    return sid


async def run_weekly_report() -> dict:
    if not ga_configured():
        print("[Analytics] GA4 niet geconfigureerd — stel GA4_PROPERTY_ID in .env in")
        return {"success": False, "error": "GA4 niet geconfigureerd"}

    today = date.today()
    iso = today.isocalendar()
    week_label = f"{iso[0]}-W{iso[1]:02d}"
    print(f"[Analytics] Start weekrapport {week_label}…")

    # 1. GA data ophalen
    try:
        ga_data = fetch_weekly_data(days=7)
        print(f"[Analytics] GA data opgehaald: {ga_data['summary']}")
    except Exception as e:
        msg = f"GA data ophalen mislukt: {e}"
        print(f"[Analytics] {msg}")
        return {"success": False, "error": msg}

    data_md = _format_ga_data(ga_data)

    # 2. Analyse via het Analytics Analist-expertprofiel (of fallback-persona)
    try:
        system, model_override = _analyst_config()
        analysis = await _run_analysis(
            f"Analyseer deze Google Analytics data:\n\n{data_md}", system, model_override
        )
        print(
            f"[Analytics] Analyse gereed ({len(analysis)} tekens, "
            f"model={model_override or 'default'})"
        )
    except Exception as e:
        msg = f"Hermes analyse mislukt: {e}"
        print(f"[Analytics] {msg}")
        return {"success": False, "error": msg}

    results: dict = {"success": True, "week": week_label, "period": ga_data["period"]}

    # 3. Dashboard
    try:
        sid = _save_dashboard(data_md, analysis, week_label)
        results["session_id"] = sid
        print(f"[Analytics] Sessie aangemaakt: {sid}")
    except Exception as e:
        print(f"[Analytics] Dashboard opslaan mislukt: {e}")

    # 4. Obsidian
    try:
        note = _save_obsidian(analysis, ga_data["period"], week_label)
        if note:
            results["obsidian_note"] = str(note)
            print(f"[Analytics] Obsidian note: {note}")
    except Exception as e:
        print(f"[Analytics] Obsidian opslaan mislukt: {e}")

    # 5. E-mail
    if email_configured():
        try:
            subject = f"Hermes GA Rapport {week_label} — {ga_data['period']['start']} t/m {ga_data['period']['end']}"
            body = f"Wekelijks Google Analytics Rapport\n{'=' * 50}\n\n{analysis}"
            sent = send_report(subject, body)
            results["email_sent"] = sent
            print(f"[Analytics] E-mail {'verstuurd' if sent else 'mislukt'}")
        except Exception as e:
            print(f"[Analytics] E-mail versturen mislukt: {e}")
            results["email_sent"] = False
    else:
        results["email_sent"] = False
        print("[Analytics] SMTP niet geconfigureerd, e-mail overgeslagen")

    print(f"[Analytics] Rapport {week_label} voltooid")
    return results
