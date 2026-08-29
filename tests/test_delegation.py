"""Tests for the DelegationPolicy (V2-5)."""
from __future__ import annotations

import unittest

from src.planning.delegation import DelegationPolicy, DelegationStrategy
from src.planning.task_plan import PlanStep


class DelegationPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = DelegationPolicy()

    def test_single_simple_read_only_is_direct(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="list the files", assigned_agent="explorer")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DIRECT)

    def test_single_simple_mutating_is_direct(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="create greet.py", assigned_agent="coding")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DIRECT)

    def test_complex_mutating_is_delegate(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="implement a caching layer", assigned_agent="coding")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_fix_signal_is_delegate(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="修复 division bug", assigned_agent="coding")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_dependent_step_is_not_simple(self):
        d = self.policy.decide(
            [PlanStep(id="s2", description="write report", assigned_agent="coding", dependencies=["s1"])]
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

    def test_mutating_leading_runs_alone_and_delegates_when_complex(self):
        c = PlanStep(id="c", description="refactor the summary module", assigned_agent="coding")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)
        self.assertEqual([s.id for s in d.steps], ["c"])

    def test_simple_mutating_leading_is_direct_and_alone(self):
        c = PlanStep(id="c", description="create greet.py", assigned_agent="coding")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        self.assertEqual(d.strategy, DelegationStrategy.DIRECT)
        self.assertEqual([s.id for s in d.steps], ["c"])

    def test_unknown_agent_treated_as_mutating(self):
        c = PlanStep(id="c", description="do a thing", assigned_agent="mystery")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        # An unknown role is serialized alone (never parallelized with reads).
        self.assertNotEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["c"])


if __name__ == "__main__":
    unittest.main()
