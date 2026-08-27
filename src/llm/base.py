"""LLM client abstraction.

Upper-layer agent code depends only on this interface, never on DeepSeek's
specific request details. This keeps the model backend swappable and the agent
loop easy to test with a mock.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.core.models import Message, ToolCall


@dataclass
class LLMResponse:
    """A single model response: text, tool calls, or both."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None


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
