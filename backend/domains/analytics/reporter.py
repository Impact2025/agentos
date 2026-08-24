"""
Weekrapport orchestrator: GA4/GSC-data → Claude-analyse (Hermes-terugval) → Obsidian + e-mail + dashboard.
"""
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from ...shared.config import OBSIDIAN_VAULT_PATH, hermes_backend
from ...shared.database import get_conn
from ...shared import agent_runner as agent_service
from ...domains.chat import service as memory_service
from .ga_service import fetch_weekly_data, is_configured as ga_configured
from .gsc_service import collect_all as gsc_collect_all, format_markdown as gsc_format_markdown
from . import insights
from ...domains.seo import gsc as gsc_api
from ...shared.outcomes import log_outcome
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

## Zoekmachine-zichtbaarheid (Google Search Console)
Dit is de belangrijkste sectie voor SEO-sturing. Gebruik de meegeleverde
GSC-data en analyseer per project:
- **Portfolio-overzicht**: welk project wint/mijn verkeer in zoek? (klikken,
  impressies, CTR, positie, week-op-week verandering)
- **Top-zoekwoorden** per project: waar ranken we, met welke CTR en positie?
- **Quick wins**: zoekwoorden op positie 4–15 met veel impressies — welke
  pagina's kunnen met content/linkwerk naar pagina 1 worden geduwd? Geef per
  project 1–3 concrete kandidaten.
- **CTR-verbetering**: zoekwoorden met veel impressies maar lage CTR — welke
  titles/metas moeten herschreven worden?
- **Stijgers & dalers**: welke posities bewegen hard? Wat is de waarschijnlijke
  oorzaak (nieuwe content, seizoen, concurrentie)?
Wees specifiek: noem projectnamen, zoekwoorden en getallen. Geen algemeenheden.

## Aandachtspunten
Wat vraagt directe aandacht of nader onderzoek?

## Status van eerdere aanbevelingen
Als er een blok "Status van eerdere aanbevelingen" is meegeleverd: neem het over
en trek er een conclusie uit. Een quick win die drie weken op rij is aanbevolen
en niet beweegt, herhaal je niet als nieuw advies — benoem dat hij blijft liggen
en stel voor hem te laten vallen of anders aan te pakken. Is er geen blok
meegeleverd, sla deze sectie dan over.

## Aanbevelingen voor komende week
3 tot 5 concrete, uitvoerbare acties per prioriteit (SEO + verkeer). Herhaal
geen aanbeveling die volgens het opvolgingsblok al weken blijft liggen.

Wees analytisch, concreet en gebruik de cijfers om je inzichten te onderbouwen."""

_FALLBACK_PERSONA = (
    "Je bent Hermes, een data-analist gespecialiseerd in Google Analytics 4. "
    "Analyseer de wekelijkse websitedata grondig en schrijf een helder rapport in het Nederlands."
)
_HERMES_SYSTEM = _FALLBACK_PERSONA + "\n\n" + _REPORT_STRUCTURE


def _resolve_model_override(profile_model: Optional[str]) -> Optional[str]:
    """Profielmodel → bare model-string die de cloud-gateway snapt.

    Geeft de model-string terug (eventuele 'openrouter/'-prefix gestript)
    zodra een cloud-sleutel aanwezig is — ongeacht welke backend de app
    standaard gebruikt. Zo wordt een 'pro'-profiel (bv. claude-sonnet-4-6 via
    OpenModel) echt gehonoreerd en niet stilzwijgend overschreven door het
    goedkope default-model. Bij geen profielmodel of geen cloud-sleutel → None."""
    if not profile_model:
        return None
    m = profile_model.strip()
    if m.startswith("openrouter/"):
        from ...shared.config import OPENROUTER_API_KEY
        return m[len("openrouter/"):] if OPENROUTER_API_KEY else None
    from ...shared.config import OPENMODEL_API_KEY
    return m if OPENMODEL_API_KEY else None


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
        max_tokens=6000,
        purpose="analytics",
    ):
        if ev.get("type") == "error":
            raise RuntimeError(ev.get("message") or "Onbekende agent-fout")
        if ev.get("type") == "text":
            chunks.append(ev["text"])
    return "".join(chunks).strip()


def _pct(v: Optional[float]) -> str:
    return f"{v:+.1f}%" if v is not None else "n/b"


def _opvolging_block() -> str:
    """Wat er van de vorige rapporten níét is opgepakt.

    Zonder dit heeft het rapport geen geheugen: het herhaalt elke maandag
    dezelfde aanbeveling zonder te merken dat die al drie weken blijft liggen.
    `stale_quick_wins` en `structural_decliners` bestonden al maar werden alleen
    door Iris en de UI gelezen, niet door het rapport zelf.
    """
    try:
        blijvers = insights.stale_quick_wins()
        dalers = insights.structural_decliners()
    except Exception as e:  # noqa: BLE001 — geheugen mag het rapport niet slopen
        print(f"[Analytics] opvolging ophalen mislukt: {e}")
        return ""

    if not blijvers and not dalers:
        return ""

    L = ["## Status van eerdere aanbevelingen", ""]
    if blijvers:
        L.append("**Blijft liggen** — deze quick wins stonden al meerdere weken in "
                 "het rapport en bewegen niet. Herhaal het advies niet; vraag je af "
                 "waarom het niet wordt opgepakt of laat het los:")
        for b in blijvers[:5]:
            pos = f"positie {b['positie']}" if b.get("positie") else "positie onbekend"
            L.append(f"- **{b['project']}**: `{b['query']}` — {b['weken']} weken op rij, {pos}")
        L.append("")
    if dalers:
        L.append("**Structureel dalend** — deze projecten verliezen over 28 dagen "
                 "zowel volume als positie. Dat is geen weekruis:")
        for d in dalers[:5]:
            # position_delta is een positieverschil, geen percentage.
            pd = d.get("position_delta")
            pd_txt = "n/b" if pd is None else f"{pd:+.1f}"
            L.append(f"- **{d['project']}**: {d['clicks']} klikken ({_pct(d.get('clicks_pct'))}), "
                     f"positie {d['position']} ({pd_txt})")
        L.append("")
    return "\n".join(L)


def _build_fallback_analysis(
    ga_data: dict,
    gsc_analyses: List[Dict],
    week_label: str,
    fallback_reason: str = "",
) -> str:
    """Deterministische, datagedreven analyse op basis van de echte GSC/GA-cijfers.

    Wordt gebruikt wanneer de LLM een lege analyse teruggeeft (zwak model,
    time-out, 429). Levert altijd een volwaardige, leesbare analyse — geen
    algemeenheden, wél de concrete projectnamen, zoekwoorden en getallen.
    """
    s = ga_data.get("summary", {})
    p = ga_data.get("period", {})
    L: list[str] = []

    L += [
        f"# Analyse week {week_label} ({p.get('start')} t/m {p.get('end')})",
        "",
        "## Samenvatting",
    ]
    if s:
        L.append(
            f"GA4 toont {s.get('sessions', 0):,} sessies en {s.get('users', 0):,} "
            f"unieke gebruikers over de afgelopen 7 dagen, met "
            f"{s.get('pageviews', 0):,} paginaweergaven. Engagementrate "
            f"{s.get('engagement_rate', 'n/b')}% en een bounce rate van "
            f"{s.get('bounce_rate', 'n/b')}%. Verkeer via GA4 blijft laag; de echte "
            f"zichtbaarheid zit in Google Search Console (zie hieronder), waar "
            f"{len(gsc_analyses)} projecten samen "
            f"{sum(a['aggregate']['impressions'] for a in gsc_analyses):,} zoekimpressies "
            f"genereerden."
        )
    else:
        L.append(
            f"GA4-data ontbreekt voor deze week. De Search Console-analyse hieronder "
            f"dekt {len(gsc_analyses)} projecten."
        )

    # GA4 kerncijfers
    L += ["", "## Kerncijfers & Trends (GA4)", ""]
    if s:
        L.append(
            f"- Sessies: **{s.get('sessions', 0):,}** · Unieke gebruikers: "
            f"**{s.get('users', 0):,}** · Paginaweergaven: **{s.get('pageviews', 0):,}**"
        )
        L.append(
            f"- Engagementrate: {s.get('engagement_rate', 'n/b')}% · Gem. sessieduur: "
            f"{s.get('avg_session_duration', 0)}s · Bounce rate: {s.get('bounce_rate', 'n/b')}%"
        )
    if ga_data.get("channels"):
        total = sum(c["sessions"] for c in ga_data["channels"]) or 1
        top = sorted(ga_data["channels"], key=lambda c: c["sessions"], reverse=True)[:5]
        L.append("")
        L.append("**Verkeersbronnen:** " + ", ".join(
            f"{c['channel']} ({round(c['sessions']/total*100)}%)" for c in top))
    if ga_data.get("top_pages"):
        tp = ga_data["top_pages"][0]
        L.append("")
        L.append(f"**Top pagina:** `{tp['path']}` ({tp['pageviews']:,} weergaven)")
    if not (s or ga_data.get("channels")):
        L.append("_Geen GA4-kerncijfers beschikbaar._")

    # GSC per project
    L += ["", "## Zoekmachine-zichtbaarheid (Google Search Console)", ""]
    if not gsc_analyses:
        L.append("_Geen Search Console-data beschikbaar._")
    else:
        # Portfolio-ranking op impressies
        ranked = sorted(gsc_analyses, key=lambda a: a["aggregate"]["impressions"], reverse=True)
        L.append("**Portfolio-overzicht (gesorteerd op zoekimpressies):**")
        L.append("| Project | Klikken | Impressies | CTR | Gem. positie | Klik w-o-w | Impressie w-o-w |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        for a in ranked:
            c = a["comparison"]
            L.append(
                f"| {a['name']} | {a['aggregate']['clicks']:,} | {a['aggregate']['impressions']:,} "
                f"| {a['aggregate']['ctr']}% | {a['aggregate']['position']} "
                f"| {_pct(c['clicks'].get('pct'))} | {_pct(c['impressions'].get('pct'))} |"
            )
        L.append("")

        for a in ranked:
            c = a["comparison"]
            L.append(f"### {a['name']}")
            L.append(
                f"- Zoekprestaties: **{a['aggregate']['clicks']:,}** klikken, "
                f"**{a['aggregate']['impressions']:,}** impressies, CTR "
                f"**{a['aggregate']['ctr']}%**, gem. positie **{a['aggregate']['position']}** "
                f"(positie {c['position']['delta']:+.1f} t.o.v. vorige periode)."
            )
            if a["top_queries"]:
                L.append("- **Top zoekwoorden:** " + ", ".join(
                    f"`{r['query']}` (pos {r['position']}, {r['impressions']:,} impr)"
                    for r in a["top_queries"][:6]))
            if a["quick_wins"]:
                L.append("- **Quick wins (push naar pagina 1):** " + "; ".join(
                    f"`{r['query']}` (pos {r['position']}, {r['impressions']:,} impr)"
                    for r in a["quick_wins"][:3]))
            if a["ctr_fix"]:
                L.append("- **CTR-verbeterpunten:** " + "; ".join(
                    f"`{r['query']}` ({r['impressions']:,} impr, CTR {r['ctr']}%)"
                    for r in a["ctr_fix"][:3]))
            movers = []
            if a["risers"]:
                movers.append("stijgers: " + ", ".join(
                    f"`{m['query']}` (+{m['pos_delta']})" for m in a["risers"][:3]))
            if a["fallers"]:
                movers.append("dalers: " + ", ".join(
                    f"`{m['query']}` ({m['pos_delta']})" for m in a["fallers"][:3]))
            if movers:
                L.append("- **Beweging:** " + " · ".join(movers))
            L.append("")

    # Opvolging: wat bleef er van eerdere rapporten liggen?
    opvolging = _opvolging_block()
    if opvolging:
        L.append(opvolging)

    # Aanbevelingen
    L += ["## Aanbevelingen voor komende week", ""]
    recs: list[str] = []
    # Grootste quick-win-projecten eerst
    qw_projects = sorted(
        [a for a in gsc_analyses if a["quick_wins"]],
        key=lambda a: a["aggregate"]["impressions"], reverse=True)[:3]
    for a in qw_projects:
        kw = a["quick_wins"][0]
        recs.append(
            f"**{a['name']}**: optimaliseer de pagina voor `{kw['query']}` "
            f"(nu pos {kw['position']}, {kw['impressions']:,} impr) — push naar "
            f"pagina 1 met content- en interne-linkwerk; dit levert de snelste "
            f"klikwinst.")
    ctr_projects = sorted(
        [a for a in gsc_analyses if a["ctr_fix"]],
        key=lambda a: a["aggregate"]["impressions"], reverse=True)[:2]
    for a in ctr_projects:
        cf = a["ctr_fix"][0]
        recs.append(
            f"**{a['name']}**: schrijf title/meta van `{cf['query']}` "
            f"({cf['impressions']:,} impr, CTR {cf['ctr']}%) opnieuw voor een "
            f"hogere klikratio.")
    if not recs:
        recs.append("Geen acute SEO-kansen gedetecteerd — focus op contentproductie "
                    "om het lage zoekvolume structureel te vergroten.")
    recs.append("Blijf wekelijks publiceren via de content-pipeline; impression-groei "
                "is de belangrijkste hefboom bij dit volume.")
    for i, r in enumerate(recs[:5], 1):
        L.append(f"{i}. {r}")

    L.append("")
    L.append("---")
    # De reden meegeven, niet alleen het feit. Zonder reden is een weggevallen
    # synthese niet te diagnosticeren — zie het rapport van 2026-W32.
    reden = f" Reden: {fallback_reason}." if fallback_reason else ""
    L.append("_Automatisch gegenereerde analyse op basis van live GA4- en Google "
             f"Search Console-data. De LLM-synthese was niet beschikbaar.{reden} "
             "Deze datagedreven fallback garandeert een volledig rapport._")
    return "\n".join(L)


# Het sterke model voor de synthese. Losse constante zodat de modelkeuze op één
# plek staat en in de faalreden genoemd kan worden.
# Signalen dat de GSC-sectie daadwerkelijk geschreven is. De oude check zocht
# letterlijk "Search Console"; een model dat zijn kop "## Zoekmachine-zichtbaarheid"
# noemt schreef dan een prima analyse die alsnog werd weggegooid.
_GSC_SIGNALEN = ("search console", "zoekmachine-zichtbaarheid", "gsc",
                 "impressies", "zoekwoord")


def _analyse_is_volledig(analyse: Optional[str]) -> bool:
    """Is dit een bruikbare analyse, of moeten we terugvallen?

    Twee eisen: genoeg tekst, en aantoonbaar iets over zoekprestaties gezegd.
    Bewust op signaalwoorden in plaats van één exacte kop — de vorige versie
    verwierp geldige analyses op een koptekstverschil.
    """
    if not analyse:
        return False
    tekst = analyse.strip()
    if len(tekst) < 2000:
        return False
    laag = tekst.lower()
    return any(signaal in laag for signaal in _GSC_SIGNALEN)


async def _analyze(ga_data, gsc_analyses, gsc_block, data_md, week_label) -> tuple[str, str]:
    """Probeer de LLM-analyse via Claude; val terug op de profiel-LLM en dan
    de deterministische schrijver wanneer de output te dun of incompleet is.

    Strategie (denkwerk hoort op het Claude-pad — zelfde afspraak als Iris en
    goal-synthese, zie CLAUDE.md 'LLM-keten'; dit rapport is precies zo'n
    synthese-taak, geen bulkwerk):
      1. Claude (chat/claude.py, purpose="analytics") — het model waarop al
         het andere denkwerk in dit systeem draait.
      2. Anders de profiel-LLM (deepseek-v4-flash e.a., Hermes-pad).
      3. Altijd: als de LLM-output te kort is (<2000 tekens) of de verplichte
         GSC-sectie mist, gebruik de datagedreven fallback. Die is altijd
         volledig en noemt concrete projecten, zoekwoorden en cijfers.

    Returns (analysis, source) waarbij source 'claude', 'hermes' of 'fallback' is.
    """
    system, model_override = _analyst_config()
    opvolging = _opvolging_block()
    opvolging_block = f"\n\n{opvolging}" if opvolging else ""
    prompt = (
        f"Analyseer deze Google Analytics én Google Search Console data:\n\n"
        f"{data_md}{gsc_block}{opvolging_block}"
    )

    # Waarom een poging faalde, zodat het rapport het kan vermelden.
    redenen: list[str] = []

    # 1. Claude — het denkwerk-pad
    from ..chat import claude as claude_service
    if claude_service.is_configured():
        try:
            claude_out = (await claude_service.get_response(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=6000,
                purpose="analytics",
            )).strip()
            if _analyse_is_volledig(claude_out):
                return claude_out, "claude"
            redenen.append(f"Claude gaf een te dunne of incomplete analyse "
                           f"({len(claude_out)} tekens)")
            print("[Analytics] Claude-analyse te dun/incompleet — probeer profiel-LLM")
        except Exception as e:
            redenen.append(f"Claude mislukte ({e})")
            print(f"[Analytics] Claude-analyse mislukt ({e}) — profiel-LLM")
    else:
        redenen.append("Claude niet geconfigureerd (ANTHROPIC_API_KEY/OPENMODEL_API_KEY)")

    # 2. Profiel-LLM (deepseek e.a.)
    try:
        analysis = await _run_analysis(prompt, system, model_override)
        if _analyse_is_volledig(analysis):
            return analysis.strip(), "hermes"
        redenen.append(f"profiel-LLM ({model_override or 'standaardmodel'}) gaf een te "
                       f"dunne of incomplete analyse ({len(analysis or '')} tekens)")
        print("[Analytics] Profiel-LLM analyse te dun/incompleet — fallback analyse")
    except Exception as e:
        redenen.append(f"profiel-LLM ({model_override or 'standaardmodel'}) mislukte ({e})")
        print(f"[Analytics] LLM analyse mislukt ({e}) — fallback analyse")

    # 3. Deterministische fallback (altijd volledig)
    return _build_fallback_analysis(
        ga_data, gsc_analyses, week_label, fallback_reason="; ".join(redenen),
    ), "fallback"


_BRON_LABEL = {"claude": "Claude", "hermes": "Hermes"}


def _save_obsidian(analysis: str, period: dict, week_label: str, source: str = "claude") -> Optional[Path]:
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
        f"gegenereerd_door: {_BRON_LABEL.get(source, 'datagedreven fallback')}\n"
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
        # Een weekrapport dat niet verschijnt hoort een kaart te zijn, geen
        # print: de maandagrun is de enige plek waar het 28-daagse beeld ontstaat.
        log_outcome(
            "Impact OS", "weekrapport", msg,
            next_step="Controleer de GA4-koppeling (service-account + GA4_PROPERTY_ID); "
                      "zonder deze run is er deze week geen portfolio-beeld.",
            status="error",
        )
        return {"success": False, "error": msg}

    data_md = _format_ga_data(ga_data)

    # 1b. GSC-data per project (Search Console) — faalveilig
    gsc_block = ""
    gsc_analyses = []
    if gsc_api.is_configured():
        try:
            gsc_analyses = gsc_collect_all()
            if gsc_analyses:
                gsc_block = "\n\n" + gsc_format_markdown(gsc_analyses)
                # Vastleggen vóórdat de LLM eraan komt: de bevindingen zijn een
                # meting, de analyse is een mening. Hierdoor kunnen Iris en de
                # UI het weekbeeld lezen ook als de gateway plat ligt — en pas
                # hierdoor is zichtbaar dat een quick win weken blijft liggen.
                try:
                    bewaard = insights.store_week(gsc_analyses, week_label)
                    print(f"[Analytics] {bewaard} project(en) vastgelegd in weekly_insights")
                except Exception as e:  # noqa: BLE001
                    print(f"[Analytics] weekbevindingen opslaan mislukt: {e}")
                    log_outcome(
                        "Impact OS", "weekrapport", f"Weekbevindingen niet opgeslagen: {e}",
                        next_step="Controleer de tabel weekly_insights; zonder deze rijen "
                                  "stuurt het weekrapport niets aan en leert Iris er niets van.",
                        status="error",
                    )
            else:
                # GSC ís geconfigureerd en gaf voor geen enkele site data. Dat is
                # geen 'rustige week' maar een kapotte koppeling, en het rapport
                # ziet er zonder deze melding volkomen normaal uit.
                print("[Analytics] GSC geconfigureerd maar geen enkele site gaf zoekdata")
        except Exception as e:
            print(f"[Analytics] GSC-data ophalen mislukt (ga door zonder): {e}")
    else:
        print("[Analytics] GSC niet geconfigureerd — alleen GA4 in rapport")

    # 2. Analyse via het Analytics Analist-expertprofiel (of fallback-persona).
    #    Bij een lege/mislukte LLM-reactie schakelen we over op een deterministische,
    #    datagedreven analyse zodat er altijd een volwaardig rapport uitkomt.
    analysis, source = await _analyze(ga_data, gsc_analyses, gsc_block, data_md, week_label)
    print(f"[Analytics] Analyse gereed ({len(analysis)} tekens, bron={source})")
    if source == "fallback":
        # Een rapport zonder synthese ziet er compleet uit en is dat niet. Zonder
        # kaart merkt niemand dat de duiding weken achtereen ontbreekt.
        log_outcome(
            "Impact OS", "weekrapport",
            f"Weekrapport {week_label} zonder LLM-synthese: alleen de datagedreven fallback.",
            next_step="Controleer de modelgateway (ANTHROPIC_API_KEY/OPENMODEL_API_KEY, quota); "
                      "de reden staat onderaan het rapport.",
            status="error",
        )

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
        note = _save_obsidian(analysis, ga_data["period"], week_label, source=source)
        if note:
            results["obsidian_note"] = str(note)
            print(f"[Analytics] Obsidian note: {note}")
    except Exception as e:
        print(f"[Analytics] Obsidian opslaan mislukt: {e}")

    # 5. E-mail
    if email_configured():
        try:
            subject = f"Hermes GA+GSC Rapport {week_label} — {ga_data['period']['start']} t/m {ga_data['period']['end']}"
            gsc_note = f"\n\n(Bijgevoegd: {len(gsc_analyses)} project(en) met Google Search Console-zoekdata)" if gsc_analyses else ""
            body = f"Wekelijks Google Analytics + Search Console Rapport\n{'=' * 50}\n\n{analysis}{gsc_note}"
            sent = send_report(subject, body)
            results["email_sent"] = sent
            print(f"[Analytics] E-mail {'verstuurd' if sent else 'mislukt'}")
        except Exception as e:
            print(f"[Analytics] E-mail versturen mislukt: {e}")
            results["email_sent"] = False
    else:
        results["email_sent"] = False
        print("[Analytics] SMTP niet geconfigureerd, e-mail overgeslagen")

    # Uitkomstkaart: het weekrapport is een agent-run en hoort een artefact te
    # hebben (de Obsidian-notitie). Zonder kaart is een rapport dat niemand mailt
    # en dat niemand leest niet te onderscheiden van een rapport dat er nooit was.
    try:
        artefact = results.get("obsidian_note") or ""
        log_outcome(
            "Impact OS", "weekrapport",
            f"Weekrapport {week_label}: {len(gsc_analyses)} project(en) met zoekdata, "
            f"analyse via {source}",
            artifact=artefact,
            next_step=("Lees de quick wins en CTR-gaten in het rapport; Iris weegt ze "
                       "mee in de briefing van morgen."),
        )
    except Exception as e:  # noqa: BLE001 — een kaart mag het rapport niet vellen
        print(f"[Analytics] uitkomstkaart schrijven mislukt: {e}")

    print(f"[Analytics] Rapport {week_label} voltooid")
    return results
