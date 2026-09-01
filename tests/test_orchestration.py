"""Tests for orchestration modes (V2-5.4): fast / auto / thorough topologies."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.main_agent import MainAgent
from src.core.models import AgentStatus, ToolCall
from src.llm.base import LLMResponse
from src.main import build_agent
from src.task_router import TaskRouter


class _FakeLLM:
    def chat(self, messages, tools=None):
        raise AssertionError("LLM must not be called during agent construction")


class OrchestrationTest(unittest.TestCase):
    def test_fast_builds_single_agent(self):
        with tempfile.TemporaryDirectory() as d:
            agent = build_agent(
                Path(d), _FakeLLM(), 20, orchestration="fast", permission_mode="autonomous"
            )
            self.assertIsInstance(agent, BaseAgent)
            self.assertNotIsInstance(agent, MainAgent)

    def test_thorough_builds_main_agent(self):
        with tempfile.TemporaryDirectory() as d:
            agent = build_agent(
                Path(d), _FakeLLM(), 20, orchestration="thorough", permission_mode="autonomous"
            )
            self.assertIsInstance(agent, MainAgent)

    def test_auto_builds_task_router(self):
        with tempfile.TemporaryDirectory() as d:
            agent = build_agent(
                Path(d), _FakeLLM(), 20, orchestration="auto", permission_mode="autonomous"
            )
            self.assertIsInstance(agent, TaskRouter)


class _SynthLLM:
    """Loop turn returns a terse English report; the synthesis turn returns Chinese."""

    def __init__(self, synth="已修复，4 个测试全部通过。"):
        self._synth = synth
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        names = {t["function"]["name"] for t in (tools or [])}
        if "submit_report" in names:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="r1",
                        name="submit_report",
                        arguments={"summary": "All 4 tests pass."},
                        arguments_json='{"summary": "All 4 tests pass."}',
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content=self._synth, tool_calls=None, finish_reason="stop")


class FastSynthesisTest(unittest.TestCase):
    def test_fast_mode_synthesizes_final_answer_in_user_language(self):
        with tempfile.TemporaryDirectory() as d:
            llm = _SynthLLM()
            agent = build_agent(
                Path(d), llm, 20, orchestration="fast", permission_mode="autonomous"
            )
            result = agent.run("修复测试")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.summary, "已修复，4 个测试全部通过。")
        self.assertTrue(result.artifacts.get("synthesized"))
        self.assertGreaterEqual(llm.calls, 2)  # loop turn + synthesis turn


if __name__ == "__main__":
    unittest.main()
