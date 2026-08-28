"""Unified event bus for tracing, auditing, and metrics (V1-2).

The runtime (``ReactLoop`` / ``ToolExecutor`` / ``MainAgent`` / ``ContextManager``)
emits one stream of structured ``TraceEvent``s; any number of consumers observe
that stream. Three consumers ship with the project:

- ``ConsoleTracer`` — human-readable CLI trace (the old tracer, re-hosted).
- ``JsonlAuditLogger`` — appends one JSON line per event to a file (audit log).
- ``MetricsCollector`` — aggregates counters/timers into a summary dict.

This is the single data source that the V3 web dashboard will consume.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_CONSOLE_RESULT_LIMIT = 4000


class EventType(str, Enum):
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"
    AGENT_START = "AGENT_START"
    AGENT_FINISH = "AGENT_FINISH"
    PLAN_CREATED = "PLAN_CREATED"
    STEP_START = "STEP_START"
    SUBAGENT_START = "SUBAGENT_START"
    SUBAGENT_FINISH = "SUBAGENT_FINISH"
    REPLAN_START = "REPLAN_START"
    REPLAN_FINISH = "REPLAN_FINISH"
    LOOP_STEP = "LOOP_STEP"
    PRE_TOOL_USE = "PRE_TOOL_USE"
    POST_TOOL_USE = "POST_TOOL_USE"
    TOOL_ERROR = "TOOL_ERROR"
    CONTEXT_COMPACT = "CONTEXT_COMPACT"
    LLM_CALL = "LLM_CALL"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"


@dataclass
class TraceEvent:
    """One structured lifecycle event.

    ``event_type``, ``timestamp``, ``session_id``, ``agent_id``, ``payload``,
    ``duration_ms``, ``status`` — the contract every consumer (console, JSONL
    audit log, metrics) reads.
    """

    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    session_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }


class EventConsumer(Protocol):
    def on_event(self, event: TraceEvent) -> None: ...


class EventBus:
    """Broadcasts ``TraceEvent``s to all subscribed consumers."""

    def __init__(
        self,
        consumers: list[EventConsumer] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._consumers: list[EventConsumer] = list(consumers or [])
        self.session_id = session_id

    def subscribe(self, consumer: EventConsumer) -> None:
        self._consumers.append(consumer)

    def emit(self, event: TraceEvent) -> None:
        if event.session_id is None:
            event.session_id = self.session_id
        for consumer in self._consumers:
            try:
                consumer.on_event(event)
            except Exception as exc:  # noqa: BLE001 - a consumer must never break the runtime
                logger.warning(
                    "event consumer %s failed: %s", type(consumer).__name__, exc
                )

    def emit_simple(
        self,
        event_type: EventType,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        status: str | None = None,
    ) -> None:
        self.emit(
            TraceEvent(
                event_type,
                agent_id=agent_id,
                payload=payload or {},
                duration_ms=duration_ms,
                status=status,
            )
        )


# --------------------------------------------------------------------------- #
# Consumers.
# --------------------------------------------------------------------------- #
class ConsoleTracer:
    """Renders events as an ASCII CLI trace (Windows-console safe)."""

    def on_event(self, event: TraceEvent) -> None:
        handler = {
            EventType.AGENT_START: self._agent_start,
            EventType.PLAN_CREATED: self._plan_created,
            EventType.STEP_START: self._step_start,
            EventType.SUBAGENT_FINISH: self._subagent_finish,
            EventType.REPLAN_FINISH: self._replan_finish,
            EventType.LOOP_STEP: self._loop_step,
            EventType.PRE_TOOL_USE: self._pre_tool,
            EventType.POST_TOOL_USE: self._post_tool,
            EventType.TOOL_ERROR: self._tool_error,
            EventType.CONTEXT_COMPACT: self._context_compact,
            EventType.LLM_CALL: self._llm_call,
            EventType.APPROVAL_REQUIRED: self._approval_required,
            EventType.APPROVAL_GRANTED: self._approval_granted,
            EventType.APPROVAL_REJECTED: self._approval_rejected,
            EventType.AGENT_FINISH: self._agent_finish,
        }.get(event.event_type)
        if handler is not None:
            handler(event)

    @staticmethod
    def _agent_start(event: TraceEvent) -> None:
        if event.agent_id == "main_agent" and "task" in event.payload:
            print(f"Goal: {event.payload['task']}")

    @staticmethod
    def _plan_created(event: TraceEvent) -> None:
        steps = event.payload.get("steps", [])
        print(f"Plan ({len(steps)} step(s)):")
        for s in steps:
            deps = f" (after {', '.join(s['dependencies'])})" if s.get("dependencies") else ""
            print(f"  - {s['id']} [{s.get('assigned_agent', 'coding')}]: {s['description']}{deps}")

    @staticmethod
    def _step_start(event: TraceEvent) -> None:
        p = event.payload
        print(f"Dispatch {p['step_id']} [{p.get('assigned_agent', 'coding')}]: {p['description']}")

    @staticmethod
    def _subagent_finish(event: TraceEvent) -> None:
        p = event.payload
        print(f"  -> {p.get('step_id', '?')} {p.get('status', '')}: {p.get('summary', '')}")

    @staticmethod
    def _replan_finish(event: TraceEvent) -> None:
        print(f"Replanned ({event.payload.get('replans_left', '?')} replans left)")

    @staticmethod
    def _loop_step(event: TraceEvent) -> None:
        print(f"\n{'-' * 64}")
        print(f"Step {event.payload.get('iteration', '?')}")

    @staticmethod
    def _pre_tool(event: TraceEvent) -> None:
        print(f"  >> {event.payload.get('tool', '?')}")
        args = event.payload.get("arguments")
        if args:
            print(f"      payload : {json.dumps(args, ensure_ascii=False)}")

    @staticmethod
    def _post_tool(event: TraceEvent) -> None:
        text = str(event.payload.get("content", ""))
        ConsoleTracer._print_result(text)

    @staticmethod
    def _tool_error(event: TraceEvent) -> None:
        print(f"      error   : {event.payload.get('error', '')}")

    @staticmethod
    def _print_result(text: str) -> None:
        if len(text) > _CONSOLE_RESULT_LIMIT:
            text = text[:_CONSOLE_RESULT_LIMIT] + f"\n...[truncated {len(text) - _CONSOLE_RESULT_LIMIT} chars]"
        label = "      result  : "
        print(label + text.replace("\n", "\n" + " " * len(label)))

    @staticmethod
    def _context_compact(event: TraceEvent) -> None:
        print(
            f"  [context] trimmed {event.payload.get('removed', 0)} message(s) "
            f"(total {event.payload.get('trimmed_exchanges', 0)})"
        )

    @staticmethod
    def _llm_call(event: TraceEvent) -> None:
        if event.status == "error":
            print(
                f"  [warn] LLM error (attempt {event.payload.get('attempt', '?')}): "
                f"{event.payload.get('error', '')}"
            )

    @staticmethod
    def _approval_required(event: TraceEvent) -> None:
        print(f"      [approval] {event.payload.get('description', '')}")

    @staticmethod
    def _approval_granted(event: TraceEvent) -> None:
        print("      [approval] granted")

    @staticmethod
    def _approval_rejected(event: TraceEvent) -> None:
        print(f"      [approval] rejected: {event.payload.get('reason', '')}")

    @staticmethod
    def _agent_finish(event: TraceEvent) -> None:
        if event.agent_id and event.agent_id != "main_agent":
            final_state = (event.payload or {}).get("final_state", event.status or "?")
            print(f"  [state] RUNNING -> {final_state}")


class JsonlAuditLogger:
    """Appends one JSON object per event to a file (lazy-open, flushed)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._fh = None

    def on_event(self, event: TraceEvent) -> None:
        if self._fh is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a", encoding="utf-8")
        self._fh.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class MetricsCollector:
    """Aggregates events into a small set of counters/timers."""

    def __init__(self) -> None:
        self._llm_calls = 0
        self._total_tokens = 0
        self._llm_durations: list[float] = []
        self._tool_calls = 0
        self._tool_errors = 0
        self._tool_durations: list[float] = []
        self._replans = 0
        self._subagents = 0
        self._context_compactions = 0
        self._approvals = {"required": 0, "granted": 0, "rejected": 0}
        self._session_start: float | None = None
        self._session_end: float | None = None

    def on_event(self, event: TraceEvent) -> None:
        t = event.event_type
        if t == EventType.LLM_CALL:
            self._llm_calls += 1
            if event.duration_ms is not None:
                self._llm_durations.append(event.duration_ms)
            tokens = (event.payload or {}).get("total_tokens")
            if tokens is not None:
                self._total_tokens += int(tokens)
        elif t == EventType.PRE_TOOL_USE:
            # ``submit_report`` is a terminal pseudo-tool, not a real action.
            if not (event.payload or {}).get("report"):
                self._tool_calls += 1
        elif t == EventType.TOOL_ERROR:
            self._tool_errors += 1
        elif t == EventType.POST_TOOL_USE and event.duration_ms is not None:
            self._tool_durations.append(event.duration_ms)
        elif t == EventType.REPLAN_FINISH:
            self._replans += 1
        elif t == EventType.SUBAGENT_START:
            self._subagents += 1
        elif t == EventType.CONTEXT_COMPACT:
            self._context_compactions += 1
        elif t == EventType.APPROVAL_REQUIRED:
            self._approvals["required"] += 1
        elif t == EventType.APPROVAL_GRANTED:
            self._approvals["granted"] += 1
        elif t == EventType.APPROVAL_REJECTED:
            self._approvals["rejected"] += 1
        elif t == EventType.SESSION_START:
            self._session_start = event.timestamp
        elif t == EventType.SESSION_END:
            self._session_end = event.timestamp

    @staticmethod
    def _avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    def summary(self) -> dict[str, Any]:
        total_tool = self._tool_calls + self._tool_errors
        success_rate = round(self._tool_calls / total_tool, 3) if total_tool else None
        duration = (
            round((self._session_end - self._session_start) * 1000, 1)
            if self._session_start is not None and self._session_end is not None
            else None
        )
        return {
            "llm_calls": self._llm_calls,
            "total_tokens": self._total_tokens,
            "llm_avg_ms": self._avg(self._llm_durations),
            "tool_calls": self._tool_calls,
            "tool_errors": self._tool_errors,
            "tool_success_rate": success_rate,
            "tool_avg_ms": self._avg(self._tool_durations),
            "replans": self._replans,
            "subagents": self._subagents,
            "context_compactions": self._context_compactions,
            "approvals": dict(self._approvals),
            "duration_ms": duration,
        }
