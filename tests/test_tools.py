"""Tests for the file/command tools and the workspace guard (no LLM needed)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from src.core.events import EventBus, MetricsCollector
from src.core.models import ToolCall
from src.safety.workspace_guard import WorkspaceViolationError, resolve_in_workspace
from src.tools.command_tools import _decode_output, compress_command_output, execute_command
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.file_tools import list_files, read_file
from src.tools.registry import ToolRegistry


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sub").mkdir()
        (self.root / "a.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
        (self.root / "sub" / "b.txt").write_text("hello", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_files(self):
        out = list_files(self.root, ".", depth=2)
        self.assertIn("a.py", out)
        self.assertIn("sub", out)
        self.assertIn("b.txt", out)

    def test_list_files_skips_junk_and_hidden(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "x.cpython-311.pyc").write_text("x", encoding="utf-8")
        (self.root / ".hidden").write_text("h", encoding="utf-8")
        out = list_files(self.root, ".", depth=2)
        self.assertIn("a.py", out)
        self.assertNotIn("__pycache__", out)
        self.assertNotIn(".hidden", out)

    def test_list_files_show_hidden(self):
        (self.root / ".hidden").write_text("h", encoding="utf-8")
        out = list_files(self.root, ".", depth=2, show_hidden=True)
        self.assertIn(".hidden", out)

    def test_read_file_numbers_lines(self):
        out = read_file(self.root, "a.py")
        self.assertIn("1 | line1", out)
        self.assertIn("(lines 1-3 of 3)", out)

    def test_read_file_range(self):
        out = read_file(self.root, "a.py", start_line=2, end_line=2)
        self.assertIn("2 | line2", out)
        self.assertNotIn("line1", out)

    def test_workspace_guard_rejects_escape(self):
        with self.assertRaises(WorkspaceViolationError):
            resolve_in_workspace(self.root, "../../outside")


class CommandToolTest(unittest.TestCase):
    def test_execute_command_echo(self):
        out = execute_command(Path("."), "echo hello")
        self.assertIn("hello", out)
        self.assertIn("exit_code: 0", out)

    def test_execute_command_failure(self):
        out = execute_command(Path("."), 'python -c "raise SystemExit(3)"')
        self.assertIn("exit_code: 3", out)

    def test_decode_ascii(self):
        self.assertEqual(_decode_output(b"hello"), "hello")

    def test_decode_gbk_chinese(self):
        self.assertEqual(_decode_output("中文".encode("gbk")), "中文")


class BackgroundCommandTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "start /b is a cmd builtin")
    def test_background_start_does_not_hang(self):
        # A detached child inheriting stdout must not block the command from
        # returning. The old pipe-based capture waited for EOF forever; the
        # child here outlives the timeout, so the old code would report a
        # timeout while the fixed code returns immediately.
        out = execute_command(Path("."), "start /b ping -n 20 127.0.0.1", timeout=3)
        self.assertIn("timed_out: False", out)
        self.assertIn("exit_code: 0", out)


class CompressOutputTest(unittest.TestCase):
    def test_short_output_unchanged(self):
        text = "command: x\nexit_code: 0\n"
        self.assertEqual(compress_command_output(text), text)

    def test_long_output_compresses_and_keeps_key_lines(self):
        lines = ["command: pytest", "exit_code: 1", "timed_out: False", "--- stdout ---"]
        lines += [f"line {i}" for i in range(120)]
        lines += ["FAILED test_x - AssertionError"]
        lines += ["--- stderr ---", "Traceback (most recent call last):", '  File "t.py", line 3, in test_x', "AssertionError"]
        out = compress_command_output("\n".join(lines), max_chars=200)
        self.assertLess(len(out), len("\n".join(lines)))
        self.assertIn("FAILED test_x", out)
        self.assertIn("Traceback", out)
        self.assertIn("omitted", out)


class ToolCacheTest(unittest.TestCase):
    def _executor(self, bus=None):
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="list_files",
                description="",
                parameters={"type": "object", "properties": {}},
                func=lambda: "dir",
            )
        )
        registry.register(
            ToolDefinition(
                name="write_file",
                description="",
                parameters={"type": "object", "properties": {}},
                func=lambda: "wrote",
            )
        )
        return ToolExecutor(registry, event_bus=bus, agent_id="t")

    def test_repeated_read_is_cache_hit(self):
        bus = EventBus()
        metrics = MetricsCollector()
        bus.subscribe(metrics)
        ex = self._executor(bus)
        ex.execute(ToolCall(id="1", name="list_files", arguments={}))
        ex.execute(ToolCall(id="2", name="list_files", arguments={}))
        self.assertEqual(metrics.summary()["tool_cache_hits"], 1)

    def test_write_invalidates_cache(self):
        bus = EventBus()
        metrics = MetricsCollector()
        bus.subscribe(metrics)
        ex = self._executor(bus)
        ex.execute(ToolCall(id="1", name="list_files", arguments={}))  # cache
        ex.execute(ToolCall(id="2", name="write_file", arguments={}))  # invalidate
        ex.execute(ToolCall(id="3", name="list_files", arguments={}))  # miss
        self.assertEqual(metrics.summary()["tool_cache_hits"], 0)


if __name__ == "__main__":
    unittest.main()
