"""Tests for the web workspace plumbing (V3-9): EventBroker + WebApprover."""
from __future__ import annotations

import threading
import time
import unittest

from src.core.events import EventType, TraceEvent
from web.broker import EventBroker, WebApprover


class BrokerTest(unittest.TestCase):
    def test_publish_fans_out_and_replays(self):
        broker = EventBroker()
        q1, history1 = broker.subscribe()
        self.assertEqual(history1, [])

        e1 = TraceEvent(EventType.STEP_START, payload={"step_id": "s1"})
        broker.publish(e1)
        q2, history2 = broker.subscribe()  # late joiner sees replay
        e2 = TraceEvent(EventType.STEP_START, payload={"step_id": "s2"})
        broker.publish(e2)

        self.assertEqual(q1.get(timeout=2).payload["step_id"], "s1")
        self.assertEqual(q1.get(timeout=2).payload["step_id"], "s2")
        self.assertEqual([e.payload["step_id"] for e in history2], ["s1"])
        self.assertEqual(q2.get(timeout=2).payload["step_id"], "s2")
        broker.unsubscribe(q1)
        broker.unsubscribe(q2)

    def test_on_event_delegates_to_publish(self):
        broker = EventBroker()
        q, _ = broker.subscribe()
        broker.on_event(TraceEvent(EventType.TOOL_ERROR, payload={"x": 1}))
        self.assertEqual(q.get(timeout=2).event_type, EventType.TOOL_ERROR)


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ApproverTest(unittest.TestCase):
    def test_approve_returns_true_after_resolve(self):
        captured: list[TraceEvent] = []
        approver = WebApprover(publish=captured.append)
        holder: dict = {}

        def call():
            holder["r"] = approver("execute_command: git push")

        t = threading.Thread(target=call)
        t.start()
        self.assertTrue(_wait_until(lambda: len(captured) == 1))
        approval_id = captured[0].payload["approval_id"]
        self.assertEqual(captured[0].event_type, EventType.APPROVAL_PENDING)
        self.assertTrue(approver.resolve(approval_id, True))
        t.join(timeout=2)
        self.assertIs(holder["r"], True)

    def test_reject_returns_false(self):
        captured: list[TraceEvent] = []
        approver = WebApprover(publish=captured.append)
        holder: dict = {}

        def call():
            holder["r"] = approver("patch_file: a.py")

        t = threading.Thread(target=call)
        t.start()
        self.assertTrue(_wait_until(lambda: len(captured) == 1))
        approver.resolve(captured[0].payload["approval_id"], False)
        t.join(timeout=2)
        self.assertIs(holder["r"], False)

    def test_always_allow_remembers_tool(self):
        captured: list[TraceEvent] = []
        approver = WebApprover(publish=captured.append)
        holder: dict = {}

        def call():
            holder["r"] = approver("execute_command: rm x")

        t = threading.Thread(target=call)
        t.start()
        self.assertTrue(_wait_until(lambda: len(captured) == 1))
        approver.resolve(captured[0].payload["approval_id"], True, always=True)
        t.join(timeout=2)
        self.assertIs(holder["r"], True)

        # Same tool is auto-approved without a new pending approval.
        self.assertTrue(approver("execute_command: ls"))
        self.assertEqual(len(captured), 1)

        # A different tool still asks.
        holder2: dict = {}

        def call2():
            holder2["r"] = approver("mcp__x__y: {}")

        t2 = threading.Thread(target=call2)
        t2.start()
        self.assertTrue(_wait_until(lambda: len(captured) == 2))
        approver.resolve(captured[1].payload["approval_id"], False)
        t2.join(timeout=2)
        self.assertIs(holder2["r"], False)

    def test_timeout_auto_rejects(self):
        captured: list[TraceEvent] = []
        approver = WebApprover(publish=captured.append, timeout=0.1)
        self.assertFalse(approver("execute_command: x"))
        self.assertEqual(len(captured), 1)

    def test_resolve_unknown_returns_false(self):
        approver = WebApprover(publish=lambda e: None)
        self.assertFalse(approver.resolve("missing", True))


if __name__ == "__main__":
    unittest.main()
