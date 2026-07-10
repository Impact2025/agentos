"""
Claude-integratie met drie routes, in volgorde van voorkeur:

  1. Anthropic direct        — echte ANTHROPIC_API_KEY (sk-ant-…)
  2. OpenModel.ai-gateway    — jullie vaste gateway spreekt het Anthropic
                               Messages-formaat en biedt de Claude-modellen aan
                               (OPENMODEL_SMART_MODEL, default claude-sonnet-4-6).
                               Dit is op deze machine de primaire route: zo
                               draait al het denk-werk (Iris, kwaliteitsgate,
                               goal-synthese, drafts) op een topmodel zonder
                               directe Anthropic-key.
  3. OpenRouter              — laatste terugval (CLAUDE_VIA_OPENROUTER).

De OpenModel-route is bewust non-streaming: de gateway retourneert op
/v1/messages een volledig message-object (geen betrouwbare SSE), dus we
parsen zelf en bewaken stop_reason — een op max_tokens afgekapt antwoord is
de klassieke oorzaak van 'halve JSON' en wordt hier expliciet gelogd.
"""
import json
import logging
from typing import AsyncGenerator, List, Dict

import anthropic
import httpx

from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, OPENROUTER_API_KEY,
    CLAUDE_VIA_OPENROUTER, anthropic_configured,
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_SMART_MODEL,
)

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None

# Denk-werk-prompts zijn fors (Iris-briefing, artikel-herschrijfrondes van
# 4500+ tokens); de non-streaming gateway doet daar in de praktijk tot ~8
# minuten over. Een te krappe timeout uit zich als 'transiente fout' × 2 en
# kost een hele verbeterronde (gezien op 2026-07-10, 2× 300s-timeout op rij).
_OPENMODEL_TIMEOUT = 600.0
_OPENMODEL_ATTEMPTS = 2


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def openmodel_claude_configured() -> bool:
    return bool(OPENMODEL_API_KEY)


def is_configured() -> bool:
    return anthropic_configured() or openmodel_claude_configured() or bool(OPENROUTER_API_KEY)


def active_route() -> str:
    if anthropic_configured():
        return f"anthropic/{CLAUDE_MODEL}"
    if openmodel_claude_configured():
        return f"openmodel/{OPENMODEL_SMART_MODEL}"
    if OPENROUTER_API_KEY:
        return f"openrouter/{CLAUDE_VIA_OPENROUTER}"
    return "geen"


async def _get_via_openmodel(
    messages: List[Dict], system_prompt: str, max_tokens: int,
) -> str:
    """Claude-model via de OpenModel-gateway (Anthropic Messages-formaat).

    Retourneert de volledige tekst. Eén interne retry: de gateway geeft
    sporadisch een lege respons of een transient 5xx — één verse poging is
    goedkoper dan de terugval naar een zwakker model."""
    payload = {
        "model": OPENMODEL_SMART_MODEL,
        "system": system_prompt,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENMODEL_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    last_error: Exception | None = None
    for attempt in range(1, _OPENMODEL_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_OPENMODEL_TIMEOUT) as client:
                resp = await client.post(
                    OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages",
                    json=payload, headers=headers,
                )
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Model '{OPENMODEL_SMART_MODEL}' niet gevonden op OpenModel — "
                        "controleer OPENMODEL_SMART_MODEL in .env."
                    )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                if usage:
                    from ...shared.outcomes import log_llm_usage
                    log_llm_usage(
                        backend="openmodel", model=OPENMODEL_SMART_MODEL,
                        route="claude-openmodel",
                        prompt_tokens=usage.get("input_tokens", 0),
                        completion_tokens=usage.get("output_tokens", 0),
                        total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                    )
                text = "".join(
                b.get("text", "") for b in data.get("content", []) if isinstance(b, dict)
            )
            if data.get("stop_reason") == "max_tokens":
                logger.warning(
                    "[claude/openmodel] Antwoord afgekapt op max_tokens (%d) — "
                    "de aanroeper krijgt mogelijk halve JSON; overweeg een ruimer budget.",
                    max_tokens,
                )
            if text.strip():
                return text
            logger.warning("[claude/openmodel] Lege respons (poging %d/%d)",
                           attempt, _OPENMODEL_ATTEMPTS)
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as e:
            if (isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403
                    and "quota" in e.response.text.lower()):
                # Dagquota van de gateway op: retryen is zinloos en de kale 403
                # is voor niemand leesbaar. De aanroepers escaleren dit al naar
                # het Actiecentrum.
                raise RuntimeError(
                    "OpenModel-dagquota op (403 quota exceeded) — wacht op de reset "
                    "of verhoog de quota op openmodel.ai"
                ) from e
            transient = not (isinstance(e, httpx.HTTPStatusError)
                             and e.response.status_code < 500
                             and e.response.status_code != 429)
            if not transient:
                raise
            last_error = e
            # str(e) van een httpx-timeout is leeg — noem ook de klasse.
            logger.warning("[claude/openmodel] Transiente fout (poging %d/%d): %s: %s",
                           attempt, _OPENMODEL_ATTEMPTS, e.__class__.__name__, e)
    if last_error:
        raise last_error
    raise RuntimeError("OpenModel gaf herhaaldelijk een lege respons")


async def _stream_via_openrouter(
    messages: List[Dict], system_prompt: str, max_tokens: int,
) -> AsyncGenerator[str, None]:
    """Claude via OpenRouter (SSE) — zelfde model-familie, andere route."""
    payload = {
        "model": CLAUDE_VIA_OPENROUTER,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


async def stream_response(
    messages: List[Dict],
    system_prompt: str = "You are a helpful AI assistant.",
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    yielded = False
    if anthropic_configured():
        try:
            client = get_client()
            async with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yielded = True
                    yield text
            return
        except anthropic.APIError as e:
            if yielded:
                raise  # midden in een stream niet meer stilletjes wisselen
            logger.warning(f"Anthropic direct faalde ({e.__class__.__name__}) — terugval op OpenModel/OpenRouter")

    if openmodel_claude_configured():
        try:
            # Gateway streamt niet betrouwbaar — volledige tekst als één chunk.
            yield await _get_via_openmodel(messages, system_prompt, max_tokens)
            return
        except Exception as e:
            logger.warning(f"Claude via OpenModel faalde ({e}) — terugval op OpenRouter")

    if OPENROUTER_API_KEY:
        async for text in _stream_via_openrouter(messages, system_prompt, max_tokens):
            yield text
        return

    raise RuntimeError(
        "Geen werkende Claude-backend: geen ANTHROPIC_API_KEY, OPENMODEL_API_KEY of OPENROUTER_API_KEY."
    )


async def get_response(
    messages: List[Dict],
    system_prompt: str = "You are a helpful AI assistant.",
    max_tokens: int = 4096,
) -> str:
    if anthropic_configured():
        try:
            client = get_client()
            response = await client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
            return response.content[0].text
        except anthropic.APIError as e:
            logger.warning(f"Anthropic direct faalde ({e.__class__.__name__}) — terugval op OpenModel/OpenRouter")

    if openmodel_claude_configured():
        try:
            return await _get_via_openmodel(messages, system_prompt, max_tokens)
        except Exception as e:
            logger.warning(f"Claude via OpenModel faalde ({e}) — terugval op OpenRouter")

    if OPENROUTER_API_KEY:
        parts: List[str] = []
        async for text in _stream_via_openrouter(messages, system_prompt, max_tokens):
            parts.append(text)
        return "".join(parts)

    raise RuntimeError(
        "Geen werkende Claude-backend: geen ANTHROPIC_API_KEY, OPENMODEL_API_KEY of OPENROUTER_API_KEY."
    )
