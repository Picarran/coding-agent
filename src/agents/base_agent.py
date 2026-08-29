"""Base SubAgent: a ReAct loop with a role, restricted tools, and a structured report.

Each SubAgent is the action layer specialized for one job. It returns a
structured report (via a ``submit_report`` terminal tool) instead of free-form
text, so the Main Agent receives structured artifacts rather than raw history.
"""
from __future__ import annotations

from typing import Any

from src.core.events import EventBus
from src.core.models import AgentResult
from src.llm.base import LLMClient
from src.loops.react_loop import ReactLoop
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

REPORT_TOOL_NAME = "submit_report"


def make_report_tool(fields: dict[str, Any]) -> ToolDefinition:
    properties = {
        "summary": {
            "type": "string",
            "description": "One-sentence summary of what you did and found.",
        },
    }
    for name, spec in fields.items():
        properties[name] = spec
    return ToolDefinition(
        name=REPORT_TOOL_NAME,
        description="Submit your structured report and finish the task.",
        parameters={"type": "object", "properties": properties, "required": ["summary"]},
        func=lambda **kwargs: "report submitted",
    )


class BaseAgent:
    def __init__(
        self,
        name: str,
        llm: LLMClient,
        registry: ToolRegistry,
        system_prompt: str,
        report_fields: dict[str, Any],
        event_bus: EventBus | None = None,
        max_steps: int = 20,
        permission_checker: Any = None,
        summarizer_llm: LLMClient | None = None,
    ) -> None:
        self._name = name
        registry.register(make_report_tool(report_fields))
        executor = ToolExecutor(
            registry,
            permission_checker=permission_checker,
            event_bus=event_bus,
            agent_id=name,
        )
        self._loop = ReactLoop(
            llm,
            executor,
            system_prompt,
            event_bus=event_bus,
            agent_id=name,
            report_tool_name=REPORT_TOOL_NAME,
            max_steps=max_steps,
            summarizer_llm=summarizer_llm,
        )

    def run(self, subtask: str) -> AgentResult:
        result = self._loop.run(subtask)
        result.agent_name = self._name
        return result
