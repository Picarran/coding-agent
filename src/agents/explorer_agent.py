"""Explorer SubAgent: investigates the workspace (read-only + search + check commands)."""
from __future__ import annotations

from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.registries import build_explorer_registry
from src.context.environment import build_environment_context
from src.core.events import Tracer
from src.llm.base import LLMClient

EXPLORER_SYSTEM = (
    "You are an Explorer agent. Investigate the workspace to gather information: "
    "list files, search for code, read files, and run read-only commands. Do NOT "
    "modify or create files. When finished, submit your structured investigation "
    "report with submit_report."
)

EXPLORER_REPORT_FIELDS = {
    "relevant_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Files relevant to the investigation.",
    },
    "findings": {"type": "string", "description": "What you found."},
    "suspected_causes": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Suspected root causes.",
    },
    "suggested_next_action": {"type": "string", "description": "Suggested next step."},
}


class ExplorerAgent(BaseAgent):
    def __init__(self, llm: LLMClient, root: Path, tracer: Tracer | None = None) -> None:
        super().__init__(
            "explorer_agent",
            llm,
            build_explorer_registry(root),
            EXPLORER_SYSTEM + "\n\n" + build_environment_context(root),
            EXPLORER_REPORT_FIELDS,
            tracer,
        )
