"""
Agentic loop — orkestreert multi-step tool use via Hermes (lokaal / OpenRouter / Ollama).

Emits dicts via async generator:
  {"type": "tool_start",  "name": "...", "input": {...}}
  {"type": "tool_result", "name": "...", "output": "...", "error": false}
  {"type": "text",        "text": "..."}
"""
import json
import asyncio
import httpx
from typing import AsyncGenerator, List, Dict, Optional, Tuple

from .config import (
    OPENROUTER_API_KEY, HERMES_MODEL, HERMES_FALLBACK_MODELS,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    HERMES_LOCAL_URL, HERMES_LOCAL_KEY,
    OPENMODEL_API_KEY, OPENMODEL_BASE_URL, OPENMODEL_MODEL,
    hermes_backend, HERMES_LOCAL_FALLBACK,
)
from ..tools import TOOLS, TOOL_MAP

MAX_ITERATIONS = 8


class _BackendUnavailable(RuntimeError):
    """Backend is niet bereikbaar (connection error of timeout)."""


def _cloud_backend_for_model(model_override: Optional[str]) -> Optional[str]:
    """Bepaal welke cloud-backend bij een model_override hoort (als die cloud
    beschikbaar is). Zo kan een 'pro'-profiel (claude-sonnet-4-6, deepseek-v4-flash
    via OpenModel) worden uitgevoerd op de cloud zelfs als de standaard-backend
    lokaal/Ollama is. Retourneert None als er geen cloud-model of geen sleutel is."""
    if not model_override:
        return None
    model = model_override.strip()
    if model.startswith("openrouter/"):
        return "openrouter" if OPENROUTER_API_KEY else None
    # Bare OpenModel-modelnaam (claude-*, deepseek-*, gpt-*) → OpenModel-gateway.
    return "openmodel" if OPENMODEL_API_KEY else None


def _fallback_chain(backend: str, model: str) -> List[str]:
    """Modelketen voor automatische 429-fallback.

    Alleen de OpenRouter-backend heeft fallbacks (gratis modellen worden daar
    rate-limited). Lokaal/Ollama hebben één vast model van de gateway.
    """
    if backend != "openrouter":
        return [model]
    chain = [model]
    for fb in HERMES_FALLBACK_MODELS:
        if fb and fb not in chain:
            chain.append(fb)
    return chain


def _is_rate_limited(status_code: int, body: Optional[dict] = None) -> bool:
    """429 komt soms direct, soms ingepakt in een 200-body (OpenRouter proxy)."""
    if status_code == 429:
        return True
    if status_code == 200 and isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and err.get("code") == 429:
            return True
    return False


def _cloud_fallback_chain(skip: str) -> List[str]:
    """Geeft beschikbare cloud-backends terug, de primaire overgeslagen."""
    chain: List[str] = []
    if skip != "openmodel" and OPENMODEL_API_KEY:
        chain.append("openmodel")
    if skip != "openrouter" and OPENROUTER_API_KEY:
        chain.append("openrouter")
    return chain


async def _run_cloud_backend(
    backend: str,
    messages: List[Dict],
    system_prompt: str,
    max_tokens: int,
    model_override: Optional[str],
) -> tuple:
    """Voer één cloud-backend uit en buffer de events.
    Geeft (events, had_error, error_msg) terug."""
    events: List[Dict] = []
    error_msg = ""
    try:
        if backend == "openmodel":
            gen = _openmodel_loop(messages, system_prompt, max_tokens, model_override)
        else:
            gen = _openai_loop(messages, system_prompt, max_tokens, backend,
                               model_override=model_override, use_tools=False)
        async for event in gen:
            events.append(event)
            if event.get("type") == "error":
                error_msg = event.get("message", "onbekende fout")
                return events, True, error_msg
    except _BackendUnavailable as exc:
        return [], True, str(exc)
    return events, False, ""


async def run_agent(
    messages: List[Dict],
    system_prompt: str,
    agent: str = "hermes",
    max_tokens: int = 4096,
    model_override: str = None,
    use_tools: bool = True,
    purpose: str = "",
    backend_override: Optional[str] = None,
) -> AsyncGenerator[Dict, None]:
    # Een expliciete backend_override (bv. "openmodel") forceert die cloud-route
    # ook als de standaard-backend lokaal/Ollama is. Zo kan een agent-profiel met
    # een premium cloud-model (claude-sonnet-4-6 via OpenModel) wél gehonoreerd
    # worden terwijl de rest van de app op gratis lokale Ollama draait.
    if backend_override == "openmodel":
        async for event in _openmodel_loop(messages, system_prompt, max_tokens,
                                           model_override, use_tools=use_tools,
                                           purpose=purpose):
            yield event
        return
    if backend_override == "openrouter":
        try:
            async for event in _openai_loop(messages, system_prompt, max_tokens,
                                            "openrouter", model_override=model_override,
                                            use_tools=use_tools):
                yield event
            return
        except _BackendUnavailable as exc:
            yield {"type": "error", "message": f"backend_override openrouter faalde: {exc}"}
            return

    # Auto-route: als een model_override een cloud-model noemt (en die sleutel
    # is aanwezig), stuur die call naar de juiste cloud-backend — óók wanneer de
    # app breed op lokale Ollama draait. Dit is wat 'pro'-agentprofielen doet
    # werken zonder dat elke aanroeper zijn backend expliciet zet.
    auto_backend = _cloud_backend_for_model(model_override)
    if auto_backend == "openmodel":
        async for event in _openmodel_loop(messages, system_prompt, max_tokens,
                                           model_override, use_tools=use_tools,
                                           purpose=purpose):
            yield event
        return
    if auto_backend == "openrouter":
        try:
            async for event in _openai_loop(messages, system_prompt, max_tokens,
                                            "openrouter", model_override=model_override,
                                            use_tools=use_tools):
                yield event
            return
        except _BackendUnavailable as exc:
            yield {"type": "error", "message": f"auto-route openrouter faalde: {exc}"}
            return

    backend = hermes_backend()
    if not backend:
        yield {"type": "error", "message": "Geen backend geconfigureerd. Controleer HERMES_LOCAL_URL of OPENROUTER_API_KEY in .env"}
        return

    # Primaire backend — stream direct door (geen buffer)
    primary_error: Optional[str] = None
    if backend == "openmodel":
        async for event in _openmodel_loop(messages, system_prompt, max_tokens,
                                           model_override, use_tools=use_tools,
                                           purpose=purpose):
            yield event
        return
    else:
        try:
            async for event in _openai_loop(messages, system_prompt, max_tokens, backend,
                                            model_override=model_override, use_tools=use_tools):
                yield event
            return
        except _BackendUnavailable as exc:
            primary_error = str(exc)

    # Primaire backend mislukt — doorloop de cloud-fallback keten
    for fb in _cloud_fallback_chain(skip=backend):
        yield {"type": "fallback", "model": fb, "reason": primary_error or "backend niet beschikbaar"}
        events, had_error, err = await _run_cloud_backend(fb, messages, system_prompt, max_tokens, model_override)
        if not had_error:
            for event in events:
                yield event
            return
        primary_error = err  # voor volgende iteratie

    # Alle backends opgebruikt/exhaust — als lokale fallback aanstaat, lever
    # een deterministische concept-vuller zodat de pijplijn niet stilvalt. De
    # output is expliciet gemarkeerd als CONCEPT en scoort altijd <80 bij de
    # SEO-gate, dus hij belandt in 'needs_work' en wordt NOOIT gepubliceerd.
    if HERMES_LOCAL_FALLBACK:
        async for event in _local_template_fill(messages, system_prompt):
            yield event
        return

    yield {"type": "error", "message": primary_error or "Alle backends onbereikbaar"}


async def _local_template_fill(messages: List[Dict], system_prompt: str) -> AsyncGenerator[Dict, None]:
    """Deterministische offline vuller: zet de gebruikersopdracht om in een
    gestructureerd concept-karkas. Geen LLM — puur patroon/titel-extractie.
    Altijd duidelijk gemarkeerd als CONCEPT zodat een mens het ziet en de
    kwaliteitsgate (score >=80) het weigert voor publicatie."""
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content") or ""
            break
    # Haal een werktitel uit de opdracht (eerste niet-lege regel na '# ').
    title = ""
    for line in user_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        # Pak de eerste zin die eruit ziet als een titel.
        for line in user_text.splitlines():
            line = line.strip()
            if line and len(line) > 8 and not line.lower().startswith(("titel:", "beschrijving:")):
                title = line.split("\n")[0][:80]
                break
    title = title or "Concept (lokale fallback — geen LLM beschikbaar)"

    out = [
        f"# {title}",
        "",
        "> ⚠️ CONCEPT gegenereerd door lokale fallback — geen LLM-backend beschikbaar.",
        "> Controleer en schrijf dit uit voordat je het publiceert.",
        "",
        "## Inleiding",
        f"[Automatisch karkas voor: {title}]",
        "",
        "## Hoofdpunten",
        "1. [punt 1 — invullen]",
        "2. [punt 2 — invullen]",
        "3. [punt 3 — invullen]",
        "",
        "## Afsluiting",
        "[samenvatting — invullen]",
        "",
        "## Veelgestelde vragen",
        "**Vraag 1?** [antwoord invullen]",
        "**Vraag 2?** [antwoord invullen]",
        "",
        "---",
        f"_Meta-titel: {title} · _Meta-beschrijving: [invullen, ~155 tekens]_",
    ]
    yield {"type": "text", "text": "\n".join(out)}
    yield {"type": "note", "text": "lokale-fallback-concept (geen LLM)"}



# ── OpenModel / Anthropic-compatible loop ─────────────────────────────────────

async def _openmodel_loop(
    messages: List[Dict],
    system_prompt: str,
    max_tokens: int,
    model_override: str = None,
    use_tools: bool = True,
    purpose: str = "",
) -> AsyncGenerator[Dict, None]:
    """Agent-loop voor OpenModel.ai (Anthropic-compatible /v1/messages).

    Ondersteunt dezelfde tool-use-cyclus als _openai_loop: de agent mag
    tool_use-blocks teruggeven, wij voeren ze uit en voeren de resultaten terug
    tot MAX_ITERATIONS. Streaming laten we hier achterwege (tool_use vereist de
    volledige message); de tekst wordt in één keer doorgegeven als 'text'-events
    zodat de frontend er niets van merkt. Token-verbruik wordt gelogd in llm_usage.
    """
    import anthropic as _sdk
    from ..tools import TOOLS, TOOL_MAP
    model = model_override or OPENMODEL_MODEL
    client = _sdk.AsyncAnthropic(
        api_key=OPENMODEL_API_KEY,
        base_url=OPENMODEL_BASE_URL,
    )
    openai_tools = [t.to_anthropic() for t in TOOLS] if use_tools else []
    full_messages = list(messages)

    try:
        for _ in range(MAX_ITERATIONS):
            kwargs = dict(model=model, max_tokens=max_tokens, system=system_prompt,
                          messages=full_messages)
            if openai_tools:
                kwargs["tools"] = openai_tools
            try:
                msg = await client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                # Een harde 403 "quota exceeded" van de OpenModel-gateway is geen
                # bug: de provider-dagquota is op. Zet de zelf-uitlijnende rem
                # (note_llm_quota_exhausted) — precies zoals in chat/hermes/mail —
                # zodat autonome jobs én de backend-keuze zichzelf pauzeren in
                # plaats van elke 15/45 min opnieuw op een dode quota te bonken.
                # Zo verdwijnt de rode FOUT-kaart en pauzeert Agent OS netjes tot
                # de reset, in plaats van eindeloos te falen.
                status = getattr(exc, "status_code", None)
                resp = getattr(exc, "response", None)
                body = getattr(resp, "text", "") or "" if resp is not None else ""
                if status == 403 and "quota" in str(body).lower():
                    from .outcomes import note_llm_quota_exhausted
                    note_llm_quota_exhausted(
                        backend="openmodel", model=model,
                        route=purpose or "agent-openmodel",
                    )
                    yield {"type": "error", "message":
                        "OpenModel-dagquota op (403 quota exceeded) — wacht op de "
                        "reset of verhoog de quota op openmodel.ai"}
                    return
                yield {"type": "error", "message": f"OpenModel fout: {exc}"}
                return

            # usage logging
            u = getattr(msg, "usage", None)
            if u:
                pt, ct = getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0)
                yield {"type": "usage", "model": model,
                       "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
                from .outcomes import log_llm_usage
                log_llm_usage(backend="openmodel", model=model, route=purpose or "agent-openmodel",
                              prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct)

            # Tekst-blokken streamen als text-events
            text_parts = [b.text for b in msg.content if b.type == "text"]
            if text_parts:
                yield {"type": "text", "text": "".join(text_parts)}

            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if not tool_uses:
                break  # klaar, geen verdere tool-aanroep

            # Tool-aanroepen uitvoeren en resultaten teruggeven
            full_messages.append({"role": "assistant", "content": msg.content})
            for tu in tool_uses:
                yield {"type": "tool_start", "name": tu.name, "input": tu.input}
                tool = TOOL_MAP.get(tu.name)
                if tool:
                    try:
                        result = await tool.run(**(tu.input or {}))
                        output, is_error = result.output, result.error
                    except Exception as exc:  # noqa: BLE001
                        output, is_error = f"Tool '{tu.name}' faalde: {exc}", True
                else:
                    output, is_error = f"Tool '{tu.name}' niet gevonden", True
                yield {"type": "tool_result", "name": tu.name, "output": output, "error": is_error}
                full_messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": output}],
                })
    except Exception as exc:
        yield {"type": "error", "message": f"OpenModel fout: {exc}"}




def _openai_headers_and_url(backend: str):
    if backend == "local":
        return (
            f"{HERMES_LOCAL_URL.rstrip('/')}/chat/completions",
            {
                "Content-Type": "application/json",
                # De local-tier is Ollama/LM Studio en heeft geen echte key.
                # Zonder terugval bouwt een lege HERMES_LOCAL_KEY de header
                # `Bearer ` (trailing space) — h11 weigert dat als "Illegal
                # header value b'Bearer '" en de héle run crasht cryptisch
                # (incident 2026-07-15, goal-taken faalden 4× op rij). Zelfde
                # 'ollama'-terugval als chat/hermes.py houdt de header geldig.
                "Authorization": f"Bearer {HERMES_LOCAL_KEY or 'ollama'}",
            },
            # De local-tier is in de praktijk Ollama/LM Studio: die 404't op een
            # niet-bestaand model. Zelfde fix als chat/hermes.py — gebruik het
            # geconfigureerde lokale model i.p.v. de hardcoded 'hermes-agent'.
            OLLAMA_MODEL or "hermes-agent",
        )
    if backend == "ollama":
        return (
            f"{OLLAMA_BASE_URL.rstrip('/')}/chat/completions",
            {"Content-Type": "application/json"},
            OLLAMA_MODEL,
        )
    return (
        "https://openrouter.ai/api/v1/chat/completions",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:1250",
            "X-Title": "Agent OS",
        },
        HERMES_MODEL,
    )


async def _openai_loop(
    messages: List[Dict],
    system_prompt: str,
    max_tokens: int,
    backend: str,
    model_override: str = None,
    use_tools: bool = True,
) -> AsyncGenerator[Dict, None]:
    # Token optimalisatie: strip ruis en truncate voor lange context
    from .token_optimizer import optimize_prompt_messages, truncate_to_token_budget
    if system_prompt:
        system_prompt = truncate_to_token_budget(system_prompt, 3000)
    optimized_messages = optimize_prompt_messages(messages)

    url, headers, model = _openai_headers_and_url(backend)
    if model_override:
        model = model_override
    chain = _fallback_chain(backend, model)
    active_model = chain[0]
    openai_tools = [t.to_openai() for t in TOOLS] if use_tools else []
    full_messages = [{"role": "system", "content": system_prompt}] + list(optimized_messages)

    for _ in range(MAX_ITERATIONS):
        payload = {
            "messages": full_messages,
            "max_tokens": max_tokens,
        }
        # Tools alleen meesturen als ze gebruikt mogen worden; zwakke modellen
        # lekken anders tool-call-syntax in pure contenttaken.
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        try:
            data, used_model = await _post_with_fallback(
                url, headers, payload, chain, active_model
            )
        except _AllRateLimited as exc:
            yield {"type": "error", "message": str(exc)}
            return
        if used_model != active_model:
            active_model = used_model
            yield {"type": "fallback", "model": used_model, "reason": "429 rate-limit"}
        model = active_model

        usage = data.get("usage") or {}
        if usage:
            yield {"type": "usage", "model": model, **{
                k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            }}

        # Sommige backends/proxies geven bij een fout een 200-body zónder 'choices'
        # (bijv. {"error": {...}}). Niet hard crashen — meld het netjes als error-event.
        choices = data.get("choices")
        if not choices:
            err = data.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or "geen 'choices' in API-respons"
            yield {"type": "error", "message": f"Modelfout: {detail}"}
            return
        choice = choices[0]
        msg = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Laatste stap — stream de tekst
            text = msg.get("content") or ""
            if text:
                async for event in _stream_openai_text(url, headers, chain, active_model, full_messages, max_tokens):
                    yield event
            break

        # Tekst die de agent schrijft vlak vóór een tool-aanroep is zijn 'Thought'
        # (denkproces) — apart gemarkeerd zodat Mission Control de logica kan mappen.
        if msg.get("content"):
            yield {"type": "thought", "text": msg["content"]}

        full_messages.append(msg)

        # Tools uitvoeren
        for tc in tool_calls:
            fn = tc["function"]
            tool_name = fn["name"]
            try:
                tool_input = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_input = {}

            yield {"type": "tool_start", "name": tool_name, "input": tool_input}

            tool = TOOL_MAP.get(tool_name)
            if tool:
                try:
                    result = await tool.run(**tool_input)
                    output = result.output
                    is_error = result.error
                except Exception as exc:  # noqa: BLE001 — een tool-crash mag de run niet slopen
                    output = f"Tool '{tool_name}' faalde: {exc}"
                    is_error = True
            else:
                output = f"Tool '{tool_name}' niet gevonden"
                is_error = True

            yield {"type": "tool_result", "name": tool_name, "output": output, "error": is_error}
            full_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})


class _AllRateLimited(RuntimeError):
    """Geen enkel model in de keten was beschikbaar (allemaal 429)."""


def _order_models(chain: List[str], active: str) -> List[str]:
    """Begin bij het laatst werkende model, dan de rest van de keten."""
    return [active] + [m for m in chain if m != active]


async def _post_with_fallback(
    url: str, headers: Dict, payload: Dict, chain: List[str], active: str
) -> Tuple[Dict, str]:
    """POST naar de chat-completions endpoint; schakel bij 429 door naar het
    volgende model in de keten. Retourneert (responsebody, gebruikt-model)."""
    order = _order_models(chain, active)
    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in order:
            try:
                resp = await client.post(url, json={**payload, "model": model}, headers=headers)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                err = str(exc) or type(exc).__name__
                raise _BackendUnavailable(f"{type(exc).__name__}: {err}") from exc
            body: Optional[dict] = None
            if resp.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = resp.json()
                except json.JSONDecodeError:
                    body = None
            if _is_rate_limited(resp.status_code, body):
                continue
            resp.raise_for_status()
            return (body if body is not None else resp.json()), model
    raise _AllRateLimited(
        "Alle modellen rate-limited (429): " + ", ".join(order)
    )


async def _stream_openai_text(
    url: str, headers: Dict, chain: List[str], active: str,
    messages: List[Dict], max_tokens: int,
) -> AsyncGenerator[Dict, None]:
    order = _order_models(chain, active)
    base_payload = {
        "messages": messages, "max_tokens": max_tokens, "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        for idx, model in enumerate(order):
            try:
                async with client.stream(
                    "POST", url, json={**base_payload, "model": model}, headers=headers
                ) as resp:
                    if resp.status_code == 429:
                        continue  # rate-limited — probeer het volgende model
                    resp.raise_for_status()
                    if idx > 0:
                        yield {"type": "fallback", "model": model, "reason": "429 rate-limit"}
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            # Usage komt (bij include_usage) in een laatste chunk zonder choices.
                            usage = chunk.get("usage")
                            if usage:
                                yield {"type": "usage", "model": model, **{
                                    k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                                }}
                            choices = chunk.get("choices") or []
                            if choices:
                                text = choices[0].get("delta", {}).get("content") or ""
                                if text:
                                    yield {"type": "text", "text": text}
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    return  # stream voltooid
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                err = str(exc) or type(exc).__name__
                raise _BackendUnavailable(f"{type(exc).__name__}: {err}") from exc
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    continue
                raise
    yield {"type": "error", "message": "Alle modellen rate-limited (429): " + ", ".join(order)}
