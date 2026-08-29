"""Tests for the DelegationPolicy (V2-5): metric scoring + routing."""
from __future__ import annotations

import unittest

from src.planning.delegation import DelegationPolicy, DelegationStrategy
from src.planning.task_plan import PlanStep


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.policy = DelegationPolicy()

    def test_read_step_scores_low(self):
        s = PlanStep(id="s1", description="list the files", assigned_agent="explorer")
        self.assertLess(self.policy.score(s), 50)

    def test_simple_write_scores_low(self):
        s = PlanStep(id="s1", description="创建 greet.py，定义 greet(name)", assigned_agent="coding")
        self.assertLess(self.policy.score(s), 50)

    def test_fix_scores_high(self):
        s = PlanStep(
            id="s1",
            description="修复 calculator.py 的除法 bug，让 test_calculator.py 全部通过",
            assigned_agent="coding",
        )
        self.assertGreaterEqual(self.policy.score(s), 50)

    def test_refactor_scores_high(self):
        s = PlanStep(
            id="s1",
            description="把 arith.py 的 triple 移到 advanced.py，更新 app.py 的 import",
            assigned_agent="coding",
        )
        self.assertGreaterEqual(self.policy.score(s), 50)

    def test_implement_scores_high(self):
        s = PlanStep(id="s1", description="implement a caching layer", assigned_agent="coding")
        self.assertGreaterEqual(self.policy.score(s), 50)

    def test_more_files_scores_higher(self):
        a = PlanStep(id="a", description="read the code", assigned_agent="explorer")
        b = PlanStep(id="b", description="read a.py b.py c.py", assigned_agent="explorer")
        self.assertGreater(self.policy.score(b), self.policy.score(a))

    def test_write_scores_higher_than_read(self):
        read = PlanStep(id="r", description="check the code", assigned_agent="explorer")
        write = PlanStep(id="w", description="check the code", assigned_agent="coding")
        self.assertGreater(self.policy.score(write), self.policy.score(read))


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.policy = DelegationPolicy()

    def test_single_simple_read_only_is_direct(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="list the files", assigned_agent="explorer")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DIRECT)
        self.assertIsNotNone(d.complexity_score)

    def test_simple_write_is_direct(self):
        d = self.policy.decide(
            [PlanStep(id="s1", description="创建 greet.py，定义 greet(name)", assigned_agent="coding")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DIRECT)

    def test_fix_is_delegate(self):
        d = self.policy.decide(
            [
                PlanStep(
                    id="s1",
                    description="修复 calculator.py 的除法 bug，让 test_calculator.py 全部通过",
                    assigned_agent="coding",
                )
            ]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_refactor_is_delegate(self):
        d = self.policy.decide(
            [
                PlanStep(
                    id="s1",
                    description="把 arith.py 的 triple 移到 advanced.py，更新 app.py import",
                    assigned_agent="coding",
                )
            ]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_two_read_only_steps_parallel(self):
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        b = PlanStep(id="b", description="read mod_b", assigned_agent="test")
        d = self.policy.decide([a, b])
        self.assertEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["a", "b"])
        self.assertIsNone(d.complexity_score)

    def test_parallel_stops_at_first_mutating_step(self):
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        b = PlanStep(id="b", description="read mod_b", assigned_agent="explorer")
        c = PlanStep(id="c", description="write summary", assigned_agent="coding")
        d = self.policy.decide([a, b, c])
        self.assertEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["a", "b"])

    def test_mutating_leading_runs_alone(self):
        c = PlanStep(id="c", description="refactor the summary module", assigned_agent="coding")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)
        self.assertEqual([s.id for s in d.steps], ["c"])

    def test_unknown_agent_treated_as_mutating(self):
        c = PlanStep(id="c", description="do a thing", assigned_agent="mystery")
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        d = self.policy.decide([c, a])
        # An unknown role is serialized alone (never parallelized with reads).
        self.assertNotEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["c"])


class DirectDisabledTest(unittest.TestCase):
    """THOROUGH mode: DIRECT is disabled, but PARALLEL (read-only) stays on."""

    def test_direct_disabled_forces_delegate(self):
        policy = DelegationPolicy(direct_enabled=False)
        d = policy.decide(
            [PlanStep(id="s1", description="list the files", assigned_agent="explorer")]
        )
        self.assertEqual(d.strategy, DelegationStrategy.DELEGATE)

    def test_direct_disabled_still_parallelizes_reads(self):
        policy = DelegationPolicy(direct_enabled=False)
        a = PlanStep(id="a", description="read mod_a", assigned_agent="explorer")
        b = PlanStep(id="b", description="read mod_b", assigned_agent="explorer")
        d = policy.decide([a, b])
        self.assertEqual(d.strategy, DelegationStrategy.PARALLEL)
        self.assertEqual([s.id for s in d.steps], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
