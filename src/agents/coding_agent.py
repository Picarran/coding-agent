"""Coding SubAgent: implements/fixes code with the full toolset."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.base_agent import BaseAgent
from src.agents.registries import build_coding_registry
from src.context.environment import build_environment_context
from src.core.events import EventBus
from src.llm.base import LLMClient
from src.tools.definitions import ToolDefinition

CODING_SYSTEM = (
    "You are a Coding agent. Read the relevant code, implement or fix the required "
    "change, and verify it locally. You may list, read, search, patch, write files, "
    "and run commands. When finished, submit your structured patch report with "
    "submit_report."
)

CODING_REPORT_FIELDS = {
    "modified_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Files you modified or created.",
    },
    "changes": {"type": "string", "description": "Description of the changes."},
    "verification_result": {
        "type": "string",
        "description": "Result of local verification (e.g. tests/commands you ran).",
    },
    "remaining_issues": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Remaining issues, if any.",
    },
}


class CodingAgent(BaseAgent):
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
    ) -> None:
        registry = build_coding_registry(root)
        for tool in extra_tools or []:
            registry.register(tool)
        super().__init__(
            "coding_agent",
            llm,
            registry,
            CODING_SYSTEM + "\n\n" + build_environment_context(root),
            CODING_REPORT_FIELDS,
            event_bus,
            max_steps,
            permission_checker,
            summarizer_llm=summarizer_llm,
            checkpoint_cb=checkpoint_cb,
        )
