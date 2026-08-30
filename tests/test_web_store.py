"""Tests for web session persistence + event replay (V3-9 refactor)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import threading
from unittest.mock import patch

from src.agents.main_agent_session import MainAgentSession
from src.core.events import EventBus, EventType, TraceEvent
from web import server as server_mod
from web.broker import EventBroker
from web.server import (
    StopRequested,
    _is_command,
    _make_checkpoint,
    _messages_to_history,
    _resolve_workspace,
)
from web.store import WebSessionStore


class StoreTest(unittest.TestCase):
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            store = WebSessionStore(Path(d))
            store.save(
                "abc",
                {
                    "id": "abc",
                    "workspace": "/tmp/ws",
                    "title": "hello",
                    "messages": [{"role": "user", "content": "hi"}],
                    "events": [{"event_type": "STEP_START", "payload": {"step_id": "s1"}}],
                },
            )
            data = store.load("abc")
        self.assertEqual(data["title"], "hello")
        self.assertEqual(data["messages"][0]["content"], "hi")
        self.assertEqual(data["events"][0]["event_type"], "STEP_START")

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = WebSessionStore(Path(d))
            self.assertIsNone(store.load("nope"))

    def test_list_returns_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            store = WebSessionStore(Path(d))
            store.save("a", {"id": "a", "workspace": "/w", "title": "first", "messages": [{"role": "user", "content": "x"}], "updated_at": "2026-01-01"})
            store.save("b", {"id": "b", "workspace": "/w", "title": "second", "messages": [], "updated_at": "2026-01-02"})
            items = store.list()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "b")  # newest first
        self.assertEqual(items[0]["message_count"], 0)
        self.assertEqual(items[1]["message_count"], 1)

    def test_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = WebSessionStore(Path(d))
            store.save("a", {"id": "a"})
            store.delete("a")
            self.assertIsNone(store.load("a"))


class EventRoundTripTest(unittest.TestCase):
    def test_from_dict_round_trip(self):
        original = TraceEvent(
            EventType.APPROVAL_PENDING,
            session_id="s",
            agent_id="a",
            payload={"approval_id": "x"},
            status="ok",
        )
        restored = TraceEvent.from_dict(original.to_dict())
        self.assertEqual(restored.event_type, EventType.APPROVAL_PENDING)
        self.assertEqual(restored.payload["approval_id"], "x")
        self.assertEqual(restored.status, "ok")


class BrokerReplayTest(unittest.TestCase):
    def test_replay_seeds_history_without_fanout(self):
        broker = EventBroker()
        q, _ = broker.subscribe()
        broker.replay(
            [TraceEvent(EventType.STEP_START, payload={"step_id": "s1"})]
        )
        self.assertEqual(len(broker.history()), 1)
        # Replay must NOT have been pushed to subscribers.
        self.assertTrue(q.empty())


class FsListTest(unittest.TestCase):
    def test_lists_subdirs_with_full_paths(self):
        from fastapi.testclient import TestClient
        from web.server import app

        client = TestClient(app)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "alpha").mkdir()
            (Path(d) / "beta").mkdir()
            (Path(d) / ".hidden").mkdir()
            res = client.get("/api/fs/list", params={"path": d})
        data = res.json()
        self.assertEqual(res.status_code, 200)
        names = [x["name"] for x in data["dirs"]]
        self.assertIn("alpha", names)
        self.assertIn("beta", names)
        self.assertNotIn(".hidden", names)
        # Full child paths so the frontend never joins paths itself.
        self.assertTrue(all(x["path"].endswith("alpha") or x["path"].endswith("beta") for x in data["dirs"]))

    def test_bad_path_returns_error(self):
        from fastapi.testclient import TestClient
        from web.server import app

        res = TestClient(app).get("/api/fs/list", params={"path": "Z:/definitely/not/real/xyz"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("error", res.json())


class CommandStopTest(unittest.TestCase):
    def test_is_command_recognizes_slash_commands(self):
        self.assertTrue(_is_command("/help"))
        self.assertTrue(_is_command("/use fix-tests"))
        self.assertFalse(_is_command("/unknown"))
        self.assertFalse(_is_command("fix the bug"))

    def test_checkpoint_raises_stop_when_requested(self):
        state = {"stop_event": threading.Event()}
        cb = _make_checkpoint(state)
        cb()  # not set -> no raise
        state["stop_event"].set()
        with self.assertRaises(StopRequested):
            cb()

    def test_run_command_emits_start_and_end(self):
        """A slash command must open a turn (SESSION_START) and reply (TURN_END),
        otherwise the frontend drops the output and never resets streaming."""
        broker = EventBroker()
        bus = EventBus([broker], session_id="testcmd")

        class _Agent:
            def run(self, task, forced_skill=None):
                raise AssertionError("agent must not run for a command")

        state = {
            "id": "testcmd",
            "bus": bus,
            "broker": broker,
            "agent_session": MainAgentSession(_Agent()),
            "mcp_manager": None,
            "messages": [],
            "title": "",
            "status": "running",
            "running": True,
            "stop_event": threading.Event(),
            "workspace": "/tmp",
            "orchestration": "auto",
            "permission": "default",
            "max_steps": 20,
            "approver": None,
            "created_at": "",
            "updated_at": "",
        }
        with tempfile.TemporaryDirectory() as d:
            with patch.object(server_mod, "store", WebSessionStore(Path(d))):
                server_mod._run_command(state, "/help")

        kinds = [e.event_type for e in broker.history()]
        self.assertIn(EventType.SESSION_START, kinds)
        self.assertIn(EventType.TURN_END, kinds)
        self.assertEqual(state["messages"][-1]["role"], "assistant")
        self.assertIn("/help", state["messages"][-1]["content"])


class HelperTest(unittest.TestCase):
    def test_messages_to_history(self):
        messages = [
            {"role": "user", "content": "task a"},
            {"role": "assistant", "content": "done a"},
        ]
        self.assertEqual(
            _messages_to_history(messages),
            ["User: task a", "Agent: done a"],
        )

    def test_resolve_workspace_absolute(self):
        p = _resolve_workspace("C:/tmp/ws")
        self.assertEqual(str(p), str(Path("C:/tmp/ws").resolve()))

    def test_resolve_workspace_relative(self):
        p = _resolve_workspace("demo_workspace")
        self.assertTrue(str(p).replace("\\", "/").endswith("/demo_workspace"))


if __name__ == "__main__":
    unittest.main()
