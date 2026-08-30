"""LLM client abstraction.

Upper-layer agent code depends only on this interface, never on DeepSeek's
specific request details. This keeps the model backend swappable and the agent
loop easy to test with a mock.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterator

from src.core.models import Message, ToolCall


@dataclass
class LLMResponse:
    """A single model response: text, tool calls, or both.

    ``usage`` optionally carries token counts
    (``{"prompt_tokens": .., "completion_tokens": .., "total_tokens": ..}``)
    when the backend reports them; mock clients may leave it ``None``.
    """

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class StreamChunk:
    """One piece of a streaming response.

    ``content`` is a single text token delta (or the whole text for the
    one-shot fallback). The terminal chunk carries the assembled ``tool_calls``,
    ``finish_reason`` and ``usage``; intermediate chunks leave them ``None``.
    """

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


class LLMClient(ABC):
    """Minimal chat interface the agent loop depends on."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send messages and return the model's response."""
        raise NotImplementedError

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        """Streaming variant of :meth:`chat`.

        The default implementation emulates streaming by yielding the whole
        response once, so clients that only implement ``chat`` keep working.
        Backends with real token streaming override this.
        """
        response = self.chat(messages, tools=tools)
        yield StreamChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            finish_reason=response.finish_reason,
            usage=response.usage,
        )
