"""Tests for the termination monitor (repeated action + consecutive errors)."""
from __future__ import annotations

import unittest

from src.core.models import ToolCall, ToolResult
from src.core.termination import TerminationConfig, TerminationMonitor


def _call(name, args):
    return ToolCall(id="x", name=name, arguments=args, arguments_json="{}")


def _ok():
    return ToolResult(tool_call_id="x", name="t", content="ok")


def _err():
    return ToolResult(tool_call_id="x", name="t", error="boom")


class TerminationMonitorTest(unittest.TestCase):
    def test_repeated_action_detection(self):
        m = TerminationMonitor(TerminationConfig(repeated_action_warn=3, repeated_action_limit=6))
        for _ in range(2):
            m.record_tool_call(_call("read_file", {"path": "a.py"}))
        self.assertFalse(m.should_warn_repetition())
        m.record_tool_call(_call("read_file", {"path": "a.py"}))
        self.assertTrue(m.should_warn_repetition())
        self.assertEqual(m.repeated_action_count(), 3)

    def test_repeated_action_resets_on_different_call(self):
        m = TerminationMonitor(TerminationConfig())
        m.record_tool_call(_call("read_file", {"path": "a.py"}))
        m.record_tool_call(_call("read_file", {"path": "a.py"}))
        m.record_tool_call(_call("list_files", {"path": "."}))
        self.assertEqual(m.repeated_action_count(), 1)

    def test_repeated_action_limit(self):
        m = TerminationMonitor(TerminationConfig(repeated_action_warn=3, repeated_action_limit=4))
        for _ in range(4):
            m.record_tool_call(_call("read_file", {"path": "a.py"}))
        self.assertTrue(m.should_terminate_repetition())

    def test_consecutive_errors(self):
        m = TerminationMonitor(TerminationConfig(consecutive_error_warn=3, consecutive_error_limit=6))
        for _ in range(3):
            m.record_tool_result(_err())
        self.assertTrue(m.should_warn_consecutive_errors())
        self.assertFalse(m.should_terminate_consecutive_errors())
        for _ in range(3):
            m.record_tool_result(_err())
        self.assertTrue(m.should_terminate_consecutive_errors())

    def test_consecutive_errors_reset_on_success(self):
        m = TerminationMonitor(TerminationConfig())
        m.record_tool_result(_err())
        m.record_tool_result(_err())
        m.record_tool_result(_ok())
        self.assertEqual(m.consecutive_errors, 0)


if __name__ == "__main__":
    unittest.main()
