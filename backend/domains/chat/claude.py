"""
Anthropic Claude integratie met async streaming via SSE.

Terugvalpad: als de directe Anthropic-key ontbreekt of ongeldig is (401),
loopt hetzelfde Claude-model via OpenRouter (CLAUDE_VIA_OPENROUTER) — zodat
de Claude-agent blijft werken zolang één van beide keys geldig is.
"""
import json
import logging
from typing import AsyncGenerator, List, Dict

import anthropic
import httpx

from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, OPENROUTER_API_KEY,
    CLAUDE_VIA_OPENROUTER, anthropic_configured,
)

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def is_configured() -> bool:
    return anthropic_configured() or bool(OPENROUTER_API_KEY)


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
            logger.warning(f"Anthropic direct faalde ({e.__class__.__name__}) — terugval op Claude via OpenRouter")

    if OPENROUTER_API_KEY:
        async for text in _stream_via_openrouter(messages, system_prompt, max_tokens):
            yield text
        return

    raise RuntimeError(
        "Geen werkende Claude-backend: ANTHROPIC_API_KEY ontbreekt/ongeldig en geen OPENROUTER_API_KEY."
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
            logger.warning(f"Anthropic direct faalde ({e.__class__.__name__}) — terugval op Claude via OpenRouter")

    if OPENROUTER_API_KEY:
        parts: List[str] = []
        async for text in _stream_via_openrouter(messages, system_prompt, max_tokens):
            parts.append(text)
        return "".join(parts)

    raise RuntimeError(
        "Geen werkende Claude-backend: ANTHROPIC_API_KEY ontbreekt/ongeldig en geen OPENROUTER_API_KEY."
    )
