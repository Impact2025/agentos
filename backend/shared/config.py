import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MODEL_2: str = os.getenv("CLAUDE_MODEL_2", "claude-haiku-4-5-20251001")

# Hermes agent — priority: lokaal (127.0.0.1:8642) > Ollama > OpenModel > OpenRouter > Anthropic
HERMES_LOCAL_URL: str = os.getenv("HERMES_LOCAL_URL", "")
HERMES_LOCAL_KEY: str = os.getenv("HERMES_LOCAL_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# OpenModel.ai — Anthropic-compatible gateway (DeepSeek V4 Flash e.a.)
OPENMODEL_API_KEY: str = os.getenv("OPENMODEL_API_KEY", "")
OPENMODEL_BASE_URL: str = os.getenv("OPENMODEL_BASE_URL", "https://api.openmodel.ai")
OPENMODEL_MODEL: str = os.getenv("OPENMODEL_MODEL", "deepseek-v4-flash")
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

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "agentos.db"

DATA_DIR.mkdir(exist_ok=True)


def _is_real_key(value: str, prefix: str) -> bool:
    return bool(value) and value.startswith(prefix) and "your-key" not in value


def anthropic_configured() -> bool:
    return _is_real_key(ANTHROPIC_API_KEY, "sk-ant-")


def hermes_backend() -> str:
    """Returns which backend Hermes will use based on configured env vars."""
    if HERMES_LOCAL_URL and HERMES_LOCAL_KEY:
        return "local"
    if OLLAMA_BASE_URL:
        return "ollama"
    if OPENMODEL_API_KEY:
        return "openmodel"
    if OPENROUTER_API_KEY:
        return "openrouter"
    return "anthropic"


# Model dat OpenRouter gebruikt voor de Claude-agent als Anthropic niet geconfigureerd is
CLAUDE_VIA_OPENROUTER: str = os.getenv("CLAUDE_VIA_OPENROUTER", "anthropic/claude-sonnet-4-5")

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
