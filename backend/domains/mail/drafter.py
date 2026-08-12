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
from typing import List, Dict, Optional, Tuple

import httpx

from .pootgelukkig_referral import (
    detect_pootgelukkig_opportunity,
    referral_instruction,
)
from ...shared.config import (
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
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
        text = "".join(
            part.get("text", "") for part in data["content"] if part.get("type") == "text"
        )
        # Soms komt er alleen een 'thinking'-blok terug en géén 'text' (bij grote
        # prompts) — dat is géén geldig antwoord. Geef leeg terug zodat de
        # fallback-keten (Claude → Ollama) het overneemt i.p.v. een lege draft.
        return text
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return data.get("text", "")


def _sync_ollama(system: str, user: str) -> str:
    """Lokale Ollama-vangnet (gratis, geen cloud-quota nodig).

    Wordt gebruikt als OpenModel/Claude allebei op zijn (403 quota) maar de
    lokale Ollama wel draait — zo blijft Iris alsnog stijl-antwoorden
    schrijven. Geen SEO/clickbait-prompt, dus llama3.1 weigert niet.

    BELANGRIJK: alleen de NATIEVE /api/generate-route werkt hier betrouwbaar.
    De OpenAI-compat /v1/chat/completions hangt in deze omgeving (lege response,
    zie incident 2026-08-10) — dus niet die gebruiken.
    """
    if not OLLAMA_BASE_URL or not OLLAMA_MODEL:
        return ""
    base = OLLAMA_BASE_URL.rstrip("/").replace("/v1", "")
    url = base + "/api/generate"
    prompt = f"{system}\n\n{user}"
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=180) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("response") or "").strip()


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
    # Iris-regel: optionele Pootgelukkig-referral. Alleen wanneer er een echte
    # kans is (dier-/adoptie-signaal + WeAreImpact-achtige brand-context) krijgt
    # de LLM een zachte hint — nooit verplicht, de mens keurt alsnog goed.
    opp = detect_pootgelukkig_opportunity(subject, body, brand_context)
    ref_instr = referral_instruction(opp)
    if ref_instr:
        system += ref_instr
    if knowledge:
        # OpenModel (deepseek-v4-flash) geeft bij een te grote system-prompt
        # geregeld alleen een 'thinking'-blok terug en géén 'text' → lege draft.
        # De kennisbank van grote projecten (WeAreImpact) loopt makkelijk over de
        # 200K chars heen, dus clippen we hem tot een veilige bovengrens. De
        # drafter mist dan hooguit wat dieperliggende details; liever een goed
        # antwoord met minder context dan een lege draft die de review-gate vult.
        _MAX_KNOWLEDGE = 6000
        if len(knowledge) > _MAX_KNOWLEDGE:
            knowledge = knowledge[:_MAX_KNOWLEDGE].rsplit("\n", 1)[0] + "\n[…kennis ingekort]"
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
    claude_out = _sync_claude_fallback(system, user).strip()
    if claude_out and not claude_out.startswith("[Kon geen antwoord genereren"):
        return claude_out
    # Laatste vangnet: lokale Ollama — draait gratis lokaal, ook als de
    # cloud-quota op is. Zonder dit bleef Iris stil bij een OpenModel-403.
    if OLLAMA_BASE_URL and OLLAMA_MODEL:
        try:
            o = _sync_ollama(system, user).strip()
            if o:
                return o
        except Exception as e:
            log.warning("Ollama draft mislukt: %s", e)
    return claude_out  # leesbare placeholder — review-gate vangt dit op


def draft_reply_with_referral(
    from_name: str,
    subject: str,
    body: str,
    brand_context: str,
    knowledge: str,
    history: str = "",
    has_signature: bool = False,
) -> Tuple[str, Optional[dict]]:
    """Zelfde als draft_reply(), maar geeft ook de referral-kans terug.

    Return: (draft_tekst, opportunity_dict_of_None). De caller kan de
    opportunity gebruiken om in de UI een 'Pootgelukkig-suggestie'-vlag te
    tonen, zodat Vincent weet dat Iris een kans zag (en 'm met één klik kan
    weglaten bij Verstuur/Bewerk).
    """
    opp = detect_pootgelukkig_opportunity(subject, body, brand_context)
    draft = draft_reply(
        from_name=from_name,
        subject=subject,
        body=body,
        brand_context=brand_context,
        knowledge=knowledge,
        history=history,
        has_signature=has_signature,
    )
    return draft, opp
