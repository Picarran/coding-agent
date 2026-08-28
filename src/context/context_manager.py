"""Context management for a single agent loop.

Keeps the system prompt and the current task, and trims the OLDEST tool
exchanges when the message list exceeds a budget. This avoids the unbounded
``messages.append(...)`` growth the project explicitly forbids.

Trimming is deterministic: whole assistant+tool-result exchanges are dropped,
and a single marker note is inserted so the model knows earlier steps were
removed. An LLM-based summarizer can replace this later without changing the
interface.
"""
from __future__ import annotations

import logging

from src.core.events import EventBus, EventType
from src.core.models import Message

logger = logging.getLogger(__name__)

_MARKER_PREFIX = "[Context trimmed:"


class ContextManager:
    def __init__(
        self,
        system_prompt: str,
        max_messages: int = 30,
        max_chars: int = 100_000,
        event_bus: EventBus | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_messages = max_messages
        self._max_chars = max_chars
        self._messages: list[Message] = []
        self._trimmed_exchanges = 0
        self._bus = event_bus
        self._agent_id = agent_id

    def start(self, task: str) -> None:
        self._messages = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=task),
        ]
        self._trimmed_exchanges = 0

    def is_empty(self) -> bool:
        return len(self._messages) == 0

    def append(self, message: Message) -> None:
        self._messages.append(message)
        self._compact()

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def trimmed_exchanges(self) -> int:
        return self._trimmed_exchanges

    def _compact(self) -> None:
        while self._over_budget():
            removed = self._remove_oldest_exchange()
            if removed == 0:
                break
            self._trimmed_exchanges += 1
            logger.info("context trimmed: removed %d message(s)", removed)
            self._emit_compact(removed)
        self._refresh_marker()

    def _emit_compact(self, removed: int) -> None:
        if self._bus is not None:
            self._bus.emit_simple(
                EventType.CONTEXT_COMPACT,
                agent_id=self._agent_id,
                payload={
                    "removed": removed,
                    "trimmed_exchanges": self._trimmed_exchanges,
                },
            )

    def _over_budget(self) -> bool:
        return len(self._messages) > self._max_messages or self._total_chars() > self._max_chars

    def _total_chars(self) -> int:
        return sum(len(m.content or "") for m in self._messages)

    def _remove_oldest_exchange(self) -> int:
        # 1) prefer removing a full tool exchange (assistant + its tool results)
        for i in range(2, len(self._messages)):
            m = self._messages[i]
            if m.role == "assistant" and m.tool_calls:
                j = i + 1
                while j < len(self._messages) and self._messages[j].role == "tool":
                    j += 1
                del self._messages[i:j]
                return j - i
        # 2) fallback: remove the oldest plain assistant answer (no tool pairing)
        for i in range(2, len(self._messages)):
            m = self._messages[i]
            if m.role == "assistant" and not m.tool_calls:
                del self._messages[i]
                return 1
        return 0

    def _refresh_marker(self) -> None:
        if len(self._messages) > 2:
            m = self._messages[2]
            if m.role == "system" and m.content and m.content.startswith(_MARKER_PREFIX):
                del self._messages[2]
        if self._trimmed_exchanges > 0:
            self._messages.insert(
                2,
                Message(
                    role="system",
                    content=(
                        f"{_MARKER_PREFIX} {self._trimmed_exchanges} earlier "
                        "tool-exchange(s) removed to stay within the context budget.]"
                    ),
                ),
            )
