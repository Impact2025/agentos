import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MODEL_2: str = os.getenv("CLAUDE_MODEL_2", "claude-haiku-4-5-20251001")

# Hermes agent — priority: lokaal > OpenModel (deepseek-v4-flash, goedkoop, primair)
#   > Ollama (lokaal llama3.1, GRATIS backup, geen quota) > OpenRouter > Anthropic.
# Bij een OpenModel 403 quota-exceeded schakelt hermes_backend tijdelijk over naar
# Ollama (zie llm_quota_backoff_active) zodat de agents gratis doorlopen.
HERMES_LOCAL_URL: str = os.getenv("HERMES_LOCAL_URL", "")
HERMES_LOCAL_KEY: str = os.getenv("HERMES_LOCAL_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# OpenModel.ai — Anthropic-compatible gateway (DeepSeek V4 Flash e.a.)
OPENMODEL_API_KEY: str = os.getenv("OPENMODEL_API_KEY", "")
OPENMODEL_BASE_URL: str = os.getenv("OPENMODEL_BASE_URL", "https://api.openmodel.ai")
OPENMODEL_MODEL: str = os.getenv("OPENMODEL_MODEL", "deepseek-v4-flash")
# Sterk model op dezelfde gateway voor denk-werk (Iris-analyse, kwaliteitsgate,
# goal-synthese, drafts): het Claude-pad in de app loopt hierover zodra er geen
# directe Anthropic-key is. Bulk-/toolwerk loopt op OPENMODEL_MODEL (flash).
# Default is bewust deepseek-v4-flash: claude-sonnet-4-6 verbrandde ~10x zoveel
# krediet (incident 2026-07-11, $4,90 op één dag). Wil je alsnog het dure Claude-
# pad, zet dan expliciet OPENMODEL_SMART_MODEL=claude-sonnet-4-6 in .env.
OPENMODEL_SMART_MODEL: str = os.getenv("OPENMODEL_SMART_MODEL", "deepseek-v4-flash")
HERMES_MODEL: str = os.getenv("HERMES_MODEL", "meta-llama/llama-3.1-8b-instruct")
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
HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")

# Netlify publisher — globale fallback-token (Personal Access Token). Per site kun
# je een eigen token + site-ID zetten in de sites-tabel (publish_api_key / publish_api_url).
NETLIFY_TOKEN: str = os.getenv("NETLIFY_TOKEN", "")

OBSIDIAN_VAULT_PATH: str = os.getenv("OBSIDIAN_VAULT_PATH", "")

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

# Google Indexing API (urlNotifications.publish) voor directe indexering na
# publicatie. Default uit: officieel alleen voor JobPosting/Livestream-content
# en het service-account moet Owner zijn in Search Console. De nette route
# (sitemap-submit bij GSC) staat altijd aan.
GOOGLE_INDEXING_ENABLED: bool = os.getenv("GOOGLE_INDEXING_ENABLED", "0") == "1"

# Kwaliteitsgate voor content (0-100): onder deze score komt een artikel niet
# in de Wachtrij als publiceerbaar en weigert de publish-API. De pipeline
# probeert eerst automatisch te verbeteren (max 3 rondes).
# Wereldklasse-standaard: 85. Een artikel moet écht AEO-/rich-result-klaar zijn
# (direct-answer + FAQ + E-E-A-T + schone links) om gepubliceerd te worden.
CONTENT_MIN_SCORE: int = int(os.getenv("CONTENT_MIN_SCORE", "85"))

# Hoeveel verbeterrondes de agent mág doen voordat hij opgeeft. Dit is een
# HARDE veiligheidslimiet (tegen eindeloze LLM-loops), geen streefwaarde: de
# review-loop stopt pas als de score ≥ CONTENT_MIN_SCORE OF dit aantal rondes
# op is. Ruim gezet (standaard 12) zodat een artikel in de praktijk bijna altijd
# boven de 85-grens uitkomt — en dus nooit als "onder de grens" op het dashboard
# (en bij Vincent) belandt. Lokale/fallback-concepten (HERMES_LOCAL_FALLBACK)
# scoren altijd < grens en worden hierdoor bewust niet oneindig geprobeerd.
CONTENT_MAX_ROUNDS: int = int(os.getenv("CONTENT_MAX_ROUNDS", "5"))

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
DAILY_TOKEN_BUDGET: int = int(os.getenv("DAILY_TOKEN_BUDGET", "600000"))

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

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
# AGENTOS_DB_PATH override: tests draaien tegen een wegwerp-database.
DB_PATH = Path(os.getenv("AGENTOS_DB_PATH", str(DATA_DIR / "agentos.db")))

DATA_DIR.mkdir(exist_ok=True)


def _is_real_key(value: str, prefix: str) -> bool:
    return bool(value) and value.startswith(prefix) and "your-key" not in value


def anthropic_configured() -> bool:
    return _is_real_key(ANTHROPIC_API_KEY, "sk-ant-")


def hermes_backend() -> str:
    """Returns which backend Hermes will use based on configured env vars.

    Volgorde: lokaal > OpenModel (deepseek-v4-flash, primair/goedkoop) >
    Ollama (lokaal llama3.1, gratis backup) > OpenRouter > Anthropic.
    Als de OpenModel-provider recent 403 quota zei, slaan we OpenModel over en
    vallen we meteen terug op Ollama — zo blijven de agents draaien zonder
    dure cloud-tokens te verbranden (incident 2026-07-10/11)."""
    from .outcomes import llm_quota_backoff_active
    if HERMES_LOCAL_URL and HERMES_LOCAL_KEY:
        return "local"
    if OPENMODEL_API_KEY and not llm_quota_backoff_active():
        return "openmodel"
    if OLLAMA_BASE_URL:
        return "ollama"
    if OPENMODEL_API_KEY:
        return "openmodel"  # laatste poging ondanks quota-backoff
    if OPENROUTER_API_KEY:
        return "openrouter"
    return "anthropic"


# Model dat OpenRouter gebruikt voor de Claude-agent als Anthropic niet geconfigureerd is
CLAUDE_VIA_OPENROUTER: str = os.getenv("CLAUDE_VIA_OPENROUTER", "anthropic/claude-sonnet-4-5")

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
CALENDAR_CALENDAR_ID: str = os.getenv("CALENDAR_CALENDAR_ID", "primary")

# Microsoft Outlook / Graph API
# Registreer een Azure AD app (portal.azure.com > App registrations) met:
#   Delegated permissions: Mail.Read, Mail.ReadWrite, Mail.Send, User.Read
#   Redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient
OUTLOOK_CLIENT_ID: str = os.getenv("OUTLOOK_CLIENT_ID", "")
OUTLOOK_TENANT_ID: str = os.getenv("OUTLOOK_TENANT_ID", "common")

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

# X (Twitter) — API v2, OAuth 1.0a user-context (nodig voor POST /2/tweets).
TWITTER_API_KEY: str = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET: str = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET: str = os.getenv("TWITTER_ACCESS_SECRET", "")
