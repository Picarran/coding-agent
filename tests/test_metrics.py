"""Tests for performance metrics (V3-11): cost, snapshot/reset, SessionMetrics."""
from __future__ import annotations

import unittest

from src.core.events import EventType, MetricsCollector, SessionMetrics, TraceEvent


def _llm_call(prompt: int = 0, completion: int = 0) -> TraceEvent:
    return TraceEvent(
        EventType.LLM_CALL,
        payload={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    )


class CostTest(unittest.TestCase):
    def test_cost_computed_from_token_breakdown(self):
        c = MetricsCollector()
        c.on_event(_llm_call(prompt=1_000_000, completion=0))
        s = c.summary()
        self.assertEqual(s["prompt_tokens"], 1_000_000)
        self.assertAlmostEqual(s["cost_usd"], 0.27, places=6)

        c.on_event(_llm_call(prompt=0, completion=1_000_000))
        s = c.summary()
        self.assertAlmostEqual(s["cost_usd"], 1.37, places=6)

    def test_zero_tokens_cost_is_zero(self):
        self.assertEqual(MetricsCollector().summary()["cost_usd"], 0.0)


class SnapshotResetTest(unittest.TestCase):
    def test_snapshot_does_not_reset(self):
        c = MetricsCollector()
        c.on_event(_llm_call(prompt=100, completion=10))
        first = c.snapshot()
        self.assertEqual(first["prompt_tokens"], 100)
        self.assertEqual(c.summary()["prompt_tokens"], 100)  # still there

    def test_reset_clears_counters(self):
        c = MetricsCollector()
        c.on_event(_llm_call(prompt=100, completion=10))
        c.reset()
        s = c.summary()
        self.assertEqual(s["prompt_tokens"], 0)
        self.assertEqual(s["total_tokens"], 0)
        self.assertEqual(s["cost_usd"], 0.0)


class SessionMetricsTest(unittest.TestCase):
    def test_finish_task_segments_and_aggregates(self):
        sm = SessionMetrics()
        sm.on_event(_llm_call(prompt=1000, completion=100))
        sm.finish_task("task A")
        sm.on_event(_llm_call(prompt=500, completion=50))
        sm.finish_task("task B")

        tasks = sm.tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["label"], "task A")
        self.assertEqual(tasks[0]["prompt_tokens"], 1000)
        self.assertEqual(tasks[1]["prompt_tokens"], 500)
        # Aggregate keeps the running total.
        self.assertEqual(sm.summary()["prompt_tokens"], 1500)


if __name__ == "__main__":
    unittest.main()
