"""Tests for the Main Agent's Plan-and-Execute loop (fake planner/replanner/agents)."""
from __future__ import annotations

import unittest

from src.agents.main_agent import MainAgent
from src.core.models import AgentResult, AgentStatus
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


if __name__ == "__main__":
    unittest.main()
