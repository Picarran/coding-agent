"""A multi-turn session: shares one ContextManager across turns in one process.

Each ``send`` appends the user's task and the resulting assistant/tool messages
to the SAME context, so the agent remembers earlier turns within the session.
This is in-process memory only; per-project disk persistence is deferred.
"""
from __future__ import annotations

from src.context.context_manager import ContextManager
from src.core.models import AgentResult
from src.loops.react_loop import ReactLoop


class Session:
    def __init__(self, loop: ReactLoop) -> None:
        self._loop = loop
        self._context = loop.new_context()

    @property
    def context(self) -> ContextManager:
        return self._context

    def send(self, task: str) -> AgentResult:
        return self._loop.run_turn(self._context, task)
