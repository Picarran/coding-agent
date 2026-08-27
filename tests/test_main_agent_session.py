"""Tests for the Main Agent interactive session (conversation across turns)."""
from __future__ import annotations

import unittest

from src.agents.main_agent_session import MainAgentSession
from src.core.models import AgentResult, AgentStatus


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


if __name__ == "__main__":
    unittest.main()
