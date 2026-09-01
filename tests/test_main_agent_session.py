"""Tests for the Main Agent interactive session (conversation across turns)."""
from __future__ import annotations

import unittest

from src.agents.main_agent_session import MainAgentSession
from src.core.events import EventType
from src.core.models import AgentResult, AgentStatus
from src.llm.base import LLMResponse


class FakeAgent:
    def __init__(self):
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return AgentResult(
            agent_name="main_agent", status=AgentStatus.SUCCESS, summary=f"done: {task}"
        )


class MainAgentSessionTest(unittest.TestCase):
    def test_passes_prior_conversation_to_later_turns(self):
        agent = FakeAgent()
        session = MainAgentSession(agent)
        session.send("task one")
        session.send("task two")

        self.assertIn("task one", agent.tasks[1])
        self.assertIn("Prior conversation", agent.tasks[1])

    def test_first_turn_has_no_history(self):
        agent = FakeAgent()
        session = MainAgentSession(agent)
        session.send("task one")
        self.assertEqual(agent.tasks[0], "task one")


class _TextLLM:
    def __init__(self, content="compact summary"):
        self._content = content
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self._content, tool_calls=None, finish_reason="stop")


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit_simple(self, event_type, agent_id=None, payload=None, duration_ms=None, status=None):
        self.events.append((event_type, payload or {}))


class SessionCommandTest(unittest.TestCase):
    def test_help_lists_commands(self):
        out = MainAgentSession(FakeAgent()).handle_command("/help")
        self.assertIn("/compact", out)
        self.assertIn("/clear", out)
        self.assertIn("/history", out)

    def test_non_command_returns_none(self):
        self.assertIsNone(MainAgentSession(FakeAgent()).handle_command("fix the bug"))

    def test_clear_empties_history(self):
        session = MainAgentSession(FakeAgent())
        session.send("a")
        session.send("b")
        out = session.handle_command("/clear")
        self.assertEqual(session.history, [])
        self.assertIn("Cleared", out)

    def test_history_shows_entries(self):
        session = MainAgentSession(FakeAgent())
        session.send("task one")
        self.assertIn("task one", session.handle_command("/history"))

    def test_compact_replaces_history_with_summary(self):
        llm = _TextLLM("compact summary")
        session = MainAgentSession(FakeAgent(), llm=llm)
        session.send("a")
        session.send("b")
        out = session.handle_command("/compact")
        self.assertEqual(len(session.history), 1)
        self.assertIn("compact summary", session.history[0])
        self.assertEqual(len(llm.calls), 1)

    def test_compact_emits_context_compact_with_removed_chars(self):
        llm = _TextLLM("summary")
        bus = _FakeBus()
        session = MainAgentSession(FakeAgent(), llm=llm, event_bus=bus)
        session.send("a")
        session.send("b")
        session.handle_command("/compact")
        compacts = [(t, p) for t, p in bus.events if t == EventType.CONTEXT_COMPACT]
        self.assertEqual(len(compacts), 1)
        payload = compacts[0][1]
        self.assertGreater(payload["removed_chars"], 0)
        self.assertEqual(payload["source"], "session")


if __name__ == "__main__":
    unittest.main()
