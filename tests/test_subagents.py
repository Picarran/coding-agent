"""Tests for SubAgent tool permissions and structured reports."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agents.coding_agent import CodingAgent
from src.agents.explorer_agent import ExplorerAgent
from src.agents.registries import (
    build_coding_registry,
    build_explorer_registry,
    build_test_registry,
)
from src.core.models import ToolCall
from src.llm.base import LLMClient, LLMResponse


class _ReportLLM(LLMClient):
    def __init__(self, report):
        self._report = report

    def chat(self, messages, tools=None):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="r",
                    name="submit_report",
                    arguments=self._report,
                    arguments_json=json.dumps(self._report),
                )
            ],
            finish_reason="tool_calls",
        )


class RegistryPermissionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_explorer_has_no_write_tools(self):
        names = build_explorer_registry(self.root).names()
        self.assertIn("read_file", names)
        self.assertIn("search_text", names)
        self.assertIn("execute_command", names)
        self.assertNotIn("patch_file", names)
        self.assertNotIn("write_file", names)

    def test_coding_has_write_tools(self):
        names = build_coding_registry(self.root).names()
        self.assertIn("patch_file", names)
        self.assertIn("write_file", names)

    def test_test_has_no_write_tools(self):
        names = build_test_registry(self.root).names()
        self.assertIn("execute_command", names)
        self.assertNotIn("patch_file", names)
        self.assertNotIn("write_file", names)


class SubAgentReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_explorer_returns_structured_report(self):
        report = {
            "summary": "found the bug",
            "relevant_files": ["a.py"],
            "suspected_causes": ["integer division"],
        }
        agent = ExplorerAgent(_ReportLLM(report), self.root)
        result = agent.run("investigate")

        self.assertEqual(result.agent_name, "explorer_agent")
        self.assertEqual(result.artifacts["report"]["summary"], "found the bug")
        self.assertEqual(result.artifacts["report"]["relevant_files"], ["a.py"])
        self.assertIn("found the bug", result.summary)

    def test_coding_agent_name(self):
        report = {"summary": "patched a.py", "modified_files": ["a.py"]}
        agent = CodingAgent(_ReportLLM(report), self.root)
        result = agent.run("fix")
        self.assertEqual(result.agent_name, "coding_agent")


if __name__ == "__main__":
    unittest.main()
