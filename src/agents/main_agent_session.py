"""Interactive session for the Main Agent: keeps conversation across turns.

Each turn re-plans with the prior conversation as context; the workspace files
provide the shared state between turns.
"""
from __future__ import annotations

from src.core.models import AgentResult


class MainAgentSession:
    def __init__(self, agent, max_history: int = 6) -> None:
        self._agent = agent
        self._max_history = max_history
        self._history: list[str] = []

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def send(self, task: str) -> AgentResult:
        context_task = self._with_history(task)
        result = self._agent.run(context_task)
        self._history.append(f"User: {task}")
        self._history.append(f"Agent: {result.summary}")
        return result

    def _with_history(self, task: str) -> str:
        if not self._history:
            return task
        recent = self._history[-self._max_history :]
        return task + "\n\nPrior conversation:\n" + "\n".join(recent)
