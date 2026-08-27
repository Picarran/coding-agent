"""Tests for the CLI dispatch: interactive mode and one-shot run (no API key)."""
from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.main import interactive, run_once
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


class _DoneLLM(LLMClient):
    def __init__(self):
        self.tasks = []

    def chat(self, messages, tools=None):
        user_msgs = [m for m in messages if m.role == "user"]
        if user_msgs:
            self.tasks.append(user_msgs[-1].content)
        return LLMResponse(content="ok", tool_calls=None, finish_reason="stop")


class MainDispatchTest(unittest.TestCase):
    def _loop(self):
        self.llm = _DoneLLM()
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="probe",
                description="p",
                parameters={"type": "object", "properties": {}},
                func=lambda **kwargs: "ok",
            )
        )
        return ReactLoop(self.llm, ToolExecutor(registry), system_prompt="sys", max_steps=5)

    def test_interactive_runs_tasks_and_exits(self):
        loop = self._loop()
        with patch("builtins.input", side_effect=["fix the bug", "exit"]), patch(
            "sys.stdout", new=io.StringIO()
        ):
            code = interactive(loop)
        self.assertEqual(code, 0)
        self.assertEqual(self.llm.tasks, ["fix the bug"])

    def test_run_once_returns_zero_on_success(self):
        loop = self._loop()
        with patch("sys.stdout", new=io.StringIO()):
            code = run_once(loop, "do work")
        self.assertEqual(code, 0)
        self.assertEqual(self.llm.tasks, ["do work"])


if __name__ == "__main__":
    unittest.main()
