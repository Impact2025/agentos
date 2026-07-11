"""Draft-generator: laat een LLM een warm, concreet helpdesk-antwoord schrijven
in de merkstem van het project, in de taal van de vraagsteller.

Backend-keuze (expliciet, geen gok) — instelbaar via MAIL_DRAFT_BACKEND:
  * 'openmodel' (default): OpenModel.ai (jullie vaste gateway) primair,
    Claude-client (Anthropic / OpenRouter) als terugval.
  * 'claude': Claude primair (beste kwaliteit — aanbevolen voor klantcontact,
    het volume is klein), OpenModel als terugval.
Bij geen werkende backend: leesbare placeholder (geen crash) — de
review-gate vangt dat op en jij vult handmatig in.
"""
import asyncio
import os
import re
from typing import List, Dict

import httpx

from ...shared.config import (
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
)

MAIL_DRAFT_BACKEND = os.getenv("MAIL_DRAFT_BACKEND", "openmodel").strip().lower()

SYSTEM_TEMPLATE = (
    "Je bent de eerste-lijn helpdesk voor {brand}.\n"
    "Schrijf als de eigenaar van {brand} (Vincent van Munster, oprichter van "
    "WeAreImpact), in de eerste persoon (ik/wij), warm en nuchter — alsof je bij "
    "iemand aan de keukentafel zit. Geen robot-taal, geen uitroeptekens-geweld, "
    "geen kille tech-taal.\n"
    "Antwoord in de taal van de klant (herken NL/EN automatisch aan de mail).\n"
    "Wees concreet: geef waar mogelijk een directe stap, link of handeling.\n"
    "Verzin géén garanties, prijzen, features of feiten die NIET in de "
    "kennisbasis hieronder staan. Weet je iets niet zeker? Zeg dat eerlijk en "
    "bied aan het door te spelen aan het team — beloof nooit een onbekende oplossing.\n"
    "Links: gebruik ALLEEN URL's die letterlijk in de kennisbasis staan. Schrijf "
    "nooit een placeholder zoals '[jouw domein]' of een verzonnen pad — staat de "
    "juiste link er niet bij, beschrijf de stap dan in woorden.\n"
    "Is er eerdere correspondentie meegegeven? Behandel de mail dan als vervolg: "
    "niet opnieuw voorstellen, niet dezelfde uitleg herhalen, maar doorpakken op "
    "waar het gesprek was gebleven.\n"
    "Maximaal 150 woorden.\n"
)

# Afsluiting hangt af van of de mailbox een vaste handtekening heeft: die wordt
# ná het genereren onder het concept geplakt, dus de LLM mag dan niet zelf ook
# nog eens ondertekenen (dubbele groet oogt knullig).
_CLOSING_WITH_SIGNATURE = (
    "Sluit af met alléén een korte groetregel (bijv. 'Hartelijke groet,') — "
    "GEEN naam, merk of handtekening eronder; de vaste handtekening van {brand} "
    "wordt automatisch onder je antwoord gezet."
)
_CLOSING_NO_SIGNATURE = (
    "Eindig met een vriendelijke groet onder de naam van {brand}."
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
        if resp.status_code == 403 and "quota" in resp.text.lower():
            from ...shared.outcomes import note_llm_quota_exhausted
            note_llm_quota_exhausted(backend="openmodel", model=payload["model"], route="mail")
            raise RuntimeError(
                "OpenModel-dagquota op (403 quota exceeded) — wacht op de reset "
                "of verhoog de quota op openmodel.ai"
            )
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage") or {}
    if usage:
        from ...shared.outcomes import log_llm_usage
        log_llm_usage(
            backend="openmodel", model=payload["model"], route="mail",
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        )
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
                                 system_prompt=system, max_tokens=800, purpose="mail")
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
    history: str = "",
    has_signature: bool = False,
) -> str:
    brand = brand_context or "dit project"
    lang = detect_language(subject + " " + body)
    lang_note = {
        "nl": "De klant schrijft Nederlands — antwoord in het Nederlands.",
        "en": "The customer writes in English — reply in English.",
    }.get(lang, "Herken de taal van de klant en antwoord in diezelfde taal.")
    closing = (_CLOSING_WITH_SIGNATURE if has_signature else _CLOSING_NO_SIGNATURE)
    system = (SYSTEM_TEMPLATE.format(brand=brand) + closing.format(brand=brand)
              + "\n\n" + lang_note)
    if knowledge:
        system += f"\n\n— KENNISBASIS (hieronder staat wat je WÉL mag zeggen over het project, de app en de maker) —\n{knowledge}"
    user = ""
    if history:
        user += (
            "— EERDERE CORRESPONDENTIE met deze klant (oud → nieuw) —\n"
            f"{history}\n\n— NIEUWE MAIL (beantwoord déze) —\n"
        )
    user += f"Van: {from_name}\nOnderwerp: {subject}\n\n{body}"

    import logging
    log = logging.getLogger(__name__)

    if MAIL_DRAFT_BACKEND == "claude":
        # Claude primair (beste kwaliteit voor klantcontact), OpenModel-vangnet.
        out = _sync_claude_fallback(system, user).strip()
        if not out.startswith("[Kon geen antwoord genereren"):
            return out
        log.warning("Claude draft mislukt — terugval op OpenModel")
        if OPENMODEL_API_KEY:
            try:
                return _sync_openmodel(system, user).strip()
            except Exception as e:
                log.warning("OpenModel draft mislukt: %s", e)
        return out  # leesbare placeholder — review-gate vangt dit op

    # Default: OpenModel.ai (jullie vaste gateway) primair
    if OPENMODEL_API_KEY:
        try:
            return _sync_openmodel(system, user).strip()
        except Exception as e:
            # niet stilzwijgend falen — loggen en dan terugvallen
            log.warning("OpenModel draft mislukt: %s", e)
    # Terugval op Claude-agent (Anthropic / OpenRouter)
    return _sync_claude_fallback(system, user).strip()
