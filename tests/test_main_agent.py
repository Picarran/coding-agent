"""Tests for the Main Agent's Plan-and-Execute loop (fake planner/replanner/agents)."""
from __future__ import annotations

import unittest

from src.agents.main_agent import MainAgent
from src.core.models import AgentResult, AgentStatus
from src.llm.base import LLMResponse
from src.planning.task_plan import PlanStep, TaskPlan


class FakePlanner:
    def __init__(self, plan):
        self._plan = plan

    def plan(self, task):
        return self._plan


class FakeReplanner:
    def __init__(self, plan):
        self._plan = plan

    def replan(self, plan, reason):
        return self._plan


class FakeWorker:
    def __init__(self, results):
        self._results = list(results)
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return self._results.pop(0)


def _success(summary="ok"):
    return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary=summary)


def _failure(summary="boom"):
    return AgentResult(agent_name="w", status=AgentStatus.FAILED, summary=summary)


class _TextLLM:
    def __init__(self, content):
        self._content = content

    def chat(self, messages, tools=None):
        return LLMResponse(content=self._content, tool_calls=None, finish_reason="stop")


class MainAgentTest(unittest.TestCase):
    def test_executes_all_steps_in_order(self):
        plan = TaskPlan(
            goal="g",
            steps=[
                PlanStep(id="s1", description="step 1"),
                PlanStep(id="s2", description="step 2", dependencies=["s1"]),
            ],
        )
        worker = FakeWorker([_success("did s1"), _success("did s2")])
        agent = MainAgent(FakePlanner(plan), FakeReplanner(plan), {"coding": worker})
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.artifacts["final_state"], "COMPLETED")
        self.assertEqual(len(worker.tasks), 2)

    def test_dispatches_to_assigned_agent(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="investigate", assigned_agent="explorer")],
        )
        explorer = FakeWorker([_success("found it")])
        coding = FakeWorker([_success("unused")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"explorer": explorer, "coding": coding},
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(explorer.tasks), 1)
        self.assertEqual(len(coding.tasks), 0)

    def test_replans_on_failure(self):
        plan = TaskPlan(goal="g", steps=[PlanStep(id="s1", description="step 1")])
        retry_plan = TaskPlan(goal="g", steps=[PlanStep(id="s1-retry", description="retry")])
        worker = FakeWorker([_failure("boom"), _success("fixed")])
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(retry_plan), {"coding": worker}, max_replans=1
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.artifacts["replans"], 1)
        self.assertEqual(len(worker.tasks), 2)

    def test_fails_when_replans_exhausted(self):
        plan = TaskPlan(goal="g", steps=[PlanStep(id="s1", description="step 1")])
        retry_plan = TaskPlan(goal="g", steps=[PlanStep(id="r", description="retry")])
        worker = FakeWorker([_failure("boom1"), _failure("boom2")])
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(retry_plan), {"coding": worker}, max_replans=1
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.artifacts["replans"], 1)

    def test_synthesizes_final_answer(self):
        plan = TaskPlan(
            goal="introduce files",
            steps=[PlanStep(id="s1", description="explore", assigned_agent="explorer")],
        )
        worker = FakeWorker(
            [
                AgentResult(
                    agent_name="explorer_agent",
                    status=AgentStatus.SUCCESS,
                    summary="found",
                    artifacts={"report": {"findings": "two files"}},
                )
            ]
        )
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"explorer": worker},
            llm=_TextLLM("这里是最终回答。"),
        )
        result = agent.run("introduce files")
        self.assertEqual(result.summary, "这里是最终回答。")

    def test_injects_workspace_context(self):
        plan = TaskPlan(
            goal="g",
            steps=[
                PlanStep(id="s1", description="explore", assigned_agent="explorer"),
                PlanStep(id="s2", description="code", assigned_agent="coding"),
            ],
        )
        worker = FakeWorker(
            [
                AgentResult(
                    agent_name="explorer_agent",
                    status=AgentStatus.SUCCESS,
                    summary="found",
                    artifacts={"report": {"findings": "divide bug", "relevant_files": ["calculator.py"]}},
                ),
                AgentResult(
                    agent_name="coding_agent",
                    status=AgentStatus.SUCCESS,
                    summary="fixed",
                    artifacts={"report": {"modified_files": ["calculator.py"]}},
                ),
            ]
        )
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"explorer": worker, "coding": worker}
        )
        agent.run("g")
        self.assertIn("Workspace context", worker.tasks[1])
        self.assertIn("calculator.py", worker.tasks[1])


if __name__ == "__main__":
    unittest.main()
