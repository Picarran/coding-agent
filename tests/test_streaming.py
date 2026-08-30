"""Tests for LLM streaming (V3-9a): chat_stream fallback, ReactLoop deltas, DeepSeek assembly."""
from __future__ import annotations

import types
import unittest

from src.core.events import EventBus, EventType
from src.core.models import Message, ToolCall
from src.llm.base import LLMClient, LLMResponse, StreamChunk
from src.llm.deepseek_client import DeepSeekClient
from src.loops.react_loop import ReactLoop
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class _RecordingConsumer:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class ChatOnlyLLM(LLMClient):
    """Implements only ``chat`` — the default ``chat_stream`` fallback must work."""

    def chat(self, messages, tools=None):
        return LLMResponse(
            content="all at once",
            tool_calls=None,
            finish_reason="stop",
            usage={"total_tokens": 3},
        )


class ScriptedStreamingLLM(LLMClient):
    """Yields content deltas then a terminal chunk, per scripted turn."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []

    def chat(self, messages, tools=None):
        raise AssertionError("streaming path must use chat_stream")

    def chat_stream(self, messages, tools=None):
        self.calls.append(list(messages))
        deltas, tool_calls = self._turns.pop(0)
        for d in deltas:
            yield StreamChunk(content=d)
        yield StreamChunk(
            content=None,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage={"total_tokens": 7},
        )


class FallbackTest(unittest.TestCase):
    def test_chat_stream_falls_back_to_chat(self):
        chunks = list(ChatOnlyLLM().chat_stream([Message(role="user", content="hi")]))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content, "all at once")
        self.assertEqual(chunks[0].usage["total_tokens"], 3)


class RecordingTool:
    def __init__(self):
        self.executions = []

    def __call__(self, **kwargs):
        self.executions.append(kwargs)
        return "ok"


def _make_loop(llm, tool_func, streaming, bus):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="probe",
            description="probe",
            parameters={"type": "object", "properties": {}},
            func=tool_func,
        )
    )
    executor = ToolExecutor(registry, event_bus=bus, agent_id="a")
    return ReactLoop(llm, executor, "sys", event_bus=bus, agent_id="a", max_steps=5, streaming=streaming)


class ReactLoopStreamingTest(unittest.TestCase):
    def test_streaming_emits_deltas_and_completes(self):
        tool = RecordingTool()
        llm = ScriptedStreamingLLM(
            [
                ([], [ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")]),
                (["final ", "answer"], None),
            ]
        )
        bus = EventBus()
        consumer = _RecordingConsumer()
        bus.subscribe(consumer)
        loop = _make_loop(llm, tool, streaming=True, bus=bus)
        result = loop.run("task")

        deltas = [
            e.payload["text"]
            for e in consumer.events
            if e.event_type == EventType.STREAM_DELTA
        ]
        self.assertEqual(deltas, ["final ", "answer"])
        self.assertEqual(result.status.value, "SUCCESS")
        self.assertIn("final answer", result.summary)
        self.assertEqual(tool.executions, [{}])


class DeepSeekStreamingTest(unittest.TestCase):
    def _chunk(self, content=None, tool_deltas=None, finish=None, usage=None):
        delta = types.SimpleNamespace(content=content, tool_calls=tool_deltas or [])
        has_choice = bool(content or tool_deltas or finish)
        choice = types.SimpleNamespace(delta=delta, finish_reason=finish) if has_choice else None
        return types.SimpleNamespace(choices=[choice] if choice else [], usage=usage)

    def _tc(self, index, id=None, name=None, args=None):
        fn = types.SimpleNamespace(name=name, arguments=args)
        return types.SimpleNamespace(index=index, id=id, function=fn)

    def test_chat_stream_assembles_tool_calls_and_usage(self):
        client = DeepSeekClient(api_key="x", base_url="http://localhost", model="m")
        stream = [
            self._chunk(content="Hel"),
            self._chunk(content="lo"),
            self._chunk(
                tool_deltas=[self._tc(0, id="c1", name="probe", args='{"x":')],
            ),
            self._chunk(tool_deltas=[self._tc(0, args="1}")], finish="tool_calls"),
            self._chunk(usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
        ]

        class FakeCompletions:
            def create(self, **kwargs):
                assert kwargs.get("stream") is True
                return stream

        class FakeChat:
            def __init__(self):
                self.completions = FakeCompletions()

        class FakeOpenAI:
            def __init__(self):
                self.chat = FakeChat()

        client._client = FakeOpenAI()  # noqa: SLF001 - inject fake transport
        chunks = list(client.chat_stream([Message(role="user", content="hi")]))

        deltas = [c.content for c in chunks if c.content]
        self.assertEqual(deltas, ["Hel", "lo"])
        final = chunks[-1]
        self.assertEqual(final.content, None)
        self.assertEqual(len(final.tool_calls), 1)
        self.assertEqual(final.tool_calls[0].name, "probe")
        self.assertEqual(final.tool_calls[0].arguments, {"x": 1})
        self.assertEqual(final.usage["total_tokens"], 15)


if __name__ == "__main__":
    unittest.main()
