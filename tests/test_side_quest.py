"""Tests for the /btw side-quest machinery."""
from __future__ import annotations

import unittest

from src.core.models import AgentResult, AgentStatus
from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.planning.task_plan import PlanStep, TaskPlan
from src.session.side_quest import (
    SideQuestCoordinator,
    SideQuestQueue,
    classify_side_quest,
)
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


def _ok(summary: str) -> AgentResult:
    return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary=summary)


class ClassifyTest(unittest.TestCase):
    def test_read(self):
        self.assertEqual(classify_side_quest("which files define the divide function?"), "read")

    def test_write(self):
        self.assertEqual(classify_side_quest("create a todo list file"), "write")
        self.assertEqual(classify_side_quest("修复 calculator.py 的 bug"), "write")


class SideQuestQueueTest(unittest.TestCase):
    def test_put_poll(self):
        q = SideQuestQueue()
        q.put("a")
        q.put("b")
        self.assertEqual(q.poll(), ["a", "b"])
        self.assertEqual(q.poll(), [])
        self.assertFalse(q.pending())


class _FakeWorker:
    def __init__(self):
        self.tasks: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.tasks.append(task)
        return _ok(f"answered: {task}")


class _FakeAgent:
    def __init__(self, checkpoint):
        self._checkpoint = checkpoint
        self.running = False

    def run(self, task: str) -> AgentResult:
        self.running = True
        self._checkpoint()  # simulate the agent loop's checkpoint
        self.running = False
        return _ok("main done")


class CoordinatorTest(unittest.TestCase):
    def test_read_quest_answered_in_parallel(self):
        q = SideQuestQueue()
        q.put("which files are here?")  # read-only
        read = _FakeWorker()
        write = _FakeWorker()
        coord = SideQuestCoordinator(read, write, q)
        coord.agent = _FakeAgent(coord.checkpoint)

        result = coord.run("main task")

        self.assertEqual(result.summary, "main done")
        self.assertEqual(len(read.tasks), 1)
        self.assertEqual(len(write.tasks), 0)

    def test_write_quest_deferred_until_after_main(self):
        q = SideQuestQueue()
        q.put("create a new file")  # write
        read = _FakeWorker()
        write = _FakeWorker()
        coord = SideQuestCoordinator(read, write, q)
        coord.agent = _FakeAgent(coord.checkpoint)

        coord.run("main task")

        self.assertEqual(len(read.tasks), 0)
        self.assertEqual(len(write.tasks), 1)  # ran after the main task


class _TextLLM(LLMClient):
    def chat(self, messages, tools=None):
        return LLMResponse(content="done", tool_calls=None, finish_reason="stop")


class CheckpointHookTest(unittest.TestCase):
    def test_react_loop_calls_checkpoint(self):
        calls: list[int] = []
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(name="probe", description="p",
                           parameters={"type": "object", "properties": {}}, func=lambda **k: "ok")
        )
        loop = ReactLoop(
            _TextLLM(),
            ToolExecutor(registry),
            "sys",
            report_tool_name=None,
            max_steps=5,
            checkpoint_cb=lambda: calls.append(1),
        )
        loop.run("task")
        self.assertGreaterEqual(len(calls), 1)

    def test_main_agent_calls_checkpoint(self):
        from src.agents.main_agent import MainAgent

        calls: list[int] = []

        class Planner:
            def plan(self, task):
                return TaskPlan(goal="g", steps=[PlanStep(id="s1", description="read x", assigned_agent="explorer")])

        class Replanner:
            def replan(self, plan, reason):
                return plan

        class Worker:
            def run(self, task):
                return _ok("ok")

        agent = MainAgent(
            Planner(), Replanner(), {"explorer": Worker()}, checkpoint_cb=lambda: calls.append(1)
        )
        agent.run("g")
        self.assertGreaterEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
