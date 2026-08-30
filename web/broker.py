"""Live-event plumbing for the Web Agent Workspace (V3-9).

Two small pieces, deliberately framework-free so they are unit-testable without
spinning up a server:

- ``EventBroker`` — a ``EventBus`` consumer that fans every ``TraceEvent`` out to
  connected SSE subscribers and keeps a bounded replay buffer for late joiners.
- ``WebApprover`` — a blocking approval callback: it emits ``APPROVAL_PENDING``
  with an id, waits for the web client to POST an approve/reject, then returns
  the decision. Supports per-tool "always allow".
"""
from __future__ import annotations

import queue
import threading
import uuid
from typing import Callable

from src.core.events import EventType, TraceEvent

APPROVAL_TIMEOUT = 600.0  # seconds before a forgotten approval auto-rejects


class EventBroker:
    """Broadcasts events to SSE subscribers and replays history on subscribe."""

    def __init__(self, max_history: int = 2000) -> None:
        self._subscribers: list["queue.Queue[TraceEvent]"] = []
        self._history: list[TraceEvent] = []
        self._max_history = max_history
        self._lock = threading.Lock()

    def on_event(self, event: TraceEvent) -> None:
        """``EventConsumer`` interface."""
        self.publish(event)

    def publish(self, event: TraceEvent) -> None:
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # drop for slow clients rather than blocking the agent

    def subscribe(self) -> tuple["queue.Queue[TraceEvent]", list[TraceEvent]]:
        """Return (live_queue, history_snapshot); caller must ``unsubscribe``."""
        q: "queue.Queue[TraceEvent]" = queue.Queue(maxsize=2000)
        with self._lock:
            self._subscribers.append(q)
            history = list(self._history)
        return q, history

    def unsubscribe(self, q: "queue.Queue[TraceEvent]") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


class WebApprover:
    """Blocks the agent until the web client resolves the approval."""

    def __init__(
        self,
        publish: Callable[[TraceEvent], None],
        timeout: float = APPROVAL_TIMEOUT,
    ) -> None:
        self._publish = publish
        self._timeout = timeout
        self._pending: dict[str, dict] = {}
        self._always_tools: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, description: str) -> bool:
        tool = description.split(":", 1)[0].strip()
        if tool in self._always_tools:
            return True
        approval_id = uuid.uuid4().hex
        event = threading.Event()
        holder = {"result": False}
        with self._lock:
            self._pending[approval_id] = {
                "event": event,
                "holder": holder,
                "tool": tool,
            }
        self._publish(
            TraceEvent(
                EventType.APPROVAL_PENDING,
                payload={
                    "approval_id": approval_id,
                    "description": description,
                    "tool": tool,
                },
            )
        )
        event.wait(timeout=self._timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        return holder["result"]

    def resolve(self, approval_id: str, approve: bool, always: bool = False) -> bool:
        """Resolve a pending approval; return False if unknown/already resolved."""
        with self._lock:
            item = self._pending.get(approval_id)
        if item is None:
            return False
        item["holder"]["result"] = bool(approve)
        if always and approve:
            self._always_tools.add(item["tool"])
        item["event"].set()
        return True

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
