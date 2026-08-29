"""Tests for ModelRouter (V2-6)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.llm.router import ModelRouter, TaskType
from src.main import build_main_agent


class _Stub:
    def __init__(self, name: str):
        self.name = name

    def chat(self, messages, tools=None):
        raise AssertionError("LLM must not be called during routing tests")


class ModelRouterTest(unittest.TestCase):
    def test_single_model_routes_everywhere(self):
        one = _Stub("one")
        r = ModelRouter(one)
        self.assertFalse(r.split)
        for t in TaskType:
            self.assertIs(r.route(t), one)

    def test_split_routes_strong_vs_fast(self):
        strong = _Stub("strong")
        fast = _Stub("fast")
        r = ModelRouter(strong, fast)
        self.assertTrue(r.split)
        self.assertIs(r.route(TaskType.PLANNING), strong)
        self.assertIs(r.route(TaskType.CODING), strong)
        self.assertIs(r.route(TaskType.EXPLORATION), fast)
        self.assertIs(r.route(TaskType.TESTING), fast)
        self.assertIs(r.route(TaskType.SUMMARIZATION), fast)
        self.assertIs(r.route(TaskType.SYNTHESIS), fast)


class BuildAgentRouterTest(unittest.TestCase):
    def test_build_main_agent_wires_router(self):
        strong = _Stub("strong")
        fast = _Stub("fast")
        r = ModelRouter(strong, fast)
        with tempfile.TemporaryDirectory() as d:
            agent = build_main_agent(
                Path(d), strong, 20, permission_mode="autonomous", router=r
            )
            # Planner/Replanner and the coding SubAgent are strong.
            self.assertIs(agent._planner._llm, strong)
            self.assertIs(agent._agents["coding"]._loop._llm, strong)
            # Explorer/Test SubAgents and synthesis are fast.
            self.assertIs(agent._agents["explorer"]._loop._llm, fast)
            self.assertIs(agent._agents["test"]._loop._llm, fast)
            self.assertIs(agent._llm, fast)


if __name__ == "__main__":
    unittest.main()
