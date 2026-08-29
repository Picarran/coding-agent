"""Interactive session for the Main Agent: keeps conversation across turns.

The workspace files provide the shared state between turns; the session carries
a bounded rolling history of (user, agent) exchanges as context.

User-facing slash commands (``/help``, ``/compact``, ``/clear``, ``/history``)
are handled here. The command set is a plain registry, so new commands are easy
to add. ``/compact`` mirrors Claude Code: it LLM-summarizes the whole history
into one compact block instead of keeping the raw turns.
"""
from __future__ import annotations

import logging

from src.core.models import AgentResult, Message
from src.llm.base import LLMClient
from src.memory.retrieval import RetrievalMemory

logger = logging.getLogger(__name__)

_COMPACT_SYSTEM = (
    "You are compressing a coding agent's conversation history. Summarize it into "
    "concise bullet points preserving: the user's goal, files changed, key "
    "conclusions, failed attempts, and the current state. Output only the bullets."
)

# name -> one-line description (the extensible command registry).
COMMANDS: dict[str, str] = {
    "/help": "Show available commands.",
    "/compact": "Summarize and compress the conversation history.",
    "/clear": "Clear the conversation history.",
    "/history": "Show the recent conversation history.",
}


class MainAgentSession:
    def __init__(
        self,
        agent,
        max_history: int = 6,
        llm: LLMClient | None = None,
        memory: RetrievalMemory | None = None,
    ) -> None:
        self._agent = agent
        self._max_history = max_history
        self._history: list[str] = []
        self._llm = llm
        self._memory = memory

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def handle_command(self, text: str) -> str | None:
        """Handle a slash command; return None if ``text`` is a normal task."""
        stripped = (text or "").strip()
        if not stripped:
            return None
        name = stripped.split()[0].lower()
        if name not in COMMANDS:
            return None
        handler = getattr(self, "_cmd_" + name[1:])
        return handler(stripped)

    def send(self, task: str) -> AgentResult:
        context_task = self._with_history(task)
        memory_note = self._memory_note(task)
        if memory_note:
            context_task = context_task + "\n\n" + memory_note
        result = self._agent.run(context_task)
        self._history.append(f"User: {task}")
        self._history.append(f"Agent: {result.summary}")
        if self._memory is not None:
            self._memory.add(task, result)
        return result

    def _memory_note(self, task: str) -> str:
        """Top-K relevant past conclusions, injected into the new task's context."""
        if self._memory is None:
            return ""
        hits = self._memory.query(task, top_k=3)
        if not hits:
            return ""
        lines = [f"- {h.summary[:200]}" for h in hits]
        return (
            "Relevant conclusions from earlier tasks in this workspace "
            "(reuse them if they help):\n" + "\n".join(lines)
        )

    def _with_history(self, task: str) -> str:
        if not self._history:
            return task
        recent = self._history[-self._max_history :]
        return task + "\n\nPrior conversation:\n" + "\n".join(recent)

    # ---- commands ----
    def _cmd_help(self, _text: str) -> str:
        lines = [f"  {k:<10} {v}" for k, v in COMMANDS.items()]
        lines.append("  /btw <q>    Ask a side question while a task runs (parallel).")
        lines.append("  exit/quit   Leave the session.")
        return "Available commands:\n" + "\n".join(lines)

    def _cmd_clear(self, _text: str) -> str:
        n = len(self._history)
        self._history = []
        return f"Cleared {n} history entries."

    def _cmd_history(self, _text: str) -> str:
        if not self._history:
            return "No history."
        recent = self._history[-self._max_history :]
        return (
            f"{len(self._history)} entries (showing last {len(recent)}):\n"
            + "\n".join(f"  {h}" for h in recent)
        )

    def _cmd_compact(self, _text: str) -> str:
        if not self._history:
            return "Nothing to compact."
        summary = self._summarize_history()
        n = len(self._history)
        self._history = [f"[compacted] {summary}"]
        return f"Compacted {n} entries into 1 summary. Inspect with /history."

    def _summarize_history(self) -> str:
        text = "\n".join(self._history)
        if self._llm is not None:
            try:
                response = self._llm.chat(
                    [
                        Message(role="system", content=_COMPACT_SYSTEM),
                        Message(role="user", content=text),
                    ]
                )
                if response and response.content:
                    return response.content
            except Exception as exc:  # noqa: BLE001 - fall back to truncation
                logger.warning("session /compact failed: %s", exc)
        # Fallback: keep the most recent exchanges joined into one line.
        return " | ".join(self._history[-4:])
