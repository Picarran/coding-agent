"""Tests for orchestration modes (V2-5.4): fast / auto / thorough topologies."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.main_agent import MainAgent
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


if __name__ == "__main__":
    unittest.main()
