"""Tests for the DirectWorker (V2-5): a no-report lightweight ReAct loop."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agents.direct_worker import DirectWorker
from src.core.models import AgentStatus
from src.llm.base import LLMResponse


class _TextLLM:
    def __init__(self, content):
        self._content = content

    def chat(self, messages, tools=None):
        return LLMResponse(content=self._content, tool_calls=None, finish_reason="stop")


class DirectWorkerTest(unittest.TestCase):
    def test_has_full_toolset_but_no_report_tool(self):
        with tempfile.TemporaryDirectory() as d:
            worker = DirectWorker(_TextLLM("done"), Path(d))
            names = [s["function"]["name"] for s in worker._loop._executor.tool_schemas]
            self.assertNotIn("submit_report", names)
            self.assertIn("write_file", names)
            self.assertIn("read_file", names)
            self.assertIn("execute_command", names)

    def test_stops_on_plain_text_answer(self):
        with tempfile.TemporaryDirectory() as d:
            worker = DirectWorker(_TextLLM("finished the step"), Path(d))
            result = worker.run("do something")
            self.assertEqual(result.status, AgentStatus.SUCCESS)
            self.assertEqual(result.summary, "finished the step")
            self.assertEqual(result.agent_name, "direct_worker")


if __name__ == "__main__":
    unittest.main()
