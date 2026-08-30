"""Tests for session persistence (V3-10): store round-trip + resume + commands."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agents.main_agent_session import MainAgentSession
from src.core.models import AgentResult, AgentStatus
from src.session.store import default_session_path, load_session, save_session


class StoreTest(unittest.TestCase):
    def test_missing_file_loads_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            data = load_session(Path(d) / "nope.json")
        self.assertEqual(data["history"], [])
        self.assertEqual(data["last_plan"], [])

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session.json"
            save_session(p, ["User: a", "Agent: b"], [{"id": "s1", "status": "completed"}])
            data = load_session(p)
        self.assertEqual(data["history"], ["User: a", "Agent: b"])
        self.assertEqual(data["last_plan"], [{"id": "s1", "status": "completed"}])

    def test_corrupt_file_degrades_to_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session.json"
            p.write_text("{not json", encoding="utf-8")
            data = load_session(p)
        self.assertEqual(data["history"], [])
        self.assertEqual(data["last_plan"], [])

    def test_default_session_path(self):
        self.assertEqual(
            default_session_path(Path("/tmp/ws")),
            Path("/tmp/ws") / ".coding-agent" / "session.json",
        )


class _PlanAgent:
    """Returns a result carrying a plan snapshot, to exercise last_plan capture."""

    def run(self, task, forced_skill=None):
        return AgentResult(
            agent_name="main_agent",
            status=AgentStatus.SUCCESS,
            summary="ok",
            artifacts={
                "plan": [
                    {"id": "s1", "description": "read", "status": "completed", "summary": "r"}
                ]
            },
        )


class SessionResumeTest(unittest.TestCase):
    def test_set_history_seeds_conversation(self):
        session = MainAgentSession(_PlanAgent())
        session.set_history(["User: earlier", "Agent: done"])
        session.send("new task")
        self.assertEqual(session.history[:2], ["User: earlier", "Agent: done"])
        self.assertEqual(len(session.history), 4)  # 2 seeded + 2 new

    def test_send_captures_last_plan(self):
        session = MainAgentSession(_PlanAgent())
        session.send("do it")
        self.assertEqual(len(session.last_plan), 1)
        self.assertEqual(session.last_plan[0]["id"], "s1")

    def test_session_command_shows_summary(self):
        session = MainAgentSession(_PlanAgent())
        session.send("do it")
        out = session.handle_command("/session")
        self.assertIn("history entries", out)
        self.assertIn("s1", out)

    def test_new_command_clears_history_and_plan(self):
        session = MainAgentSession(_PlanAgent())
        session.send("a")
        out = session.handle_command("/new")
        self.assertEqual(session.history, [])
        self.assertEqual(session.last_plan, [])
        self.assertIn("fresh session", out)

    def test_help_lists_session_commands(self):
        out = MainAgentSession(_PlanAgent()).handle_command("/help")
        self.assertIn("/session", out)
        self.assertIn("/new", out)


if __name__ == "__main__":
    unittest.main()
