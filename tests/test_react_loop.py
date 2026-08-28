"""Tests for the ReAct loop using scripted mock LLMs (no API key needed)."""
from __future__ import annotations

import unittest

from src.core.events import EventBus, EventType
from src.core.models import AgentStatus, ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class MockLLMClient(LLMClient):
    """Returns a fixed script of responses, then a plain stop."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self._script:
            return LLMResponse(content="no more steps", tool_calls=None, finish_reason="stop")
        return self._script.pop(0)


class InfiniteToolLLM(LLMClient):
    """Always returns the same tool call, to exercise step/repetition guards."""

    def chat(self, messages, tools=None):
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="x", name="probe", arguments={}, arguments_json="{}")],
            finish_reason="tool_calls",
        )


class RecordingTool:
    def __init__(self):
        self.executions = []

    def __call__(self, **kwargs):
        self.executions.append(kwargs)
        return "ok"


def _failing_tool(**kwargs):
    raise RuntimeError("boom")


def _make_loop(llm, tool_func, **react_kwargs):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="probe",
            description="probe",
            parameters={"type": "object", "properties": {}},
            func=tool_func,
        )
    )
    event_bus = react_kwargs.pop("event_bus", None)
    agent_id = react_kwargs.pop("agent_id", None)
    defaults = {"system_prompt": "sys", "max_steps": 5}
    defaults.update(react_kwargs)
    executor = ToolExecutor(registry, event_bus=event_bus, agent_id=agent_id)
    return ReactLoop(llm, executor, event_bus=event_bus, agent_id=agent_id, **defaults)


class ReactLoopTest(unittest.TestCase):
    def test_loop_executes_tool_then_finishes(self):
        tool = RecordingTool()
        script = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="final answer", tool_calls=None, finish_reason="stop"),
        ]
        loop = _make_loop(MockLLMClient(script), tool)
        result = loop.run("task")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertIn("final answer", result.summary)
        self.assertEqual(tool.executions, [{}])
        self.assertEqual(result.artifacts["final_state"], "DONE")
        self.assertEqual(result.artifacts["stop_reason"], "done")

    def test_loop_stops_at_max_steps(self):
        loop = _make_loop(InfiniteToolLLM(), RecordingTool(), max_steps=3)
        result = loop.run("task")
        self.assertEqual(result.artifacts["final_state"], "MAX_STEPS")
        self.assertEqual(result.artifacts["stop_reason"], "max_steps")
        self.assertEqual(result.status, AgentStatus.FAILED)

    def test_loop_terminates_on_repeated_action(self):
        loop = _make_loop(
            InfiniteToolLLM(),
            RecordingTool(),
            max_steps=100,
            repeated_action_warn=3,
            repeated_action_limit=4,
        )
        result = loop.run("task")
        self.assertEqual(result.artifacts["stop_reason"], "repeated_action")
        self.assertEqual(result.artifacts["final_state"], "MAX_STEPS")

    def test_loop_terminates_on_consecutive_errors(self):
        loop = _make_loop(
            InfiniteToolLLM(),
            _failing_tool,
            max_steps=100,
            repeated_action_limit=1000,
            consecutive_error_warn=3,
            consecutive_error_limit=4,
        )
        result = loop.run("task")
        self.assertEqual(result.artifacts["stop_reason"], "consecutive_tool_errors")
        self.assertEqual(result.artifacts["final_state"], "FAILED")


class RecordingConsumer:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class EventBusTest(unittest.TestCase):
    def test_loop_emits_trace_events(self):
        script = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", tool_calls=None, finish_reason="stop"),
        ]
        bus = EventBus()
        consumer = RecordingConsumer()
        bus.subscribe(consumer)
        loop = _make_loop(MockLLMClient(script), RecordingTool(), event_bus=bus, agent_id="test_agent")
        result = loop.run("task")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        kinds = [e.event_type for e in consumer.events]
        self.assertIn(EventType.LOOP_STEP, kinds)
        self.assertIn(EventType.PRE_TOOL_USE, kinds)
        self.assertIn(EventType.POST_TOOL_USE, kinds)
        self.assertIn(EventType.AGENT_FINISH, kinds)
        finish = consumer.events[-1]
        self.assertEqual(finish.event_type, EventType.AGENT_FINISH)
        self.assertEqual(finish.agent_id, "test_agent")
        self.assertEqual(finish.status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
