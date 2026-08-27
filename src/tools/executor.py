"""Tool executor: validates, runs, and normalizes tool calls.

The model only *requests* tool calls; this module actually performs them and
converts any failure into a structured ``ToolResult`` that the loop feeds back
to the model — so an error becomes an observation, not a crash.
"""
from __future__ import annotations

import logging

from src.core.models import ToolCall, ToolResult
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def tool_schemas(self) -> list[dict]:
        return self._registry.tool_schemas()

    def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            logger.warning("unknown tool requested: %s", call.name)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error=(
                    f"Unknown tool '{call.name}'. "
                    f"Available tools: {self._registry.names()}"
                ),
            )
        try:
            output = tool.func(**call.arguments)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=self._truncate(str(output)),
            )
        except Exception as exc:  # noqa: BLE001 - normalize any tool error
            logger.warning("tool %s failed: %s", call.name, exc)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
