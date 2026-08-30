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
from src.skills.registry import SkillRegistry

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
    "/session": "Show the current session (history + last plan).",
    "/new": "Start a fresh session (clear history + last plan).",
    "/skills": "List available skills.",
    "/skill": "Show a skill's steps (usage: /skill <name>).",
    "/use": "Force the next task to use a skill (usage: /use <name>).",
}


class MainAgentSession:
    def __init__(
        self,
        agent,
        max_history: int = 6,
        llm: LLMClient | None = None,
        memory: RetrievalMemory | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._agent = agent
        self._max_history = max_history
        self._history: list[str] = []
        self._last_plan: list[dict] = []
        self._llm = llm
        self._memory = memory
        self._skill_registry = skill_registry
        self._forced_skill: str | None = None

    @property
    def history(self) -> list[str]:
        return list(self._history)

    @property
    def last_plan(self) -> list[dict]:
        return list(self._last_plan)

    def set_history(self, entries: list[str]) -> None:
        """Seed the history (used to resume a persisted session on startup)."""
        self._history = list(entries)

    def set_last_plan(self, steps: list[dict]) -> None:
        """Seed the last-plan snapshot (used to resume a persisted session)."""
        self._last_plan = list(steps)

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
        forced = self._forced_skill
        self._forced_skill = None  # one-shot
        if forced is not None:
            result = self._agent.run(context_task, forced_skill=forced)
        else:
            result = self._agent.run(context_task)
        self._history.append(f"User: {task}")
        self._history.append(f"Agent: {result.summary}")
        # Snapshot the last plan for session persistence/display.
        self._last_plan = result.artifacts.get("plan") or []
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

    def _cmd_session(self, _text: str) -> str:
        lines = [f"{len(self._history)} history entries, {len(self._last_plan)} last-plan step(s)."]
        if self._last_plan:
            lines.append("Last plan:")
            for s in self._last_plan[:10]:
                lines.append(
                    f"  {s.get('id', '?')} [{s.get('status', '?')}] {s.get('description', '')}"
                )
        return "\n".join(lines)

    def _cmd_new(self, _text: str) -> str:
        n = len(self._history)
        self._history = []
        self._last_plan = []
        return f"Started a fresh session (cleared {n} history entries)."

    def _cmd_history(self, _text: str) -> str:
        if not self._history:
            return "No history."
        recent = self._history[-self._max_history :]
        return (
            f"{len(self._history)} entries (showing last {len(recent)}):\n"
            + "\n".join(f"  {h}" for h in recent)
        )

    def _cmd_skills(self, _text: str) -> str:
        if self._skill_registry is None or not self._skill_registry.all():
            return "No skills available."
        lines = [f"  {s.name:<18} {s.description}" for s in self._skill_registry.all()]
        return "Available skills:\n" + "\n".join(lines)

    def _cmd_skill(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /skill <name>"
        name = parts[1]
        skill = self._skill_registry.get(name) if self._skill_registry else None
        if skill is None:
            return f"Unknown skill: {name}"
        lines = [f"Skill: {skill.name}", f"Description: {skill.description}", "Steps:"]
        lines += [f"  {i + 1}. [{s.agent}] {s.description}" for i, s in enumerate(skill.steps)]
        guidance = skill.guidance()
        if guidance:
            lines.append("Guidance:\n" + guidance)
        return "\n".join(lines)

    def _cmd_use(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /use <skill-name>  (forces the next task to use this skill)"
        name = parts[1]
        if self._skill_registry is None or self._skill_registry.get(name) is None:
            return f"Unknown skill: {name}"
        self._forced_skill = name
        return f"Next task will use skill '{name}'."

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
