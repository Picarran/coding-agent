"""Termination policy: repeated-action detection and consecutive-error tracking.

These are deterministic guards against loops that get stuck — they complement
the explicit state machine and are checked in code, not by asking the model to
"please stop".
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.core.models import ToolCall, ToolResult


@dataclass
class TerminationConfig:
    max_steps: int = 20
    repeated_action_warn: int = 3
    repeated_action_limit: int = 6
    consecutive_error_warn: int = 3
    consecutive_error_limit: int = 6


class TerminationMonitor:
    """Tracks recent actions and consecutive tool errors for a single loop run."""

    def __init__(self, config: TerminationConfig | None = None) -> None:
        self._config = config or TerminationConfig()
        self._recent_actions: list[tuple[str, str]] = []
        self._consecutive_errors = 0
        self._max_recent = 32

    def record_tool_call(self, call: ToolCall) -> None:
        self._recent_actions.append(self._action_key(call))
        if len(self._recent_actions) > self._max_recent:
            self._recent_actions = self._recent_actions[-self._max_recent :]

    def record_tool_result(self, result: ToolResult) -> None:
        if result.error:
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 0

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    def repeated_action_count(self) -> int:
        if not self._recent_actions:
            return 0
        last = self._recent_actions[-1]
        count = 0
        for action in reversed(self._recent_actions):
            if action == last:
                count += 1
            else:
                break
        return count

    def should_warn_repetition(self) -> bool:
        return self.repeated_action_count() >= self._config.repeated_action_warn

    def should_terminate_repetition(self) -> bool:
        return self.repeated_action_count() >= self._config.repeated_action_limit

    def should_warn_consecutive_errors(self) -> bool:
        return self._consecutive_errors >= self._config.consecutive_error_warn

    def should_terminate_consecutive_errors(self) -> bool:
        return self._consecutive_errors >= self._config.consecutive_error_limit

    @staticmethod
    def _action_key(call: ToolCall) -> tuple[str, str]:
        args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        return (call.name, args)
