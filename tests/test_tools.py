"""Tests for the file/command tools and the workspace guard (no LLM needed)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.safety.workspace_guard import WorkspaceViolationError, resolve_in_workspace
from src.tools.command_tools import _decode_output, execute_command
from src.tools.file_tools import list_files, read_file


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


if __name__ == "__main__":
    unittest.main()
