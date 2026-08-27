"""Tests for the Planner and Replanner (using a mock LLM that submits plans)."""
from __future__ import annotations

import json
import unittest

from src.core.models import AgentResult, AgentStatus, ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.planning.planner import Planner
from src.planning.replanner import Replanner
from src.planning.task_plan import PlanStep, PlanStepStatus, TaskPlan


class _PlanLLM(LLMClient):
    def __init__(self, plans):
        self._plans = list(plans)

    def chat(self, messages, tools=None):
        args = self._plans.pop(0) if self._plans else {"goal": "g", "steps": []}
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="p", name="submit_plan", arguments=args, arguments_json=json.dumps(args))
            ],
            finish_reason="tool_calls",
        )


class PlannerTest(unittest.TestCase):
    def test_plan_builds_steps(self):
        llm = _PlanLLM(
            [
                {
                    "goal": "do X",
                    "steps": [
                        {"id": "s1", "description": "inspect"},
                        {"id": "s2", "description": "fix", "dependencies": ["s1"]},
                    ],
                }
            ]
        )
        plan = Planner(llm).plan("do X")
        self.assertEqual(plan.goal, "do X")
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[1].dependencies, ["s1"])

    def test_plan_falls_back_to_single_step(self):
        llm = _PlanLLM([])  # returns an empty steps list
        plan = Planner(llm).plan("do Y")
        self.assertEqual(len(plan.steps), 1)


class ReplannerTest(unittest.TestCase):
    def test_replan_preserves_completed(self):
        s1 = PlanStep(
            id="s1",
            description="done",
            status=PlanStepStatus.COMPLETED,
            result=AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary="did s1"),
        )
        s2 = PlanStep(id="s2", description="pending")
        plan = TaskPlan(goal="g", steps=[s1, s2])

        llm = _PlanLLM([{"goal": "g", "steps": [{"id": "s2b", "description": "retry"}]}])
        new_plan = Replanner(llm).replan(plan, "s2 failed")

        self.assertEqual([s.id for s in new_plan.steps], ["s1", "s2b"])
        self.assertEqual(new_plan.steps[0].status, PlanStepStatus.COMPLETED)
        self.assertEqual(new_plan.steps[1].status, PlanStepStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
