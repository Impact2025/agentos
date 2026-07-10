"""Draft-generator: laat de LLM een warm, concreet helpdesk-antwoord schrijven
in de merkstem van het project, in de taal van de vraagsteller.

Gebruikt de bestaande Claude-client (domains/chat/claude.get_response) met
terugval op OpenRouter. Bij ontbrekende LLM-key valt hij terug op een
leesbare placeholder in plaats van te crashen — de review-gate vangt dat op.
"""
import asyncio
import re
from typing import List, Dict

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

# Woorden die sterk wijzen op Nederlands — voor goedkope, deterministiche
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


def _sync_llm(system: str, user: str) -> str:
    from ..chat import claude

    messages: List[Dict] = [{"role": "user", "content": user}]
    try:
        return asyncio.run(claude.get_response(messages=messages, system_prompt=system, max_tokens=800))
    except Exception as e:  # geen LLM beschikbaar: leesbare placeholder
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
    return _sync_llm(system, user)
