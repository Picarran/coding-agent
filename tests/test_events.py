"""Tests for the unified event bus, JSONL audit logger, and metrics collector."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.core.events import (
    EventBus,
    EventType,
    JsonlAuditLogger,
    MetricsCollector,
    TraceEvent,
)


class TraceEventTest(unittest.TestCase):
    def test_to_dict_serializes(self):
        e = TraceEvent(
            EventType.LLM_CALL,
            agent_id="a",
            payload={"n": 1},
            duration_ms=1.5,
            status="ok",
        )
        d = e.to_dict()
        self.assertEqual(d["event_type"], "LLM_CALL")
        self.assertEqual(d["agent_id"], "a")
        self.assertEqual(d["payload"], {"n": 1})
        self.assertEqual(d["duration_ms"], 1.5)
        self.assertEqual(d["status"], "ok")


class EventBusTest(unittest.TestCase):
    class _Recorder:
        def __init__(self):
            self.events = []

        def on_event(self, event):
            self.events.append(event)

    class _Raiser:
        def on_event(self, event):
            raise RuntimeError("boom")

    def test_emit_reaches_all_consumers_and_isolates_failures(self):
        recorder = self._Recorder()
        bus = EventBus([recorder, self._Raiser()])
        bus.emit(TraceEvent(EventType.SESSION_START))
        self.assertEqual(len(recorder.events), 1)

    def test_emit_simple_stamps_session_id(self):
        recorder = self._Recorder()
        bus = EventBus([recorder], session_id="sess-1")
        bus.emit_simple(EventType.AGENT_START)
        self.assertEqual(recorder.events[0].session_id, "sess-1")


class JsonlAuditLoggerTest(unittest.TestCase):
    def test_appends_one_json_line_per_event(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audit.jsonl"
            audit = JsonlAuditLogger(path)
            audit.on_event(TraceEvent(EventType.SESSION_START, session_id="s"))
            audit.on_event(TraceEvent(EventType.AGENT_FINISH, status="SUCCESS"))
            audit.close()

            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["event_type"], "SESSION_START")
            self.assertEqual(first["session_id"], "s")


class MetricsCollectorTest(unittest.TestCase):
    def test_aggregates_llm_and_tool_metrics(self):
        m = MetricsCollector()
        m.on_event(TraceEvent(EventType.SESSION_START, timestamp=0.0))
        m.on_event(TraceEvent(EventType.LLM_CALL, payload={"total_tokens": 120}, duration_ms=50.0))
        m.on_event(TraceEvent(EventType.LLM_CALL, payload={"total_tokens": 80}, duration_ms=30.0))
        m.on_event(TraceEvent(EventType.PRE_TOOL_USE))
        m.on_event(TraceEvent(EventType.POST_TOOL_USE, duration_ms=10.0))
        m.on_event(TraceEvent(EventType.TOOL_ERROR))
        m.on_event(TraceEvent(EventType.REPLAN_FINISH))
        m.on_event(TraceEvent(EventType.SUBAGENT_START))
        m.on_event(TraceEvent(EventType.SESSION_END, timestamp=2.0))

        s = m.summary()
        self.assertEqual(s["llm_calls"], 2)
        self.assertEqual(s["total_tokens"], 200)
        self.assertEqual(s["llm_avg_ms"], 40.0)
        self.assertEqual(s["tool_calls"], 1)
        self.assertEqual(s["tool_errors"], 1)
        self.assertEqual(s["tool_success_rate"], 0.5)
        self.assertEqual(s["replans"], 1)
        self.assertEqual(s["subagents"], 1)
        self.assertEqual(s["duration_ms"], 2000.0)

    def test_approval_counts(self):
        m = MetricsCollector()
        m.on_event(TraceEvent(EventType.APPROVAL_REQUIRED))
        m.on_event(TraceEvent(EventType.APPROVAL_GRANTED))
        m.on_event(TraceEvent(EventType.APPROVAL_REJECTED))
        self.assertEqual(
            m.summary()["approvals"], {"required": 1, "granted": 1, "rejected": 1}
        )

    def test_delegation_parallel_counts(self):
        m = MetricsCollector()
        m.on_event(TraceEvent(EventType.DELEGATION, payload={"strategy": "parallel", "step_ids": ["a", "b"]}))
        m.on_event(TraceEvent(EventType.DELEGATION, payload={"strategy": "delegate", "step_ids": ["c"]}))
        s = m.summary()
        self.assertEqual(s["parallel_batches"], 1)
        self.assertEqual(s["parallel_steps"], 2)

    def test_route_and_escalation_counts(self):
        m = MetricsCollector()
        m.on_event(TraceEvent(EventType.ROUTE, payload={"route": "fast", "task_score": 20}))
        m.on_event(TraceEvent(EventType.ROUTE, payload={"route": "multi", "task_score": 80}))
        m.on_event(TraceEvent(EventType.ROUTE, payload={"route": "fast", "task_score": 50}))
        m.on_event(TraceEvent(EventType.ESCALATE))
        s = m.summary()
        self.assertEqual(s["fast_routes"], 2)
        self.assertEqual(s["multi_routes"], 1)
        self.assertEqual(s["escalations"], 1)
        self.assertEqual(s["avg_task_score"], 50.0)


if __name__ == "__main__":
    unittest.main()
