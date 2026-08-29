"""Tests for DelegationPolicy (V2-5.4): the parallel read-only scheduler."""
from __future__ import annotations

import unittest

from src.planning.delegation import DelegationPolicy, DelegationStrategy
from src.planning.task_plan import PlanStep


class DelegationPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = DelegationPolicy()

    def test_single_read_only_step_delegates(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="read mod_a", assigned_agent="explorer")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)
        self.assertEqual([s.id for s in d.steps], ["s1"])

    def test_single_mutating_step_delegates(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="write summary", assigned_agent="coding")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_two_read_only_steps_parallel(self):
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        b = PlanStep(id="b", description="read mod_b", assigned_agent="test")
        d = self.policy.decide([a, b])
        self.assertEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["a", "b"])

    def test_parallel_stops_at_first_mutating_step(self):
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        b = PlanStep(id="b", description="read mod_b", assigned_agent="explorer")
        c = PlanStep(id="c", description="write summary", assigned_agent="coding")
        d = self.policy.decide([a, b, c])
        self.assertEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["a", "b"])

    def test_mutating_leading_runs_alone(self):
        c = PlanStep(id="c", description="write summary", assigned_agent="coding")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)
        self.assertEqual([s.id for s in d.steps], ["c"])

    def test_unknown_agent_treated_as_mutating(self):
        c = PlanStep(id="c", description="do a thing", assigned_agent="mystery")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)
        self.assertEqual([s.id for s in d.steps], ["c"])


if __name__ == "__main__":
    unittest.main()
