"""Test SubAgent: runs tests/commands and reports concrete verification evidence."""
from __future__ import annotations

from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.registries import build_test_registry
from src.core.events import Tracer
from src.llm.base import LLMClient

TEST_SYSTEM = (
    "You are a Test agent. Run the relevant tests or commands and report concrete "
    "evidence (command, exit_code, passed/failed). Do not guess; base your report on "
    "actual execution output. Submit your report with submit_report."
)

TEST_REPORT_FIELDS = {
    "command": {"type": "string", "description": "The command you ran."},
    "exit_code": {"type": "integer", "description": "Exit code of the command."},
    "passed": {"type": "array", "items": {"type": "string"}, "description": "Tests/checks that passed."},
    "failed": {"type": "array", "items": {"type": "string"}, "description": "Tests/checks that failed."},
    "error_summary": {"type": "string", "description": "Summary of failures/errors."},
    "suggested_next_action": {"type": "string", "description": "Suggested next action."},
}


class TestAgent(BaseAgent):
    def __init__(self, llm: LLMClient, root: Path, tracer: Tracer | None = None) -> None:
        super().__init__(
            "test_agent",
            llm,
            build_test_registry(root),
            TEST_SYSTEM,
            TEST_REPORT_FIELDS,
            tracer,
        )
