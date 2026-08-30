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
import sys
import threading
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
    CACHE_HIT = "CACHE_HIT"
    CONTEXT_COMPACT = "CONTEXT_COMPACT"
    LLM_CALL = "LLM_CALL"
    STREAM_DELTA = "STREAM_DELTA"
    TURN_END = "TURN_END"
    DELEGATION = "DELEGATION"
    ESCALATE = "ESCALATE"
    ROUTE = "ROUTE"
    SKILL_MATCHED = "SKILL_MATCHED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceEvent":
        """Reconstruct an event from its persisted ``to_dict`` form."""
        try:
            event_type = EventType(d["event_type"])
        except (KeyError, ValueError):
            event_type = EventType(d.get("event_type", "SESSION_START"))
        return cls(
            event_type=event_type,
            timestamp=d.get("timestamp") or time.time(),
            session_id=d.get("session_id"),
            agent_id=d.get("agent_id"),
            payload=d.get("payload") or {},
            duration_ms=d.get("duration_ms"),
            status=d.get("status"),
        )


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
        # Serializes dispatch so parallel workers can share one bus without
        # corrupting consumers (JSONL file writes, metric counters, console).
        self._lock = threading.Lock()

    def subscribe(self, consumer: EventConsumer) -> None:
        self._consumers.append(consumer)

    def emit(self, event: TraceEvent) -> None:
        if event.session_id is None:
            event.session_id = self.session_id
        with self._lock:
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
class _Ansi:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


# Events that are pure noise in a "quiet" run (kept in verbose mode).
_QUIET_SKIP = frozenset(
    {EventType.LOOP_STEP, EventType.PRE_TOOL_USE, EventType.POST_TOOL_USE, EventType.LLM_CALL}
)


class ConsoleTracer:
    """Renders events as a compact, optionally-colored CLI trace.

    - ``quiet`` hides per-tool call/result noise, keeping step-level progress.
    - ``color`` (auto-detected from the TTY) adds ANSI status colors.
    - ``result_limit`` caps each tool result, so long outputs never flood the screen.
    """

    def __init__(
        self,
        quiet: bool = False,
        color: bool | None = None,
        result_limit: int = _CONSOLE_RESULT_LIMIT,
    ) -> None:
        self._quiet = quiet
        self._color = (sys.stdout.isatty() if color is None else color)
        self._result_limit = result_limit

    def on_event(self, event: TraceEvent) -> None:
        if self._quiet and event.event_type in _QUIET_SKIP:
            return
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

    def _c(self, text: str, code: str) -> str:
        return f"{code}{text}{_Ansi.RESET}" if self._color else text

    @staticmethod
    def _status_color(status: str | None) -> str:
        s = (status or "").upper()
        if s in ("SUCCESS", "DONE", "COMPLETED"):
            return _Ansi.GREEN
        if s in ("FAILED", "ERROR", "BLOCKED"):
            return _Ansi.RED
        return _Ansi.YELLOW

    def _agent_start(self, event: TraceEvent) -> None:
        if event.agent_id == "main_agent" and "task" in event.payload:
            print(self._c(f"Goal: {event.payload['task']}", _Ansi.BOLD))

    def _plan_created(self, event: TraceEvent) -> None:
        steps = event.payload.get("steps", [])
        print(f"Plan ({len(steps)} step(s)):")
        for s in steps:
            deps = f" (after {', '.join(s['dependencies'])})" if s.get("dependencies") else ""
            role = self._c(s.get("assigned_agent", "coding"), _Ansi.CYAN)
            print(f"  - {s['id']} [{role}]: {s['description']}{deps}")

    def _step_start(self, event: TraceEvent) -> None:
        p = event.payload
        head = self._c(f"▶ {p['step_id']} [{p.get('assigned_agent', 'coding')}]", _Ansi.BOLD)
        print(f"{head}: {p['description']}")

    def _subagent_finish(self, event: TraceEvent) -> None:
        p = event.payload
        status = p.get("status", "")
        status_colored = self._c(status, self._status_color(status))
        print(f"  → {p.get('step_id', '?')} {status_colored}: {p.get('summary', '')}")

    def _replan_finish(self, event: TraceEvent) -> None:
        print(self._c(f"Replanned ({event.payload.get('replans_left', '?')} replans left)", _Ansi.YELLOW))

    def _loop_step(self, event: TraceEvent) -> None:
        print(self._c(f"\n{'-' * 64}\nStep {event.payload.get('iteration', '?')}", _Ansi.DIM))

    def _pre_tool(self, event: TraceEvent) -> None:
        tool = self._c(event.payload.get("tool", "?"), _Ansi.CYAN)
        print(f"  >> {tool}")
        args = event.payload.get("arguments")
        if args:
            print(f"      payload : {json.dumps(args, ensure_ascii=False)}")

    def _post_tool(self, event: TraceEvent) -> None:
        self._print_result(str(event.payload.get("content", "")))

    def _tool_error(self, event: TraceEvent) -> None:
        print(self._c(f"      error   : {event.payload.get('error', '')}", _Ansi.RED))

    def _print_result(self, text: str) -> None:
        if len(text) > self._result_limit:
            text = text[: self._result_limit] + f"\n...[truncated {len(text) - self._result_limit} chars]"
        label = "      result  : "
        print(label + text.replace("\n", "\n" + " " * len(label)))

    def _context_compact(self, event: TraceEvent) -> None:
        print(
            self._c(
                f"  [context] trimmed {event.payload.get('removed', 0)} message(s) "
                f"(total {event.payload.get('trimmed_exchanges', 0)})",
                _Ansi.DIM,
            )
        )

    def _llm_call(self, event: TraceEvent) -> None:
        if event.status == "error":
            print(
                self._c(
                    f"  [warn] LLM error (attempt {event.payload.get('attempt', '?')}): "
                    f"{event.payload.get('error', '')}",
                    _Ansi.YELLOW,
                )
            )

    def _approval_required(self, event: TraceEvent) -> None:
        print(self._c(f"      [approval] {event.payload.get('description', '')}", _Ansi.YELLOW))

    def _approval_granted(self, event: TraceEvent) -> None:
        print(self._c("      [approval] granted", _Ansi.GREEN))

    def _approval_rejected(self, event: TraceEvent) -> None:
        print(self._c(f"      [approval] rejected: {event.payload.get('reason', '')}", _Ansi.RED))

    def _agent_finish(self, event: TraceEvent) -> None:
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
        self._tool_cache_hits = 0
        self._tool_durations: list[float] = []
        self._replans = 0
        self._subagents = 0
        self._context_compactions = 0
        self._parallel_batches = 0
        self._parallel_steps = 0
        self._escalations = 0
        self._fast_routes = 0
        self._multi_routes = 0
        self._task_scores: list[int] = []
        self._skill_matches = 0
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
        elif t == EventType.CACHE_HIT:
            self._tool_cache_hits += 1
        elif t == EventType.POST_TOOL_USE and event.duration_ms is not None:
            self._tool_durations.append(event.duration_ms)
        elif t == EventType.REPLAN_FINISH:
            self._replans += 1
        elif t == EventType.SUBAGENT_START:
            self._subagents += 1
        elif t == EventType.CONTEXT_COMPACT:
            self._context_compactions += 1
        elif t == EventType.DELEGATION:
            strategy = (event.payload or {}).get("strategy")
            step_ids = (event.payload or {}).get("step_ids") or []
            if strategy == "parallel":
                self._parallel_batches += 1
                self._parallel_steps += len(step_ids)
        elif t == EventType.ESCALATE:
            self._escalations += 1
        elif t == EventType.ROUTE:
            route = (event.payload or {}).get("route")
            score = (event.payload or {}).get("task_score")
            if route == "fast":
                self._fast_routes += 1
            elif route == "multi":
                self._multi_routes += 1
            if score is not None:
                self._task_scores.append(int(score))
        elif t == EventType.SKILL_MATCHED:
            self._skill_matches += 1
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
            "tool_cache_hits": self._tool_cache_hits,
            "tool_success_rate": success_rate,
            "tool_avg_ms": self._avg(self._tool_durations),
            "replans": self._replans,
            "subagents": self._subagents,
            "context_compactions": self._context_compactions,
            "parallel_batches": self._parallel_batches,
            "parallel_steps": self._parallel_steps,
            "escalations": self._escalations,
            "fast_routes": self._fast_routes,
            "multi_routes": self._multi_routes,
            "avg_task_score": self._avg(self._task_scores),
            "skill_matches": self._skill_matches,
            "approvals": dict(self._approvals),
            "duration_ms": duration,
        }
