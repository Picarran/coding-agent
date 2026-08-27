"""Tracing / event observation for the agent loop.

The loop emits structured events (step, tool calls, tool results, state
transitions, LLM errors) through a ``Tracer``. ``ConsoleTracer`` renders them
for the CLI; a web renderer can be added later without touching the loop.
"""
from __future__ import annotations

import json
from typing import Protocol

from src.core.models import ToolCall, ToolResult
from src.core.state import AgentState


class Tracer(Protocol):
    def on_step(self, step: int) -> None: ...

    def on_tool_call(self, call: ToolCall) -> None: ...

    def on_tool_result(self, result: ToolResult) -> None: ...

    def on_state_transition(self, old_state: AgentState, new_state: AgentState) -> None: ...

    def on_llm_error(self, attempt: int, error: Exception) -> None: ...


class NullTracer:
    """No-op tracer used when no observer is configured."""

    def on_step(self, step: int) -> None: ...

    def on_tool_call(self, call: ToolCall) -> None: ...

    def on_tool_result(self, result: ToolResult) -> None: ...

    def on_state_transition(self, old_state: AgentState, new_state: AgentState) -> None: ...

    def on_llm_error(self, attempt: int, error: Exception) -> None: ...


class ConsoleTracer:
    """Renders loop events as a human-readable CLI trace."""

    _RESULT_LIMIT = 4000

    def on_step(self, step: int) -> None:
        print(f"\n{'-' * 64}")
        print(f"Step {step}")

    def on_tool_call(self, call: ToolCall) -> None:
        print(f"  >> {call.name}")
        payload = json.dumps(call.arguments, ensure_ascii=False)
        print(f"      payload : {payload}")

    def on_tool_result(self, result: ToolResult) -> None:
        if result.error:
            print(f"      error   : {result.error}")
            return
        text = result.content
        if len(text) > self._RESULT_LIMIT:
            text = (
                text[: self._RESULT_LIMIT]
                + f"\n...[truncated {len(result.content) - self._RESULT_LIMIT} chars]"
            )
        label = "      result  : "
        print(label + text.replace("\n", "\n" + " " * len(label)))

    def on_state_transition(self, old_state: AgentState, new_state: AgentState) -> None:
        print(f"  [state] {old_state.value} -> {new_state.value}")

    def on_llm_error(self, attempt: int, error: Exception) -> None:
        print(f"  [warn] LLM error (attempt {attempt}): {error}")
