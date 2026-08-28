"""Tool executor: validates, runs, and normalizes tool calls.

The model only *requests* tool calls; this module actually performs them and
converts any failure into a structured ``ToolResult`` that the loop feeds back
to the model — so an error becomes an observation, not a crash.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.models import ToolCall, ToolResult
from src.tools.registry import ToolRegistry
from src.tools.validation import validate_arguments

if TYPE_CHECKING:
    from src.safety.permissions import PermissionChecker

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_checker: "PermissionChecker | None" = None,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker

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
        errors = validate_arguments(tool.parameters, call.arguments)
        if errors:
            logger.warning("invalid arguments for %s: %s", call.name, errors)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error="Invalid arguments: " + "; ".join(errors),
            )
        denied = self._check_permission(call)
        if denied is not None:
            logger.info("permission blocked tool call '%s': %s", call.name, denied)
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error=denied,
                permission_denied=True,
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

    def _check_permission(self, call: ToolCall) -> str | None:
        """Return an error string if the call must not proceed, else None."""
        if self._checker is None:
            return None
        from src.safety.permissions import Decision

        decision = self._checker.check(call)
        if decision.decision == Decision.AUTO_ALLOW:
            return None
        if decision.decision == Decision.DENY:
            return f"Permission denied: {decision.reason}"
        # ASK: fail-closed when no interactive approver is available.
        if self._checker.approver is None:
            return (
                f"Permission required ({decision.reason}) but no approver is "
                "available; denied (fail-closed)."
            )
        if self._checker.approver(decision.description):
            return None
        return f"Permission denied by user: {decision.reason}"
