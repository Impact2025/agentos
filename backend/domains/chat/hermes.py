"""
Hermes agent — ondersteunt drie backends, auto-detected op basis van .env:
  1. Lokaal  (127.0.0.1:8642)  — stel HERMES_LOCAL_URL + HERMES_LOCAL_KEY in
  2. Ollama  (lokaal, gratis)  — stel OLLAMA_BASE_URL in, bijv. http://localhost:11434/v1
  3. OpenRouter (cloud)        — stel OPENROUTER_API_KEY in
Geen automatische fallback naar Claude/Anthropic.
"""
import json
import httpx
import anthropic
from typing import AsyncGenerator, List, Dict
from ...shared.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL_2,
    OPENROUTER_API_KEY, HERMES_MODEL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    HERMES_LOCAL_URL, HERMES_LOCAL_KEY,
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
    hermes_backend,
)

import logging
logger = logging.getLogger(__name__)

_anthropic_client: anthropic.AsyncAnthropic | None = None


def is_configured() -> bool:
    return bool(HERMES_LOCAL_URL or OLLAMA_BASE_URL or OPENMODEL_API_KEY or OPENROUTER_API_KEY or ANTHROPIC_API_KEY)


def active_model() -> str:
    backend = hermes_backend()
    if backend == "local":
        return "local/hermes-agent"
    if backend == "ollama":
        return f"ollama/{OLLAMA_MODEL}"
    if backend == "openmodel":
        return f"openmodel/{OPENMODEL_MODEL}"
    if backend == "openrouter":
        return HERMES_MODEL
    return CLAUDE_MODEL_2


def _fallback_backends(primary: str) -> List[str]:
    """Cloud-backends om op terug te vallen als de primaire backend faalt
    (bv. billing-fout of tijdelijke storing) — vóórdat er al tekst is gestreamd.

    Anthropic-direct alleen met een échte key (anthropic_configured): een
    placeholder-key is truthy en gaf hier 401's tegen het echte Anthropic-API."""
    from ...shared.config import anthropic_configured
    chain = [primary]
    if primary == "openmodel":
        if OPENROUTER_API_KEY:
            chain.append("openrouter")
        elif anthropic_configured():
            chain.append("anthropic")
    elif primary == "openrouter" and anthropic_configured():
        chain.append("anthropic")
    return chain


async def _stream_for_backend(
    backend: str, messages: List[Dict], system_prompt: str, max_tokens: int,
) -> AsyncGenerator[str, None]:
    if backend == "anthropic":
        async for chunk in _stream_anthropic(messages, system_prompt, max_tokens):
            yield chunk
    elif backend == "openmodel":
        # OpenModel.ai spreekt het Anthropic Messages-formaat, maar de officiële
        # anthropic-SDK parseert hun SSE/tekst-blokken niet betrouwbaar (lege
        # output). Routeer daarom via de eigen OpenAI-compat helper die het
        # volledige message-object zelf parset (content[].text).
        async for chunk in _stream_openai_compat(messages, system_prompt, max_tokens, "openmodel"):
            yield chunk
    else:
        async for chunk in _stream_openai_compat(messages, system_prompt, max_tokens, backend):
            yield chunk


async def stream_response(
    messages: List[Dict],
    system_prompt: str = "You are Hermes, a helpful AI assistant.",
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    backend = hermes_backend()
    chain = _fallback_backends(backend)
    last_error: Exception | None = None

    for be in chain:
        started = False
        try:
            async for chunk in _stream_for_backend(be, messages, system_prompt, max_tokens):
                started = True
                yield chunk
            return
        except Exception as e:
            last_error = e
            if started:
                # Er is al tekst gestreamd — halverwege overschakelen zou een
                # kapot/dubbel antwoord opleveren, dus de fout gewoon doorgeven.
                raise
            logger.warning(f"Hermes-backend '{be}' faalde vóór eerste chunk ({e}); volgende in keten proberen.")

    if last_error:
        raise last_error


async def _stream_anthropic(
    messages: List[Dict], system_prompt: str, max_tokens: int,
    base_url: str | None = None, api_key: str | None = None, model: str | None = None,
) -> AsyncGenerator[str, None]:
    use_key = api_key or ANTHROPIC_API_KEY
    use_model = model or CLAUDE_MODEL_2
    client_kwargs = {"api_key": use_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.AsyncAnthropic(**client_kwargs)
    async with client.messages.stream(
        model=use_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_openai_compat(
    messages: List[Dict], system_prompt: str, max_tokens: int, backend: str
) -> AsyncGenerator[str, None]:
    if backend == "local":
        base_url = HERMES_LOCAL_URL.rstrip("/")
        model = "hermes"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {HERMES_LOCAL_KEY}",
        }
    elif backend == "ollama":
        base_url = OLLAMA_BASE_URL.rstrip("/")
        model = OLLAMA_MODEL
        headers = {"Content-Type": "application/json"}
    elif backend == "openmodel":
        # OpenModel.ai spreekt het Anthropic Messages-formaat (geen OpenAI-compat).
        # Correcte route is /v1/messages (OpenAI-compat /v1/chat/completions geeft 404).
        base_url = OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages"
        model = OPENMODEL_MODEL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENMODEL_API_KEY}",
            "anthropic-version": "2023-06-01",
        }
    else:
        base_url = "https://openrouter.ai/api/v1"
        model = HERMES_MODEL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Agent OS",
        }

    if backend == "openmodel":
        # Anthropic-formaat payload + non-streaming parse (OpenModel streamt niet
        # als SSE op deze route — antwoord is een volledig message-object).
        payload = {
            "model": model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(base_url, json=payload, headers=headers)
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Model '{model}' niet gevonden (404) op OpenModel. "
                        "Controleer OPENMODEL_MODEL in .env."
                    )
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                if usage:
                    from ...shared.outcomes import log_llm_usage
                    log_llm_usage(
                        backend="openmodel", model=model, route="hermes-openmodel",
                        prompt_tokens=usage.get("input_tokens", 0),
                        completion_tokens=usage.get("output_tokens", 0),
                        total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                    )
                # Anthropic-message: content is een lijst van blokken met .text
                blocks = data.get("content", [])
                text = "".join(
                    b.get("text", "") for b in blocks if isinstance(b, dict)
                )
                if text:
                    yield text
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise RuntimeError(
                        f"Model '{model}' niet gevonden (404) op OpenModel."
                    )
                raise
        return

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            async with client.stream(
                "POST", f"{base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code == 404:
                    raise RuntimeError(
                        f"Model '{model}' niet gevonden (404) op backend '{backend}'. "
                        "Controleer OPENMODEL_MODEL of HERMES_MODEL in .env."
                    )
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        text = chunk["choices"][0].get("delta", {}).get("content", "")
                        if text:
                            yield text
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise RuntimeError(
                    f"Model '{model}' niet gevonden (404) op backend '{backend}'. "
                    "Controleer OPENMODEL_MODEL of HERMES_MODEL in .env."
                ) from e
            raise
