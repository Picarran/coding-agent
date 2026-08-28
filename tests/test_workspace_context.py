"""Tests for the WorkspaceContext (compact cross-step shared state)."""
from __future__ import annotations

import unittest

from src.context.workspace_context import WorkspaceContext
from src.core.models import AgentResult, AgentStatus


class WorkspaceContextTest(unittest.TestCase):
    def test_record_and_render(self):
        wc = WorkspaceContext()
        wc.record(
            AgentResult(
                agent_name="explorer_agent",
                status=AgentStatus.SUCCESS,
                summary="found",
                artifacts={
                    "report": {"findings": "divide bug", "relevant_files": ["calculator.py"]}
                },
            )
        )
        out = wc.render()
        self.assertIn("calculator.py", out)
        self.assertIn("divide bug", out)

    def test_empty_render(self):
        self.assertEqual(WorkspaceContext().render(), "")

    def test_records_modified_files(self):
        wc = WorkspaceContext()
        wc.record(
            AgentResult(
                agent_name="coding_agent",
                status=AgentStatus.SUCCESS,
                summary="fixed",
                artifacts={"report": {"modified_files": ["calculator.py"]}},
            )
        )
        self.assertIn("calculator.py", wc.modified_files)
        self.assertIn("calculator.py", wc.inspected_files)


if __name__ == "__main__":
    unittest.main()
