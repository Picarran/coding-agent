"""Context management for a single agent loop (V1-3).

Keeps the system prompt and the current task, and trims the OLDEST tool
exchanges when the message list exceeds a budget. The budget is now
token-aware (``chars/4`` approximation) instead of pure character count.

When a ``summarizer`` is supplied, trimmed exchanges are first compressed by
the LLM into bullet points (key conclusions / modified files / failed attempts /
unresolved issues) and kept as a rolling summary — information is preserved
instead of hard-dropped. Without a summarizer it falls back to the old
deterministic hard-delete + marker.
"""
from __future__ import annotations

import logging
from typing import Callable

from src.core.events import EventBus, EventType
from src.core.models import Message

logger = logging.getLogger(__name__)

_MARKER_PREFIX = "[Context trimmed:"
_SUMMARY_PREFIX = "[Summarized earlier steps]"

Summarizer = Callable[[list[Message]], str]


class ContextManager:
    def __init__(
        self,
        system_prompt: str,
        max_messages: int = 30,
        max_tokens: int = 8000,
        event_bus: EventBus | None = None,
        agent_id: str | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_messages = max_messages
        self._max_tokens = max_tokens
        self._messages: list[Message] = []
        self._trimmed_exchanges = 0
        self._bus = event_bus
        self._agent_id = agent_id
        self._summarizer = summarizer
        self._summaries: list[str] = []

    def start(self, task: str) -> None:
        self._messages = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=task),
        ]
        self._trimmed_exchanges = 0
        self._summaries = []

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
        removed: list[Message] = []
        while self._over_budget():
            msgs = self._remove_oldest_exchange()
            if not msgs:
                break
            removed.extend(msgs)
            self._trimmed_exchanges += 1
            logger.info("context trimmed: removed %d message(s)", len(msgs))
            self._emit_compact(len(msgs))
        if removed and self._summarizer is not None:
            summary = self._summarizer(removed)
            if summary:
                self._summaries.append(summary)
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
        return (
            len(self._messages) > self._max_messages
            or self._total_tokens() > self._max_tokens
        )

    def _total_tokens(self) -> int:
        return sum(self._estimate_tokens(m) for m in self._messages)

    @staticmethod
    def _estimate_tokens(m: Message) -> int:
        # chars/4 is a cheap, dependency-free token estimate.
        n = len(m.content or "") // 4
        if m.tool_calls:
            n += sum(len(tc.arguments_json or "") // 4 for tc in m.tool_calls)
        return n

    def _remove_oldest_exchange(self) -> list[Message]:
        # 1) prefer removing a full tool exchange (assistant + its tool results)
        for i in range(2, len(self._messages)):
            m = self._messages[i]
            if m.role == "assistant" and m.tool_calls:
                j = i + 1
                while j < len(self._messages) and self._messages[j].role == "tool":
                    j += 1
                removed = self._messages[i:j]
                del self._messages[i:j]
                return removed
        # 2) fallback: remove the oldest plain assistant answer (no tool pairing)
        for i in range(2, len(self._messages)):
            m = self._messages[i]
            if m.role == "assistant" and not m.tool_calls:
                removed = [self._messages[i]]
                del self._messages[i]
                return removed
        return []

    @staticmethod
    def _is_marker(m: Message) -> bool:
        return (
            m.role == "system"
            and bool(m.content)
            and (m.content.startswith(_MARKER_PREFIX) or m.content.startswith(_SUMMARY_PREFIX))
        )

    def _refresh_marker(self) -> None:
        if len(self._messages) > 2 and self._is_marker(self._messages[2]):
            del self._messages[2]
        if self._summaries:
            self._messages.insert(
                2,
                Message(
                    role="system",
                    content=_SUMMARY_PREFIX + "\n" + "\n".join(self._summaries),
                ),
            )
        elif self._trimmed_exchanges > 0:
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
