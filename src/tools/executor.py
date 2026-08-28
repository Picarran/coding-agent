"""Tool executor: validates, runs, and normalizes tool calls.

The model only *requests* tool calls; this module actually performs them and
converts any failure into a structured ``ToolResult`` that the loop feeds back
to the model — so an error becomes an observation, not a crash.

It also emits lifecycle events (PRE/POST_TOOL_USE, TOOL_ERROR, APPROVAL_*)
through an optional ``EventBus`` for the audit log and metrics.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from src.core.events import EventBus, EventType
from src.core.models import ToolCall, ToolResult
from src.tools.registry import ToolRegistry
from src.tools.validation import validate_arguments

if TYPE_CHECKING:
    from src.safety.permissions import PermissionChecker

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000

# Tools whose results are safe to cache (deterministic read-only lookups).
_READ_ONLY_TOOLS = {"list_files", "read_file", "search_text"}
# Tools that can change workspace files and therefore invalidate the cache.
_MUTATING_TOOLS = {"patch_file", "write_file", "execute_command"}


class ToolCache:
    """A small in-memory cache keyed by (tool, normalized args, workspace version)."""

    def __init__(self) -> None:
        self._store: dict[tuple, str] = {}

    def get(self, key: tuple) -> str | None:
        return self._store.get(key)

    def put(self, key: tuple, content: str) -> None:
        self._store[key] = content

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_checker: "PermissionChecker | None" = None,
        event_bus: EventBus | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker
        self._bus = event_bus
        self._agent_id = agent_id
        self._cache = ToolCache()
        self._workspace_version = 0

    @property
    def tool_schemas(self) -> list[dict]:
        return self._registry.tool_schemas()

    def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            logger.warning("unknown tool requested: %s", call.name)
            self._emit(
                EventType.TOOL_ERROR,
                payload={
                    "tool": call.name,
                    "error": (
                        f"Unknown tool '{call.name}'. "
                        f"Available tools: {self._registry.names()}"
                    ),
                },
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error=(
                    f"Unknown tool '{call.name}'. "
                    f"Available tools: {self._registry.names()}"
                ),
            )

        # The model's request (rendered before validation/permission).
        self._emit(
            EventType.PRE_TOOL_USE,
            payload={"tool": call.name, "arguments": call.arguments},
        )

        errors = validate_arguments(tool.parameters, call.arguments)
        if errors:
            logger.warning("invalid arguments for %s: %s", call.name, errors)
            self._emit(
                EventType.TOOL_ERROR,
                payload={"tool": call.name, "error": "Invalid arguments: " + "; ".join(errors)},
            )
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

        cache_key = self._cache_key(call) if call.name in _READ_ONLY_TOOLS else None
        if cache_key is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._emit(EventType.CACHE_HIT, payload={"tool": call.name})
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=cached,
                )

        start = time.monotonic()
        try:
            output = tool.func(**call.arguments)
            duration_ms = (time.monotonic() - start) * 1000
            content = self._truncate(str(output))
            self._emit(
                EventType.POST_TOOL_USE,
                payload={"tool": call.name, "content": content},
                duration_ms=duration_ms,
            )
            if cache_key is not None:
                self._cache.put(cache_key, content)
            elif call.name in _MUTATING_TOOLS:
                self._workspace_version += 1
                self._cache.clear()
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=content,
            )
        except Exception as exc:  # noqa: BLE001 - normalize any tool error
            duration_ms = (time.monotonic() - start) * 1000
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("tool %s failed: %s", call.name, exc)
            self._emit(
                EventType.TOOL_ERROR,
                payload={"tool": call.name, "error": error},
                duration_ms=duration_ms,
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                error=error,
            )

    @staticmethod
    def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"

    def _cache_key(self, call: ToolCall) -> tuple:
        args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False, default=str)
        return (call.name, args, self._workspace_version)

    def _check_permission(self, call: ToolCall) -> str | None:
        """Return an error string if the call must not proceed, else None."""
        if self._checker is None:
            return None
        from src.safety.permissions import Decision

        decision = self._checker.check(call)
        if decision.decision == Decision.AUTO_ALLOW:
            return None
        if decision.decision == Decision.DENY:
            self._emit(
                EventType.APPROVAL_REJECTED,
                payload={
                    "tool": call.name,
                    "reason": decision.reason,
                    "decision": "deny",
                },
            )
            return f"Permission denied: {decision.reason}"
        # ASK: fail-closed when no interactive approver is available.
        self._emit(
            EventType.APPROVAL_REQUIRED,
            payload={
                "tool": call.name,
                "description": decision.description,
                "reason": decision.reason,
                "risk_score": decision.risk_score,
            },
        )
        if self._checker.approver is None:
            self._emit(
                EventType.APPROVAL_REJECTED,
                payload={
                    "tool": call.name,
                    "reason": decision.reason,
                    "decision": "no-approver",
                },
            )
            return (
                f"Permission required ({decision.reason}) but no approver is "
                "available; denied (fail-closed)."
            )
        if self._checker.approver(decision.description):
            self._emit(
                EventType.APPROVAL_GRANTED,
                payload={"tool": call.name, "description": decision.description},
            )
            return None
        self._emit(
            EventType.APPROVAL_REJECTED,
            payload={
                "tool": call.name,
                "reason": decision.reason,
                "decision": "user",
            },
        )
        return f"Permission denied by user: {decision.reason}"

    def _emit(
        self,
        event_type: EventType,
        payload: dict | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self._bus is not None:
            self._bus.emit_simple(
                event_type,
                agent_id=self._agent_id,
                payload=payload,
                duration_ms=duration_ms,
            )
