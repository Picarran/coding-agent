"""Core data models shared across the agent.

Keeping these dataclasses in one module makes the runtime data structures
explicit — an important property for explaining the design in an interview.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    """Coarse result status returned by an agent at the end of a run."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass
class ToolCall:
    """A structured tool-call request produced by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_json: str = "{}"


@dataclass
class Message:
    """A single chat message in the OpenAI-compatible wire format."""

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments_json},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            payload["name"] = self.name
        return payload


@dataclass
class ToolResult:
    """Normalized outcome of executing a tool.

    ``permission_denied`` marks a result that was blocked by the permission
    engine (hard DENY, or an ASK the user rejected). Unlike a normal tool error,
    this is a *terminal* signal: the loop must interrupt instead of feeding it
    back to the model as an observation to work around.
    """

    tool_call_id: str
    name: str
    content: str = ""
    error: str | None = None
    timed_out: bool = False
    permission_denied: bool = False

    def to_message(self) -> Message:
        text = f"Error: {self.error}" if self.error else self.content
        return Message(
            role="tool",
            content=text,
            tool_call_id=self.tool_call_id,
            name=self.name,
        )


@dataclass
class AgentResult:
    """Structured result returned by an agent (Structured Artifact Communication)."""

    agent_name: str
    status: AgentStatus
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    next_action: str | None = None
