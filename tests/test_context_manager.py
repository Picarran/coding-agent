"""Tests for the ContextManager: budget trimming preserves system + task + recent."""
from __future__ import annotations

import unittest

from src.context.context_manager import ContextManager
from src.core.models import Message, ToolCall


def _assistant_tool(id_="a1", name="probe"):
    return Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id=id_, name=name, arguments={}, arguments_json="{}")],
    )


def _tool(id_="a1", name="probe", content="result"):
    return Message(role="tool", content=content, tool_call_id=id_, name=name)


class ContextManagerTest(unittest.TestCase):
    def test_start_keeps_system_and_task(self):
        cm = ContextManager("SYS", max_messages=10)
        cm.start("do it")
        msgs = cm.messages
        self.assertEqual([m.role for m in msgs], ["system", "user"])
        self.assertEqual(msgs[0].content, "SYS")
        self.assertEqual(msgs[1].content, "do it")

    def test_trim_drops_oldest_exchanges(self):
        cm = ContextManager("SYS", max_messages=6)
        cm.start("task")
        for cid in ("a1", "a2", "a3"):
            cm.append(_assistant_tool(cid))
            cm.append(_tool(cid))

        msgs = cm.messages
        self.assertEqual(msgs[0].role, "system")
        self.assertEqual(msgs[1].role, "user")
        self.assertGreater(cm.trimmed_exchanges, 0)
        # marker inserted after the task
        self.assertIn("[Context trimmed:", msgs[2].content)
        # newest exchange retained, oldest dropped
        self.assertTrue(any(m.tool_call_id == "a3" for m in msgs))
        self.assertFalse(any(m.tool_call_id == "a1" for m in msgs))

    def test_no_trim_when_under_budget(self):
        cm = ContextManager("SYS", max_messages=20)
        cm.start("task")
        cm.append(_assistant_tool("a1"))
        cm.append(_tool("a1"))
        self.assertEqual(cm.trimmed_exchanges, 0)
        self.assertEqual(len(cm.messages), 4)


if __name__ == "__main__":
    unittest.main()
