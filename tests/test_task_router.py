"""Tests for the TaskRouter (V2-5.4): task_score + fast-first cascade."""
from __future__ import annotations

import unittest

from src.core.events import EventBus
from src.core.models import AgentResult, AgentStatus
from src.task_router import LOW, HIGH, TaskRouter, task_score


def _result(status: AgentStatus, summary: str = "ok") -> AgentResult:
    return AgentResult(agent_name="w", status=status, summary=summary)


class _Recorder:
    def __init__(self, result: AgentResult):
        self._result = result
        self.tasks: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.tasks.append(task)
        return self._result


class TaskScoreTest(unittest.TestCase):
    def test_simple_create_scores_below_low(self):
        self.assertLess(task_score("创建 greet.py，定义函数 greet(name)，返回问候语。"), LOW)

    def test_single_file_fix_with_test_scores_in_band(self):
        score = task_score("修复 calculator.py 的除法 bug，让 test_calculator.py 里的测试全部通过。")
        self.assertGreaterEqual(score, LOW)
        self.assertLess(score, HIGH)

    def test_multi_file_refactor_scores_above_high(self):
        score = task_score(
            "把 arith.py 的 triple 移到 advanced.py，更新 app.py 的 import，并确保 test_app.py 全部通过。"
        )
        self.assertGreaterEqual(score, HIGH)

    def test_more_files_scores_higher(self):
        a = task_score("read the code")
        b = task_score("read a.py b.py c.py d.py")
        self.assertGreater(b, a)


class TaskRouterTest(unittest.TestCase):
    def _router(self, single_result, multi_result, low=0, high=200):
        single = _Recorder(single_result)
        multi = _Recorder(multi_result)
        router = TaskRouter(single, multi, llm=None, event_bus=None, low=low, high=high)
        return single, multi, router

    def test_fast_route(self):
        single, multi, router = self._router(_result(AgentStatus.SUCCESS), _result(AgentStatus.SUCCESS), low=200)
        result = router.run("do something")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(single.tasks), 1)
        self.assertEqual(len(multi.tasks), 0)

    def test_multi_route(self):
        single, multi, router = self._router(_result(AgentStatus.SUCCESS), _result(AgentStatus.SUCCESS), high=0)
        result = router.run("do something")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(single.tasks), 0)
        self.assertEqual(len(multi.tasks), 1)

    def test_band_escalates_on_failed_single(self):
        single, multi, router = self._router(
            _result(AgentStatus.FAILED, "boom"), _result(AgentStatus.SUCCESS), low=0, high=200
        )
        result = router.run("do something")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(single.tasks), 1)  # tried fast first
        self.assertEqual(len(multi.tasks), 1)  # then escalated

    def test_band_keeps_successful_single(self):
        single, multi, router = self._router(
            _result(AgentStatus.SUCCESS), _result(AgentStatus.SUCCESS), low=0, high=200
        )
        result = router.run("do something")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(single.tasks), 1)
        self.assertEqual(len(multi.tasks), 0)


class _RecordingBusConsumer:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class TaskRouterEventTest(unittest.TestCase):
    def test_emits_route_and_escalate_events(self):
        from src.core.events import EventType

        bus = EventBus()
        consumer = _RecordingBusConsumer()
        bus.subscribe(consumer)
        single = _Recorder(_result(AgentStatus.FAILED, "boom"))
        multi = _Recorder(_result(AgentStatus.SUCCESS))
        router = TaskRouter(single, multi, llm=None, event_bus=bus, low=0, high=200)
        router.run("do something")

        kinds = [e.event_type for e in consumer.events]
        self.assertIn(EventType.ROUTE, kinds)
        self.assertIn(EventType.ESCALATE, kinds)


if __name__ == "__main__":
    unittest.main()
