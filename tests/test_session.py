"""Tests for multi-turn session memory (shared context across turns)."""
from __future__ import annotations

import unittest

from src.context.session import Session
from src.core.models import AgentStatus, ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class MockLLMClient(LLMClient):
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self._script:
            return LLMResponse(content="no more steps", tool_calls=None, finish_reason="stop")
        return self._script.pop(0)


def _make_loop(llm):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="probe",
            description="p",
            parameters={"type": "object", "properties": {}},
            func=lambda **kwargs: "ok",
        )
    )
    return ReactLoop(llm, ToolExecutor(registry), system_prompt="sys", max_steps=10)


class SessionTest(unittest.TestCase):
    def test_session_accumulates_context_across_turns(self):
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="probe", arguments={}, arguments_json="{}")],
                    finish_reason="tool_calls",
                ),
                LLMResponse(content="answer one", tool_calls=None, finish_reason="stop"),
                LLMResponse(content="answer two", tool_calls=None, finish_reason="stop"),
            ]
        )
        session = Session(_make_loop(llm))

        r1 = session.send("task one")
        r2 = session.send("task two")

        self.assertEqual(r1.status, AgentStatus.SUCCESS)
        self.assertEqual(r1.summary, "answer one")
        self.assertEqual(r2.summary, "answer two")

        # The second turn's LLM call saw the first turn's task and answer.
        last_call = [m.content for m in llm.calls[-1]]
        self.assertIn("task one", last_call)
        self.assertIn("answer one", last_call)
        self.assertIn("task two", last_call)

        # The persistent context keeps the system prompt first.
        self.assertEqual(session.context.messages[0].role, "system")


if __name__ == "__main__":
    unittest.main()
