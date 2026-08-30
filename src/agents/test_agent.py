"""Test SubAgent: runs tests/commands and reports concrete verification evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.base_agent import BaseAgent
from src.agents.registries import build_test_registry
from src.context.environment import build_environment_context
from src.core.events import EventBus
from src.llm.base import LLMClient
from src.tools.definitions import ToolDefinition

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
    def __init__(
        self,
        llm: LLMClient,
        root: Path,
        event_bus: EventBus | None = None,
        max_steps: int = 20,
        permission_checker: Any = None,
        summarizer_llm: Any = None,
        checkpoint_cb: Any = None,
        extra_tools: list[ToolDefinition] | None = None,
        streaming: bool = False,
    ) -> None:
        registry = build_test_registry(root)
        for tool in extra_tools or []:
            registry.register(tool)
        super().__init__(
            "test_agent",
            llm,
            registry,
            TEST_SYSTEM + "\n\n" + build_environment_context(root),
            TEST_REPORT_FIELDS,
            event_bus,
            max_steps,
            permission_checker,
            summarizer_llm=summarizer_llm,
            checkpoint_cb=checkpoint_cb,
            streaming=streaming,
        )
