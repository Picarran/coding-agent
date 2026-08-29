"""Tests for TaskPlan / PlanStep scheduling logic."""
from __future__ import annotations

import unittest

from src.planning.task_plan import PlanStep, PlanStepStatus, TaskPlan


class TaskPlanTest(unittest.TestCase):
    def test_next_runnable_respects_dependencies(self):
        s1 = PlanStep(id="s1", description="a")
        s2 = PlanStep(id="s2", description="b", dependencies=["s1"])
        plan = TaskPlan(goal="g", steps=[s1, s2])
        self.assertEqual(plan.next_runnable_step().id, "s1")
        s1.status = PlanStepStatus.COMPLETED
        self.assertEqual(plan.next_runnable_step().id, "s2")

    def test_is_complete(self):
        s1 = PlanStep(id="s1", description="a")
        plan = TaskPlan(goal="g", steps=[s1])
        self.assertFalse(plan.is_complete())
        s1.status = PlanStepStatus.COMPLETED
        self.assertTrue(plan.is_complete())

    def test_next_runnable_none_when_dependency_failed(self):
        s1 = PlanStep(id="s1", description="a")
        s2 = PlanStep(id="s2", description="b", dependencies=["s1"])
        plan = TaskPlan(goal="g", steps=[s1, s2])
        s1.status = PlanStepStatus.FAILED
        self.assertIsNone(plan.next_runnable_step())

    def test_runnable_steps_returns_full_batch(self):
        s1 = PlanStep(id="s1", description="a")
        s2 = PlanStep(id="s2", description="b")
        s3 = PlanStep(id="s3", description="c", dependencies=["s1"])
        plan = TaskPlan(goal="g", steps=[s1, s2, s3])
        self.assertEqual([s.id for s in plan.runnable_steps()], ["s1", "s2"])


if __name__ == "__main__":
    unittest.main()
