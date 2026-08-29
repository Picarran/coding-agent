"""Tests for RetrievalMemory (V2-7)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agents.main_agent_session import MainAgentSession
from src.core.models import AgentResult, AgentStatus
from src.memory.retrieval import RetrievalMemory, extract_keywords


def _result(summary: str) -> AgentResult:
    return AgentResult(agent_name="a", status=AgentStatus.SUCCESS, summary=summary)


class ExtractKeywordsTest(unittest.TestCase):
    def test_english_stopwords_filtered(self):
        kws = extract_keywords("fix the calculator division bug")
        self.assertIn("calculator", kws)
        self.assertIn("division", kws)
        self.assertNotIn("the", kws)

    def test_cjk_bigrams_extracted(self):
        kws = extract_keywords("修复除法错误")
        self.assertIn("修复", kws)
        self.assertIn("错误", kws)


class RetrievalMemoryTest(unittest.TestCase):
    def test_add_and_query_returns_relevant(self):
        mem = RetrievalMemory()
        mem.add("fix the calculator division bug", _result("changed calculator.py to float division"))
        mem.add("create a greeting module", _result("added greet.py"))

        hits = mem.query("calculator division is still wrong")
        self.assertEqual(len(hits), 1)
        self.assertIn("float division", hits[0].summary)

    def test_query_empty_when_no_overlap(self):
        mem = RetrievalMemory()
        mem.add("create greet.py", _result("added greet.py"))
        self.assertEqual(mem.query("fix the database query"), [])

    def test_save_load_roundtrip(self):
        mem = RetrievalMemory()
        mem.add("fix the calculator division bug", _result("changed calculator.py"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "memory.json"
            mem.save(path)
            loaded = RetrievalMemory.load(path)
        self.assertEqual(len(loaded), 1)
        self.assertIn("calculator", loaded.query("calculator")[0].summary)


class _FakeAgent:
    def __init__(self):
        self.tasks: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.tasks.append(task)
        return _result("done")


class SessionMemoryTest(unittest.TestCase):
    def test_session_injects_relevant_memory_note(self):
        mem = RetrievalMemory()
        mem.add("fix the calculator division bug", _result("changed calculator.py to float division"))
        agent = _FakeAgent()
        session = MainAgentSession(agent, llm=None, memory=mem)
        session.send("calculator division is still wrong")

        self.assertIn("Relevant conclusions", agent.tasks[0])
        self.assertIn("float division", agent.tasks[0])

    def test_session_indexes_results(self):
        mem = RetrievalMemory()
        session = MainAgentSession(_FakeAgent(), llm=None, memory=mem)
        session.send("create a greeting module")
        self.assertEqual(len(mem), 1)


if __name__ == "__main__":
    unittest.main()
