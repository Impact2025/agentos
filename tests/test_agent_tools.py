"""Regressietest: de agent kan tools aanroepen via de OpenModel-backend.

Vóór de fix was _openmodel_loop een pure streamer zónder tool_use-cyclus, dus
de 10 geregistreerde tools (web_search, obsidian_write, create_task, delegate…)
waren effectief dood. Deze test verzekert dat de agent een tool_use-block
teruggeeft, wij de tool uitvoeren, het resultaat terugvoeren en daarna de
eindtekst krijgen.
"""
from types import SimpleNamespace
from unittest import mock

from backend.shared import agent_runner


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_id, inp):
    return SimpleNamespace(type="tool_use", name=name, id=tool_id, input=inp)


def _make_client(message_sequence):
    """Fake Anthropic-client die een vaste reeks messages teruggeeft."""
    class FakeMessages:
        def __init__(self, *a, **k):
            self._i = 0
        async def create(self, **kwargs):
            m = message_sequence[self._i]
            self._i += 1
            if self._i > len(message_sequence):
                self._i = len(message_sequence)  # clamp
            return m
    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
    return FakeClient


def _fake_sdk_module(message_sequence):
    return SimpleNamespace(AsyncAnthropic=_make_client(message_sequence))


async def test_openmodel_loop_runs_tools(monkeypatch, capsys):
    captured = {}

    class FakeTool:
        name = "web_search"
        description = "search the web"
        input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}

        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output="[web] resultaat voor: " + kwargs.get("query", ""), error=False)

    # Patch de tool in de TOOL_MAP en het anthropic-sysmoduul.
    from backend.tools import TOOL_MAP
    monkeypatch.setitem(TOOL_MAP, "web_search", FakeTool())

    seq = [
        SimpleNamespace(
            content=[_text_block("Ik zoek even op."),
                     _tool_block("web_search", "tu_1", {"query": "agent os"})],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="tool_use"),
        SimpleNamespace(
            content=[_text_block("Antwoord op basis van de zoekresultaten.")],
            usage=SimpleNamespace(input_tokens=4, output_tokens=6),
            stop_reason="end_turn"),
    ]
    fake_sdk = _fake_sdk_module(seq)
    with mock.patch.dict("sys.modules", {"anthropic": fake_sdk}):
        events = [e async for e in agent_runner._openmodel_loop(
            messages=[{"role": "user", "content": "zoek iets op"}],
            system_prompt="", max_tokens=200, use_tools=True)]

    types = [e["type"] for e in events]
    assert "tool_start" in types, events
    assert "tool_result" in types, events
    assert any(e["type"] == "text" and "zoekresultaten" in e["text"] for e in events)
    assert captured.get("query") == "agent os", captured


async def test_openmodel_loop_without_tools_does_not_send_tools(monkeypatch):
    sent = {}

    class FakeMessages:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                content=[_text_block("Hoi, ik ben een simpele chatbot.")],
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
                stop_reason="end_turn")
    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()
    fake_sdk = SimpleNamespace(AsyncAnthropic=FakeClient)

    with mock.patch.dict("sys.modules", {"anthropic": fake_sdk}):
        events = [e async for e in agent_runner._openmodel_loop(
            messages=[{"role": "user", "content": "hallo"}], system_prompt="",
            max_tokens=50, use_tools=False)]

    assert "tools" not in sent, "tools mogen niet meegestuurd worden als use_tools=False"
    assert any(e["type"] == "text" for e in events)
