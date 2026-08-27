"""Tests for the ReAct loop using a scripted mock LLM (no API key needed)."""
from __future__ import annotations

import unittest

from src.core.models import AgentStatus, ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class MockLLMClient(LLMClient):
    """Scripted LLM: returns a fixed sequence of responses."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self._script:
            return LLMResponse(content="no more steps", tool_calls=None, finish_reason="stop")
        return self._script.pop(0)


class InfiniteToolLLM(LLMClient):
    """Always returns the same tool call, to exercise the step cap."""

    def chat(self, messages, tools=None):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="x", name="probe", arguments={}, arguments_json="{}")
            ],
            finish_reason="tool_calls",
        )


class RecordingTool:
    def __init__(self):
        self.executions = []

    def __call__(self, **kwargs):
        self.executions.append(kwargs)
        return "ok"


class ReactLoopTest(unittest.TestCase):
    def _make_loop(self, llm):
        self.tool = RecordingTool()
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="probe",
                description="probe tool",
                parameters={"type": "object", "properties": {}},
                func=self.tool,
            )
        )
        return ReactLoop(llm, ToolExecutor(registry), system_prompt="sys", max_steps=5)

    def test_loop_executes_tool_then_finishes(self):
        script = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="final answer", tool_calls=None, finish_reason="stop"),
        ]
        loop = self._make_loop(MockLLMClient(script))
        result = loop.run("task")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertIn("final answer", result.summary)
        self.assertEqual(self.tool.executions, [{}])
        self.assertEqual(result.artifacts["final_state"], "DONE")

    def test_loop_stops_at_max_steps(self):
        loop = self._make_loop(InfiniteToolLLM())
        loop._max_steps = 3
        result = loop.run("task")
        self.assertEqual(result.artifacts["final_state"], "MAX_STEPS")
        self.assertEqual(result.status, AgentStatus.FAILED)


class RecordingTracer:
    def __init__(self):
        self.events = []

    def on_step(self, step):
        self.events.append(("step", step))

    def on_tool_call(self, call):
        self.events.append(("tool_call", call.name))

    def on_tool_result(self, result):
        self.events.append(("tool_result", result.name, result.error))

    def on_state_transition(self, old, new):
        self.events.append(("state", old.value, new.value))

    def on_llm_error(self, attempt, error):
        self.events.append(("llm_error", attempt))


class TracerTest(unittest.TestCase):
    def test_loop_emits_trace_events(self):
        script = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", tool_calls=None, finish_reason="stop"),
        ]
        tracer = RecordingTracer()
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="probe",
                description="probe",
                parameters={"type": "object", "properties": {}},
                func=lambda **kwargs: "ok",
            )
        )
        loop = ReactLoop(
            MockLLMClient(script),
            ToolExecutor(registry),
            system_prompt="sys",
            max_steps=5,
            tracer=tracer,
        )
        result = loop.run("task")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        kinds = [e[0] for e in tracer.events]
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)
        self.assertIn("state", kinds)
        self.assertEqual(tracer.events[-1], ("state", "RUNNING", "DONE"))


if __name__ == "__main__":
    unittest.main()
