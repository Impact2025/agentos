"""Draft-generator: laat een LLM een warm, concreet helpdesk-antwoord schrijven
in de merkstem van het project, in de taal van de vraagsteller.

Backend-keuze (expliciet, geen gok):
  1. OpenModel.ai (jullie vaste gateway, OPENMODEL_API_KEY) — primair.
  2. Terugval op de bestaande Claude-client (chat/claude) als OpenModel
     niet geconfigureerd is (Anthropic / OpenRouter).
Bij geen werkende backend: leesbare placeholder (geen crash) — de
review-gate vangt dat op en jij vult handmatig in.
"""
import asyncio
import re
from typing import List, Dict

import httpx

from ...shared.config import (
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)

SYSTEM_TEMPLATE = (
    "Je bent de eerste-lijn helpdesk voor {brand}.\n"
    "Schrijf een helder, warm antwoord in de taal van de klant "
    "(herken NL/EN automatisch aan de binnenkomende mail).\n"
    "Toon als de eigenaar van {brand}: kort, concreet, geen robot-taal, "
    "geen uitroeptekens-geweld. Geef waar mogelijk een directe stap of link.\n"
    "Verzin géén garanties, prijzen of features die niet in de kennis staan.\n"
    "Maximaal 150 woorden. Eindig met een vriendelijke groet.\n"
    "Weet je het antwoord niet zeker? Zeg dat eerlijk en bied aan het door te "
    "spelen aan het team — beloof nooit een onbekende oplossing."
)

# Woorden die sterk wijzen op Nederlands — voor goedkope, deterministische
# taaldetectie zonder externe dep (de LLM herkent de rest zelf).
_NL_HINTS = (
    "de", "het", "ik", "je", "mijn", "hoe", "wat", "niet", "een", "is", "met",
    "van", "en", "deze", "voor", "wachtwoord", "account", "help", "alstublieft",
    "bedankt", "groet",
)
_EN_HINTS = (
    "the", "i", "my", "you", "how", "what", "not", "a", "is", "with", "of",
    "and", "this", "for", "password", "account", "help", "please", "thanks",
    "hello", "hi",
)


def detect_language(text: str) -> str:
    """'nl' | 'en' | 'onbekend'. Deterministiche heuristiek op veelvoorkomende
    woorden; goed genoeg om de LLM de juiste schrijftaal mee te geven."""
    t = (text or "").lower()
    words = re.findall(r"[a-zà-úï]+", t)
    if not words:
        return "onbekend"
    nl = sum(1 for w in words if w in _NL_HINTS)
    en = sum(1 for w in words if w in _EN_HINTS)
    if nl > en and nl >= 1:
        return "nl"
    if en > nl and en >= 1:
        return "en"
    return "onbekend"


def _sync_openmodel(system: str, user: str) -> str:
    """OpenModel.ai is Anthropic-compatible (/v1/messages, x-api-key)."""
    url = (OPENMODEL_BASE_URL or "https://api.openmodel.ai").rstrip("/") + "/v1/messages"
    payload = {
        "model": OPENMODEL_MODEL or "deepseek-v4-flash",
        "max_tokens": 800,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            url,
            headers={
                "x-api-key": OPENMODEL_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    # OpenModel geeft ofwel Anthropic-vorm (content[].text) of OpenAI-vorm (choices)
    if "content" in data:
        return "".join(
            part.get("text", "") for part in data["content"] if part.get("type") == "text"
        )
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def _sync_claude_fallback(system: str, user: str) -> str:
    from ..chat import claude

    try:
        return asyncio.run(
            claude.get_response(messages=[{"role": "user", "content": user}],
                                 system_prompt=system, max_tokens=800)
        )
    except Exception as e:
        return (
            f"[Kon geen antwoord genereren: {e}. Beantwoord handmatig.]\n\n"
            f"(Originele vraag hierboven.)"
        )


def draft_reply(
    from_name: str,
    subject: str,
    body: str,
    brand_context: str,
    knowledge: str,
) -> str:
    brand = brand_context or "dit project"
    lang = detect_language(subject + " " + body)
    lang_note = {
        "nl": "De klant schrijft Nederlands — antwoord in het Nederlands.",
        "en": "The customer writes in English — reply in English.",
    }.get(lang, "Herken de taal van de klant en antwoord in diezelfde taal.")
    system = SYSTEM_TEMPLATE.format(brand=brand) + "\n\n" + lang_note
    if knowledge:
        system += f"\n\nBeschikbare kennis/FAQ:\n{knowledge}"
    user = f"Van: {from_name}\nOnderwerp: {subject}\n\n{body}"

    # 1) OpenModel.ai (jullie vaste gateway) — primair
    if OPENMODEL_API_KEY:
        try:
            return _sync_openmodel(system, user).strip()
        except Exception as e:
            # niet stilzwijgend falen — loggen en dan terugvallen
            import logging
            logging.getLogger(__name__).warning("OpenModel draft mislukt: %s", e)
    # 2) Terugval op Claude-agent (Anthropic / OpenRouter)
    return _sync_claude_fallback(system, user).strip()
