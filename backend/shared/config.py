import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── Harde "nooit Claude"-garantie (regel Vincent 2026-08-10) ──────────────────
# OpenModel/OpenRouter mogen NOOIT een Claude-model aanroepen — alles loopt op
# DeepSeek. Deze sanitizer grijpt elke modelstring AF op load-tijd: herkent hij
# een Claude-naam (claude-*, of 'anthropic/'-prefix), dan wordt die stil
# omgezet naar DeepSeek. Zo kan geen enkele .env-var, default of nieuwe caller
# per ongeluk Claude serveren + factureren. Verander DEEPSEEK_FALLBACK_MODEL om
# de vervanger bij te stellen.
DEEPSEEK_FALLBACK_MODEL: str = os.getenv("DEEPSEEK_FALLBACK_MODEL", "deepseek-v4-pro")


def _no_claude(model: str) -> str:
    """Rewrite elke Claude-modelnaam naar DeepSeek. Returneert anders ongewijzigd."""
    if not model:
        return model
    m = model.lower()
    if m.startswith("claude") or m.startswith("anthropic/") or "claude-" in m:
        return DEEPSEEK_FALLBACK_MODEL
    return model


CLAUDE_MODEL: str = _no_claude(os.getenv("CLAUDE_MODEL", "deepseek-v4-pro"))
CLAUDE_MODEL_2: str = _no_claude(os.getenv("CLAUDE_MODEL_2", "deepseek-v4-flash"))

# Hermes agent — priority: lokaal > OpenModel (deepseek-v4-flash, goedkoop, primair)
#   > Ollama (lokaal llama3.1, GRATIS backup, geen quota) > OpenRouter > Anthropic.
# Bij een OpenModel 403 quota-exceeded schakelt hermes_backend tijdelijk over naar
# Ollama (zie llm_quota_backoff_active) zodat de agents gratis doorlopen.
HERMES_LOCAL_URL: str = os.getenv("HERMES_LOCAL_URL", "")
HERMES_LOCAL_KEY: str = os.getenv("HERMES_LOCAL_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
AGNES_MODEL: str = os.getenv("AGNES_MODEL", "agnes-2.0-flash")

# OpenModel.ai — Anthropic-compatible gateway (DeepSeek V4 Flash e.a.)
OPENMODEL_API_KEY: str = os.getenv("OPENMODEL_API_KEY", "")
OPENMODEL_BASE_URL: str = os.getenv("OPENMODEL_BASE_URL", "https://api.openmodel.ai")
OPENMODEL_MODEL: str = _no_claude(os.getenv("OPENMODEL_MODEL", "deepseek-v4-flash"))
# Sterk model op dezelfde gateway voor denk-werk (Iris-analyse, kwaliteitsgate,
# goal-synthese, drafts): het Claude-pad in de app loopt hierover zodra er geen
# directe Anthropic-key is. Bulk-/toolwerk loopt op OPENMODEL_MODEL (flash).
# Default is bewust deepseek-v4-flash: claude-sonnet-4-6 verbrandde ~10x zoveel
# krediet (incident 2026-07-11, $4,90 op één dag). Wil je alsnog het dure Claude-
# pad, zet dan expliciet OPENMODEL_SMART_MODEL=claude-sonnet-4-6 in .env.
OPENMODEL_SMART_MODEL: str = _no_claude(os.getenv("OPENMODEL_SMART_MODEL", "deepseek-v4-pro"))
HERMES_MODEL: str = os.getenv("HERMES_MODEL", "meta-llama/llama-3.1-8b-instruct")

# ── Live model-swap (zonder herstart) ───────────────────────────────────────
# De 2-click model-swapper in de UI schrijft overrides naar de instance_settings
# store (zie shared/settings_store.py) i.p.v. .env te eisen + herstart. Deze
# resolvers lezen die override vóór de env-var, zodat een swap direct effect
# heeft op de agent-runner, chat, goals en delegate. Zie api/config models.
# Let op: de _no_claude-garantie blijft staan — een override op een Claude-naam
# wordt alsnog naar DeepSeek omgezet, zodat een swap geen Claude factureert.
_MODEL_OVERRIDE_KEYS = {
    "openmodel_model": "OPENMODEL_MODEL",
    "openmodel_smart_model": "OPENMODEL_SMART_MODEL",
}


def resolve_openmodel_model() -> str:
    """Actief bulk-/tool-model (flash) — override vóór env."""
    try:
        from .settings_store import get_setting
        ov = get_setting("openmodel_model")
        if ov:
            return _no_claude(ov)
    except Exception:
        pass
    return OPENMODEL_MODEL


def resolve_openmodel_smart_model() -> str:
    """Actief denk-/synthese-model (smart) — override vóór env."""
    try:
        from .settings_store import get_setting
        ov = get_setting("openmodel_smart_model")
        if ov:
            return _no_claude(ov)
    except Exception:
        pass
    return OPENMODEL_SMART_MODEL

# Pexels (gratis stock-foto's/video voor video-footage). Geen creditcard.
# Haal een key op via https://www.pexels.com/api/ (gratis account, email + naam)
# en zet hem hier. Zonder key valt de video-render terug op lokale foto's / merk-slides.
PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")

# ElevenLabs (betaalde, veel natuurlijkere voiceover dan de gratis edge-tts-
# stemmen — die klinken herkenbaar synthetisch, ook na tempo/toonhoogte-tuning).
# Key via https://elevenlabs.io (account + betaalmethode nodig). Zonder key valt
# de video-render terug op edge-tts — nooit een kapotte render.
ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
# Rachel: ElevenLabs' meest gedocumenteerde, stabiele voice-id (jaren ongewijzigd).
# Werkt via het meertalige model ook voor Nederlands, maar is niet NL-geoptimaliseerd
# — kies zelf een stem uit je ElevenLabs-bibliotheek en zet die hier of per project
# in video/template.json (`elevenlabs_voice_id`) zodra je wilt vergelijken.
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL: str = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
# Fallback-modellen (OpenRouter) waar de agent naartoe schakelt bij een 429
# (rate-limit) op het primaire HERMES_MODEL. Komma-gescheiden, in volgorde van
# voorkeur. Alleen relevant voor de openrouter-backend.
HERMES_FALLBACK_MODELS: list[str] = [
    m.strip()
    for m in os.getenv(
        "HERMES_FALLBACK_MODELS",
        "meta-llama/llama-3.3-70b-instruct:free,"
        "qwen/qwen3-next-80b-a3b-instruct:free,"
        "nvidia/nemotron-3-super-120b-a12b:free",
    ).split(",")
    if m.strip()
]
# Lichter/goedkoper model voor routinetaken (data-schoonmaak, JSON-formatteren, URL-checks).
# Routeer hier naartoe via model_override; laat opschalen naar HERMES_MODEL voor synthese/schrijfwerk.
HERMES_LIGHT_MODEL: str = os.getenv("HERMES_LIGHT_MODEL", "meta-llama/llama-3.1-8b-instruct")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "hermes3")

TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
# Fallback-zoekprovider (Brave Search API) — springt bij als Tavily faalt of
# zijn quota op is, zodat radar/leads/websearch niet stilvallen op één provider.
BRAVE_SEARCH_API_KEY: str = os.getenv("BRAVE_SEARCH_API_KEY", "")
# Tweede keyed backbone (eigen index, onafhankelijk van Bing/DDG) — GRATIS
# 100 queries/dag via Google Programmable Search Engine. Zodra beide waarden
# in .env staan schuift `google_pse` automatisch in de websearch-keten als
# onafhankelijke, gecontracteerde pijler naast Tavily/Brave.
GOOGLE_PSE_API_KEY: str = os.getenv("GOOGLE_PSE_API_KEY", "")
GOOGLE_PSE_CX: str = os.getenv("GOOGLE_PSE_CX", "")
HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")

# Netlify publisher — globale fallback-token (Personal Access Token). Per site kun
# je een eigen token + site-ID zetten in de sites-tabel (publish_api_key / publish_api_url).
NETLIFY_TOKEN: str = os.getenv("NETLIFY_TOKEN", "")

OBSIDIAN_VAULT_PATH: str = os.getenv("OBSIDIAN_VAULT_PATH", "")

# ── NotebookLM MCP (onderzoek-agent) ───────────────────────────────
# Spreekt de lokaal gedraaide `notebooklm-mcp`-server aan in HTTP-modus
# (start hem vanuit agentos_service.cmd op NOTEBOOKLM_PORT). De server
# drijft een echte Chrome en laat ons RAG-onderzoek doen tegen onze
# eigen notebooks, bronnen injecteren en Audio Overviews genereren.
# Zie backend/tools/notebooklm.py en backend/domains/researcher/.
NOTEBOOKLM_BASE_URL: str = os.getenv(
    "NOTEBOOKLM_BASE_URL", "http://127.0.0.1:3137"
)
NOTEBOOKLM_TIMEOUT: int = int(os.getenv("NOTEBOOKLM_TIMEOUT", "120"))
# Of de onderzoek-agent aanstaat (scheduler + API). Zet op "0" om hem
# te pauzeren zonder code te raken.
NOTEBOOKLM_ENABLED: bool = os.getenv("NOTEBOOKLM_ENABLED", "1") == "1"
# Default-notebook per project (id uit library.json van notebooklm-mcp).
# Wordt gebruikt als een research-vraag geen explicit notebook noemt.
# LET OP (2026-07-13): de SEO-notebooks in library.json (weareimpact-seo-research,
# bewaardvoorjou-seo-content) geven "Request access" — dit account heeft er geen
# toegang toe. Tijdelijk op de podcast gezet (bewezen werkend) tot Vincent de
# juiste, gedeelde SEO-notebook-URL's levert. Zie NOTES in researcher/service.py.
NOTEBOOKLM_DEFAULT_NOTEBOOK: str = os.getenv(
    "NOTEBOOKLM_DEFAULT_NOTEBOOK", "weareimpact-podcast"
)

# Google Analytics 4
GA4_PROPERTY_ID: str = os.getenv("GA4_PROPERTY_ID", "")
GA_SERVICE_ACCOUNT_PATH: str = os.getenv("GA_SERVICE_ACCOUNT_PATH", "")

# Google Search Console (Demand Engine) — hergebruikt standaard hetzelfde
# serviceaccount als GA4 (expliciet pad, anders GA-pad, anders de standaard
# GOOGLE_APPLICATION_CREDENTIALS). Zorg dat dit account als gebruiker is
# toegevoegd aan de Search Console-property, anders geeft de API 403.
GSC_SERVICE_ACCOUNT_PATH: str = (
    os.getenv("GSC_SERVICE_ACCOUNT_PATH", "")
    or GA_SERVICE_ACCOUNT_PATH
    or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    or ""
).replace("\x0b", "\\v")  # Windows \v_mun → behoud backslash

# E-mail (SMTP) voor weekrapporten
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
REPORT_EMAIL_TO: str = os.getenv("REPORT_EMAIL_TO", "v.munster@weareimpact.nl")

# Resend (transactionele mail: herinneringen, dagafsluitingen, klant-app)
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "")

# Google Indexing API (urlNotifications.publish) voor directe indexering na
# publicatie. Default uit: officieel alleen voor JobPosting/Livestream-content
# en het service-account moet Owner zijn in Search Console. De nette route
# (sitemap-submit bij GSC) staat altijd aan.
GOOGLE_INDEXING_ENABLED: bool = os.getenv("GOOGLE_INDEXING_ENABLED", "0") == "1"

# Content Multiplier: na 'Goedkeuren & publiceren' automatisch de format-waaier
# genereren uit het artikel (social-pack voor alle platforms + 9:16-video).
# Alles landt achter de bestaande review-gates (pending_review) — er wordt
# nooit automatisch gepost. Default aan; uitzetten bij LLM-budgetzorgen.
CONTENT_MULTIPLIER_ENABLED: bool = os.getenv("CONTENT_MULTIPLIER_ENABLED", "1") == "1"

# ── Bridge: cloud-companion (Vercel + Neon) ─────────────────────────────
# De bridge spiegelt de review-gates naar een klein cloud-app zodat Vincent
# onderweg (of met de pc uit) kan goedkeuren/afwijzen; besluiten worden bij de
# eerstvolgende sync hier lokaal uitgevoerd via dezelfde servicefuncties als de
# UI-knoppen. Pull-model: alleen uitgaand HTTPS, geen open poorten. Leeg laten
# = bridge uit (de sync-job slaat dan stil over). Zie remote/README.md.
BRIDGE_REMOTE_URL: str = os.getenv("BRIDGE_REMOTE_URL", "")
BRIDGE_TOKEN: str = os.getenv("BRIDGE_TOKEN", "")
BRIDGE_SYNC_MINUTES: int = int(os.getenv("BRIDGE_SYNC_MINUTES", "3"))

# Kwaliteitsgate voor content (0-100): onder deze score komt een artikel niet
# in de Wachtrij als publiceerbaar en weigert de publish-API. De pipeline
# probeert eerst automatisch te verbeteren (max 3 rondes).
# Wereldklasse-standaard: 80. Een artikel moet écht AEO-/rich-result-klaar zijn
# (direct-answer + FAQ + E-E-A-T + schone links) om gepubliceerd te worden.
# 80 (niet 85) zodat de grens matcht met wat het dashboard toont ("onder de 80")
# en de artikelen die de motor al structureel schrijft (82-84) wél doorgaan.
CONTENT_MIN_SCORE: int = int(os.getenv("CONTENT_MIN_SCORE", "80"))

# Hoeveel verbeterrondes de agent mág doen voordat hij opgeeft. Dit is een
# HARDE veiligheidslimiet (tegen eindeloze LLM-loops), geen streefwaarde: de
# review-loop stopt pas als de score ≥ CONTENT_MIN_SCORE OF dit aantal rondes
# op is. 8 (was 5) zodat de agent méér kans krijgt de grens écht te halen
# i.p.v. de score te maskeren — gecombineerd met rijker onderzoek (NotebookLM)
# haalt TeambuildingMetImpact de grens nu structureel.
CONTENT_MAX_ROUNDS: int = int(os.getenv("CONTENT_MAX_ROUNDS", "8"))

# Hoeveel onder-de-grens artikelen de autonome content-verbeteraar per run (elke
# 30 min) oppakt. Kostenbeheersing: elke job doet meerdere LLM-rondes. Oudste
# needs_work-jobs eerst; de rest volgt in latere runs.
CONTENT_IMPROVER_MAX_PER_RUN: int = int(os.getenv("CONTENT_IMPROVER_MAX_PER_RUN", "2"))

# TOTALE verbeter-pogingen per artikel, over álle runs heen (cross-run cap).
# Zonder deze grens blijft de content-verbeteraar elke 30 min hetzelfde
# vastgelopen artikel oppakken (score oscilleert 45–82, raakt de grens van 85
# nooit) en verbrandt hij de hele dag LLM-calls. Na CONTENT_IMPROVER_MAX_ATTEMPTS
# regenerate-pogingen wordt het artikel op status 'stuck' gezet en escaleert de
# agent naar de mens in plaats van eindeloos door te draaien. Dé rem tegen de
# "quota in één dag leeg"-incident van 2026-07-10.
CONTENT_IMPROVER_MAX_ATTEMPTS: int = int(os.getenv("CONTENT_IMPROVER_MAX_ATTEMPTS", "3"))

# Zelfde rem, maar voor de ZWARE Orchestrator/Gauntlet-loop (meerdere critici
# per poging — duurder dan één regenerate). Incident 14 aug 2026: een 'rejected'
# Bijeen-artikel en een WeAreImpact-artikel werden 20+ keer op één dag door de
# Gauntlet Loop herschreven — telkens een NIEUWE pending_review-job erbij, het
# bronrecord bleef gewoon 'rejected' staan en werd dus de volgende ronde weer
# gevonden. Dat verbrandde het grootste deel van de dagbudget (5M tokens) aan
# hetzelfde artikel. `process_one_under_threshold` telt nu pogingen per job en
# sluit 'm na deze grens af (net als CONTENT_IMPROVER_MAX_ATTEMPTS hierboven,
# maar lager: elke poging is al een 3-ronde criticus-loop, dus 2 zware pogingen
# ≈ 6 lichte).
ORCHESTRATOR_MAX_ATTEMPTS: int = int(os.getenv("ORCHESTRATOR_MAX_ATTEMPTS", "2"))

# ── LLM-kosten-zicht ───────────────────────────────────────────────────────
# Background-jobs (content-pipeline, improver, radar, SEO-engine) schreven hun
# token-verbruik nergens heen — je zag pas "quota op" toen het te laat was.
# Elke OpenModel/Claude/Hermes-aanroep wordt nu gelogd in de `llm_usage`-tabel.
# Vul de prijzen in (USD per 1M tokens) zodat de dagelijkse kostenschatting klopt;
# default 0 = tokens worden wél geteld, kostenraming blijft 0 totdat je prijzen
# invult. OpenModel-rekening komt op de OpenModel-factuur, niet op Anthropic.
OPENMODEL_INPUT_COST_PER_MTOK: float = float(os.getenv("OPENMODEL_INPUT_COST_PER_MTOK", "0"))
OPENMODEL_OUTPUT_COST_PER_MTOK: float = float(os.getenv("OPENMODEL_OUTPUT_COST_PER_MTOK", "0"))
ANTHROPIC_INPUT_COST_PER_MTOK: float = float(os.getenv("ANTHROPIC_INPUT_COST_PER_MTOK", "0"))
ANTHROPIC_OUTPUT_COST_PER_MTOK: float = float(os.getenv("ANTHROPIC_OUTPUT_COST_PER_MTOK", "0"))

# Waarschuw (log-WARN + activiteit) zodra het geschatte dagverbruik deze grens
# overschrijdt — vóórdat de echte quota hard tegen de limiet aanloopt.
# Default 3M (origineel). Op Vincents hoofd-instance is het echte verbruik
# ~6M/dag (autonome content + agent-runs op deepseek-v4-flash, goedkoop model).
# Een budget ver onder het werkelijke verbruik geeft elke dag een
# false-positive alarm en verliest zijn signaalwaarde. 8M = verbruik + ~30%
# marge, zodat het alarm pas afgaat bij een ÉCHTE uitschieter (bv. een loop die
# is vastgelopen en eindeloos retried). Tunable via DAILY_TOKEN_BUDGET in .env.
DAILY_TOKEN_BUDGET: int = int(os.getenv("DAILY_TOKEN_BUDGET", "8000000"))

# Agent-output-cap: maximale completion-tokens per agent-stap (run_agent /
# agent_runner). Agent-stappen (beslissingen, korte antwoorden) hebben zelden
# 4096 tok nodig; een lagere cap temt het verbruik zonder de kwaliteit van
# content-pijplijnen te raken — die stellen hun eigen (hogere) max_tokens in en
# overschrijven deze vlag. 1500 dekt vrijwel elke agent-stap; verhoog bij
# gemelde 'afgekapte' agent-output. Tunable via AGENT_MAX_TOKENS in .env.
AGENT_MAX_TOKENS: int = int(os.getenv("AGENT_MAX_TOKENS", "1500"))

# Zelf-uitlijnende quota-rem: na een harde 403 "quota exceeded" van de provider
# pauzeren autonome LLM-runs deze periode vanzelf (de provider is de bron van
# waarheid — geen gegokte tokenlimiet nodig). Zie outcomes.note_llm_quota_exhausted.
# 0 = uit. Incident 2026-07-10/11: quota om 20:39 leeg, Iris-briefing van 06:45
# liep er de volgende ochtend nog tegenaan.
LLM_QUOTA_BACKOFF_MINUTES: int = int(os.getenv("LLM_QUOTA_BACKOFF_MINUTES", "45"))

# ── Mission Radar autonomie ──────────────────────────────────────────────
# Auto-AEO: na elke sky-scan start de agent zelfstandig een AEO-aanval op de
# beste verse signalen, tot aan de Wachtrij-gate. De mens hoeft alleen nog
# "publiceer" te klikken. Zet op "0" om terug te vallen op handmatige AEO.
AEO_AUTO_ATTACK: bool = os.getenv("AEO_AUTO_ATTACK", "1") == "1"
# Minimale signaal-score voordat de agent een AEO-aanval durft te starten
# zonder mens. Hoger = conservatiever (minder vals-positieven in de Wachtrij).
AEO_AUTO_MIN_SCORE: float = float(os.getenv("AEO_AUTO_MIN_SCORE", "75"))
# Maximaal aantal auto-AEO-aanvallen per scan-run (kosten/overload-beheersing).
AEO_AUTO_MAX_PER_SCAN: int = int(os.getenv("AEO_AUTO_MAX_PER_SCAN", "3"))
# Minimale WERKENDE relevantiescore (ai_match_score) voordat de agent autonoom
# aanvalt. Een hoge signaal-score op louter versheid + keyword-boost is geen
# bewijs van topische fit; zonder een aantoonbaar relevant onderwerp mag de
# agent nooit zelfstandig de Wachtrij in. -1 (onbekend/gefaald) telt als NIET
# gehaald. Zet op 0 om deze rem uit te schakelen (oud gedrag).
AEO_AUTO_MIN_MATCH: float = float(os.getenv("AEO_AUTO_MIN_MATCH", "60"))
# NB: bewust GEEN "sterke match overrulet de score"-drempel. Zolang het
# match-model bijna alles 85-95 stempelt (ongecalibreerd), zou zo'n uitzondering
# off-topic ruis met een toevallig hoge match terug de Wachtrij in laten. Pas
# invoeren als ai_match_score aantoonbaar discrimineert.

# ── Lokale fallback ──────────────────────────────────────────────────────
# Als de primaire Hermes-backend faalt (geen API-key, timeout, 429-exhaust),
# produceert de agent-runner een deterministische concept-vuller i.p.v. de
# hele pijplijn te blokkeren. Die concepten scoren altijd < CONTENT_MIN_SCORE,
# dus ze belanden in 'needs_work' en worden NOOIT automatisch gepubliceerd.
HERMES_LOCAL_FALLBACK: bool = os.getenv("HERMES_LOCAL_FALLBACK", "1") == "1"

# Acquisitie-formule (input → output): hoeveel outreach-concepten de agent
# elke werkdag klaarzet ter review. Er wordt NOOIT automatisch verstuurd —
# versturen gebeurt alleen na expliciete goedkeuring in het Actiecentrum.
OUTREACH_DAILY_TARGET: int = int(os.getenv("OUTREACH_DAILY_TARGET", "10"))

# Linkbuilding: wekelijkse batch link-outreach-concepten die de agent klaarzet
# ter review, plus de kwalificatiedrempel (LLM-relevantiescore 0-100) waaronder
# een gevonden linkkans niet in de funnel komt. Er wordt NOOIT automatisch
# verstuurd — versturen kan alleen via de approve-knop in het Actiecentrum.
LINKBUILD_WEEKLY_TARGET: int = int(os.getenv("LINKBUILD_WEEKLY_TARGET", "10"))
LINKBUILD_MIN_SCORE: int = int(os.getenv("LINKBUILD_MIN_SCORE", "60"))
# Verzend-gate: default UIT. Als deze op "1"/"true" staat, mag de weekbatch de
# goedgekeurde concepten in één keer zélf versturen (Goldie-modus) in plaats van
# dat Vincent elke mail apart keurt. Blijft veilig: elke mail gaat alsnog door
# dezelfde e-mail- en Outlook-token-checks als de handmatige knop, en er wordt
# niets verstuurd naar een adres dat email_ok() afkeurt. Opt-in, nooit default aan.
LINKBUILD_AUTO_APPROVE: bool = os.getenv("LINKBUILD_AUTO_APPROVE", "0") in ("1", "true", "yes", "on")

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
# AGENTOS_DB_PATH override: tests draaien tegen een wegwerp-database, en elke
# klant-instance (zie AGENTOS_ENABLED_DOMAINS hieronder) tegen zijn eigen bestand.
DB_PATH = Path(os.getenv("AGENTOS_DB_PATH", str(DATA_DIR / "agentos.db")))

DATA_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── Domain-whitelist: welke onderdelen deze instance mag draaien ───────────
# Agent OS is single-tenant (één DB, één vault, één .env). Een klant krijgt
# daarom een eigen proces (eigen poort, eigen .env-waarden via de launch-
# script, eigen AGENTOS_DB_PATH/OBSIDIAN_VAULT_PATH) i.p.v. een gedeelde
# multi-tenant-laag — maar diezelfde instance monteert zonder deze knop nog
# steeds ALLE domeinen, inclusief Beursmeester/Finance/Prospecting die een
# klant nooit gevraagd heeft. AGENTOS_ENABLED_DOMAINS is de knop: een
# komma-gescheiden whitelist van domeinnamen. Leeg (de standaard, dus de
# bestaande WeAreImpact-installatie) betekent "alles aan" — bestaand gedrag
# blijft ongewijzigd. Routers/scheduler-jobs die geen expliciete domain-tag
# dragen zijn kern-functionaliteit (auth, health, action-center, chat-UI) en
# blijven altijd aan, ongeacht de whitelist.
_ENABLED_DOMAINS_RAW: str = os.getenv("AGENTOS_ENABLED_DOMAINS", "")
ENABLED_DOMAINS: set[str] = {
    d.strip() for d in _ENABLED_DOMAINS_RAW.split(",") if d.strip()
}


def domain_enabled(domain: str) -> bool:
    """True als `domain` gemonteerd/gepland mag worden op deze instance.

    Geen domain (lege string) = kernfunctionaliteit, altijd aan. Een niet-lege
    whitelist beperkt alleen de genoemde domeinen; alles wat niet in de
    whitelist staat, blijft uit — dat is het hele punt (een verkeerde klik mag
    op een klant-instance nooit bij Beursmeester/Finance kunnen komen).
    """
    if not domain:
        return True
    if not ENABLED_DOMAINS:
        return True
    return domain in ENABLED_DOMAINS


# Weergavenaam van de instance (sidebar, browsertab, loginscherm). Default
# ongewijzigd voor de hoofdinstallatie; een klant-instance zet dit op haar
# eigen merknaam zodat het meteen haar eigen omgeving voelt, niet "Agent OS
# met een paar uitgegrijsde tabs".
AGENTOS_INSTANCE_NAME: str = os.getenv("AGENTOS_INSTANCE_NAME", "Agent OS")

# Log-directory: een klant-instance draait vanuit dezelfde codebase-map als de
# hoofdinstallatie (geen code-duplicatie) maar mag nooit in hetzelfde
# logs/agentos.log schrijven — anders staan Nicole's mailfouten tussen
# WeAreImpact's linkbuilding-log. Default ongewijzigd (logs/ in de projectroot).
AGENTOS_LOG_DIR: str = os.getenv("AGENTOS_LOG_DIR", "")


def _is_real_key(value: str, prefix: str) -> bool:
    return bool(value) and value.startswith(prefix) and "your-key" not in value


def anthropic_configured() -> bool:
    return _is_real_key(ANTHROPIC_API_KEY, "sk-ant-")


def hermes_backend() -> str:
    """Returns which backend Hermes will use based on configured env vars.

    Volgorde: OpenModel (deepseek-v4-flash, primair) > lokaal/Ollama (gratis
    vangnet) > OpenRouter > Agnes > Anthropic.

    OpenModel/deepseek staat bewust vóór de local/Ollama-tier: die 'local'-tier
    wijst in de praktijk naar de Ollama-endpoint (llama3.1), een veel zwakker
    model. Met local eerst belandde ál het bulk/tool-werk op Ollama en werd
    deepseek nooit aangesproken (incident 2026-07-15). Nu is deepseek standaard
    voor het agent-pad, gelijk aan het denk-pad (claude.py).

    De quota-rem blijft het kostenvangnet: zegt de gateway 403 quota, dan slaan
    we OpenModel over en zakken we naar de gratis lokale Ollama-tier (native
    /api/generate, betrouwbaar in deze omgeving) — zo blijven de agents draaien
    zonder dure cloud-tokens te verbranden (incident 2026-07-10/11). Zonder
    local/Ollama proberen we OpenModel alsnog (laatste kans), dan
    OpenRouter/Agnes/Anthropic.

    Eerdere uitzondering (2026-08-10): de async-httpx naar Ollama's
    OpenAI-compat /v1/chat/completions hangt hier (lege response). Inmiddels
    gebruiken zowel de drafter als de agent-runner de NATIEVE /api/generate-
    route (getest betrouwbaar ~12s), dus de local-tier is bij quota-backoff de
    correcte fallback in plaats van de dode cloud-reserves.
    """
    from .outcomes import llm_quota_backoff_active
    if OPENMODEL_API_KEY and not llm_quota_backoff_active():
        return "openmodel"
    # Bij OpenModel-quota-backoff: zakt de route naar de gratis lokale Ollama.
    # De async-httpx naar /v1/chat/completions hangt wél in deze omgeving, maar
    # de agent-runner en drafter gebruiken inmiddels de NATIEVE /api/generate
    # (getest: ~12s, betrouwbaar) — dus de local-tier is hier de juiste
    # fallback, niet de dode cloud-reserves. Alleen overslaan als Ollama écht
    # niet is geconfigureerd.
    if HERMES_LOCAL_URL and HERMES_LOCAL_KEY:
        return "local"
    if OLLAMA_BASE_URL:
        return "ollama"
    if OPENMODEL_API_KEY:
        return "openmodel"  # laatste poging ondanks quota-backoff
    if OPENROUTER_API_KEY:
        return "openrouter"
    if AGNES_API_KEY:
        return "agnes"  # allerlaatste fallback — nooit primair
    return "anthropic"


# Model dat OpenRouter gebruikt voor de Claude-agent als Anthropic niet geconfigureerd is.
# DEFAULT op DeepSeek (geen Claude meer — regel Vincent 2026-08-10).
CLAUDE_VIA_OPENROUTER: str = _no_claude(os.getenv("CLAUDE_VIA_OPENROUTER", "deepseek/deepseek-v4-pro"))

# ── Google Agenda (calendar) ───────────────────────────────────────────
# Twee manieren om de serviceaccount te leveren (kies één):
#  A) Inline (makkelijkst te kopiëren uit WeAreImpact .env.local):
#       CALENDAR_CLIENT_EMAIL  = agendaweareimpact@weareimpact-482912.iam.gserviceaccount.com
#       CALENDAR_PRIVATE_KEY   = "-----BEGIN PRIVATE KEY-----\n..."
#  B) JSON-bestandspad:
#       CALENDAR_SERVICE_ACCOUNT_PATH = /pad/naar/serviceaccount.json
#       (default: hergebruikt het GSC/GA4-serviceaccount)
# Voor een persoonlijke/Workspace-agenda met Domain-Wide Delegation zet je
# CALENDAR_SUB op het impersonatie-adres (bijv. v.munster@weareimpact.nl).
# CALENDAR_CALENDAR_ID is de gedeelde agenda (of 'primary' voor de eigenaar).
CALENDAR_CLIENT_EMAIL: str = os.getenv("CALENDAR_CLIENT_EMAIL", "")
CALENDAR_PRIVATE_KEY: str = os.getenv("CALENDAR_PRIVATE_KEY", "").replace(
    "\\n", "\n"
)
CALENDAR_SERVICE_ACCOUNT_PATH: str = (
    os.getenv("CALENDAR_SERVICE_ACCOUNT_PATH", "") or GSC_SERVICE_ACCOUNT_PATH
)
CALENDAR_SUB: str = os.getenv("CALENDAR_SUB", "")
# Schrijf-agenda: hier landen goedgekeurde afspraak-voorstellen.
CALENDAR_CALENDAR_ID: str = os.getenv("CALENDAR_CALENDAR_ID", "primary")
# Lees-agenda's voor conflict-detectie (komma-gescheiden). Bewust los van de
# schrijf-agenda: de agent schrijft naar zijn eigen agenda, maar "is dit slot
# vrij?" moet óók Vincents persoonlijke agenda meenemen — anders plant hij
# vrolijk bovenop een klantafspraak die hij niet kan zien. Leeg = alleen de
# schrijf-agenda (en dan is de conflict-check dus zo blind als die agenda).
CALENDAR_BUSY_CALENDAR_IDS: list = [
    c.strip() for c in os.getenv("CALENDAR_BUSY_CALENDAR_IDS", "").split(",")
    if c.strip()
] or [CALENDAR_CALENDAR_ID]

# Microsoft Outlook / Graph API
# Registreer een Azure AD app (portal.azure.com > App registrations) met:
#   Delegated permissions: Mail.Read, Mail.ReadWrite, Mail.Send, User.Read,
#   Calendars.ReadWrite (die laatste dekt ook CALENDAR_BACKEND=outlook)
#   Redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient
OUTLOOK_CLIENT_ID: str = os.getenv("OUTLOOK_CLIENT_ID", "")
OUTLOOK_TENANT_ID: str = os.getenv("OUTLOOK_TENANT_ID", "common")

# Per-klant OAuth (Iris-onboarding, domains/onboarding/) — losstaand van de
# device-flow hierboven, die een public client is (geen secret) en één
# globaal account levert. De browser-redirect authorization-code-uitwisseling
# zelf loopt sinds de verhuizing naar Iris Remote (CLAUDE.md 14) via
# `remote/api/oauth.js` op Vercel — dezelfde client_id/secret staan dus ook
# in de Vercel-omgevingsvariabelen. Hier lokaal blijven ze nodig voor de
# ná-koppeling: het ververs-token-endpoint aanroepen
# (`oauth_google.py`/`oauth_microsoft.py:_refresh`) gebeurt wél lokaal, dus
# zonder deze twee waarden hier kan een gekoppeld account niet blijven werken
# nadat het eerste access-token verloopt.
OUTLOOK_CLIENT_SECRET: str = os.getenv("OUTLOOK_CLIENT_SECRET", "")

# Google OAuth-client voor per-klant GSC/Calendar-koppeling (Iris-onboarding).
# Los van GSC_SERVICE_ACCOUNT_PATH hierboven: dat is één gedeeld service-account
# voor het hele systeem, dit is een OAuth 2.0-client (Google Cloud Console >
# APIs & Services > Credentials > OAuth client ID > type Web application)
# waarmee elke klant zijn eigen account koppelt. Zelfde verhaal als
# OUTLOOK_CLIENT_SECRET hierboven: de uitwisseling zelf gebeurt op Vercel,
# het lokale ververs-token-endpoint gebruikt deze waarden nog wel.
GOOGLE_OAUTH_CLIENT_ID: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Agenda-backend per instance: 'google' (serviceaccount, default — WeAreImpact)
# of 'outlook' (Microsoft Graph via dezelfde delegated OAuth-login als Mail —
# voor klanten die zelf Outlook gebruiken, bv. Nicole @ WE SHAPE THE FUTURE).
# Beide leveren dezelfde functiesignaturen (domains/calendar/service.py
# dispatcht hierop), dus de agenda-agent en alle aanroepers blijven ongewijzigd.
CALENDAR_BACKEND: str = os.getenv("CALENDAR_BACKEND", "google").strip().lower()

# LinkedIn — Personal Access Token voor social posting
LINKEDIN_ACCESS_TOKEN: str = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_USER_URN: str = os.getenv("LINKEDIN_USER_URN", "")  # optioneel: urn:li:person:xxx

# Facebook — Page Access Token (Graph API). Per site kun je een eigen page-id/token
# zetten in de sites-tabel (facebook_page_id / facebook_page_token).
FACEBOOK_PAGE_ID: str = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_PAGE_TOKEN: str = os.getenv("FACEBOOK_PAGE_TOKEN", "")

# Instagram — Graph API content publishing via een aan de Facebook-pagina gekoppeld
# Business/Creator-account. Gebruikt hetzelfde page-token als Facebook.
INSTAGRAM_BUSINESS_ID: str = os.getenv("INSTAGRAM_BUSINESS_ID", "")

# Canva Connect API (server-to-server design-aanmaak). Vereist een OAuth-app
# met client_id + client_secret + een refresh-token uit de éénmalige browser-
# flow. LET OP: CANVA_APP_ID / CANVA_APP_ORIGIN / CANVA_HMR_ENABLED zijn de
# Canva *Apps SDK* vars en werken NIET voor Connect — die zijn voor het bouwen
# van een app binnen de Canva-editor, niet voor backend-design-creatie.
CANVA_CLIENT_ID: str = os.getenv("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET: str = os.getenv("CANVA_CLIENT_SECRET", "")
CANVA_REFRESH_TOKEN: str = os.getenv("CANVA_REFRESH_TOKEN", "")
# OAuth-redirect-URI — moet exact overeenkomen met wat je in de Canva
# Connect-app hebt ingevuld (developer.canva.com > je app > OAuth redirect).
CANVA_REDIRECT_URI: str = os.getenv("CANVA_REDIRECT_URI", "https://www.canva.com/design/")
# Brand-Template Autofill: het ID van JE vaste 'Insta/FB advertenties'-template
# (moet een Canva Brand Template zijn waarvan de tekstvelden als data-veld
# zijn gemarkeerd). Zonder dit maakt Canva Connect alleen een LEEG poster-design.
CANVA_BRAND_TEMPLATE_ID: str = os.getenv("CANVA_BRAND_TEMPLATE_ID", "")
# Optioneel: map van brief-veld -> Canva data-veldnaam (komma-gescheiden "k=v").
# Zonder dit: headline->Headline, subtext->Subtext.
CANVA_TEMPLATE_FIELDS: str = os.getenv(
    "CANVA_TEMPLATE_FIELDS", "headline=Headline,subtext=Subtext"
)
# Optioneel: zet nieuwe designs in deze Canva-folder (folder-id).
CANVA_FOLDER_ID: str = os.getenv("CANVA_FOLDER_ID", "")

# X (Twitter) — API v2, OAuth 1.0a user-context (nodig voor POST /2/tweets).
TWITTER_API_KEY: str = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET: str = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET: str = os.getenv("TWITTER_ACCESS_SECRET", "")

# ── Beursmeester (domains/invest) ─────────────────────────────────────────
# Een papieren portefeuille die op de koers vult die het model zag en geen
# kosten rekent, verslaat elke index. Deze getallen zijn er om dat te
# voorkomen; ze horen eerder pessimistisch te staan dan optimistisch.
INVEST_START_CAPITAL: float = float(os.getenv("INVEST_START_CAPITAL", "10000"))
INVEST_FEE_FIXED: float = float(os.getenv("INVEST_FEE_FIXED", "2.0"))      # € per order
INVEST_FEE_PCT: float = float(os.getenv("INVEST_FEE_PCT", "0.0005"))       # 0,05% van de ordergrootte
INVEST_SLIPPAGE_BPS: float = float(os.getenv("INVEST_SLIPPAGE_BPS", "10")) # 0,10% voor beursuren-instrumenten
INVEST_SLIPPAGE_BPS_CRYPTO: float = float(os.getenv("INVEST_SLIPPAGE_BPS_CRYPTO", "25"))

# Risicoklemmen. Deterministisch en niet-onderhandelbaar: ze mogen de analist
# overrulen, nooit andersom.
INVEST_RISK_PER_TRADE: float = float(os.getenv("INVEST_RISK_PER_TRADE", "0.01"))   # 1% van NAV per idee
INVEST_MAX_POSITION_PCT: float = float(os.getenv("INVEST_MAX_POSITION_PCT", "0.15"))
INVEST_MAX_CLASS_PCT: float = float(os.getenv("INVEST_MAX_CLASS_PCT", "0.35"))
INVEST_MAX_CRYPTO_PCT: float = float(os.getenv("INVEST_MAX_CRYPTO_PCT", "0.25"))
INVEST_MAX_DAY_LOSS_PCT: float = float(os.getenv("INVEST_MAX_DAY_LOSS_PCT", "0.03"))
INVEST_MAX_DRAWDOWN_PCT: float = float(os.getenv("INVEST_MAX_DRAWDOWN_PCT", "0.15"))
INVEST_COOLDOWN_DAYS: int = int(os.getenv("INVEST_COOLDOWN_DAYS", "5"))

# Noodrem. Staat dit op 1, dan komen er geen voorstellen en worden er geen
# orders uitgevoerd — ongeacht wat de analist vindt.
INVEST_KILL_SWITCH: bool = os.getenv("INVEST_KILL_SWITCH", "0") == "1"

# Claude Code als denkende partner (Pro-abonnement, geen API-kosten).
# Leeg pad = uitgeschakeld; dan valt de analyse terug op de gateway en draagt
# elk voorstel het label 'terugval'.
CLAUDE_CODE_BIN: str = os.getenv("CLAUDE_CODE_BIN", "claude")
CLAUDE_CODE_ENABLED: bool = os.getenv("CLAUDE_CODE_ENABLED", "1") == "1"
CLAUDE_CODE_TIMEOUT: int = int(os.getenv("CLAUDE_CODE_TIMEOUT", "900"))    # seconden
CLAUDE_CODE_MAX_RUNS_PER_DAY: int = int(os.getenv("CLAUDE_CODE_MAX_RUNS_PER_DAY", "8"))
