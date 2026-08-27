"""Phase 2 tests: search_text, patch_file, write_file, and argument validation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.core.models import ToolCall
from src.tools.executor import ToolExecutor
from src.tools.file_tools import build_file_tools
from src.tools.patch_tools import patch_file, write_file
from src.tools.registry import ToolRegistry
from src.tools.search_tools import search_text


class SearchTextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.py").write_text("foo = 2\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_search_finds_matches(self):
        out = search_text(self.root, "foo")
        self.assertIn("a.py:1:", out)
        self.assertIn("b.py:1:", out)

    def test_search_respects_pattern(self):
        out = search_text(self.root, "foo", file_pattern="a.py")
        self.assertIn("a.py", out)
        self.assertNotIn("b.py", out)


class PatchWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.file = self.root / "a.py"
        self.file.write_text("x = 1\ny = 2\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_patch_replaces_unique_text(self):
        out = patch_file(self.root, "a.py", "x = 1", "x = 10")
        self.assertIn("patched", out)
        self.assertIn("x = 10", self.file.read_text(encoding="utf-8"))

    def test_patch_missing_text_errors(self):
        out = patch_file(self.root, "a.py", "zzz", "nope")
        self.assertIn("not found", out)

    def test_patch_ambiguous_text_errors(self):
        self.file.write_text("v = 1\nv = 1\n", encoding="utf-8")
        out = patch_file(self.root, "a.py", "v = 1", "v = 2")
        self.assertIn("appears 2 times", out)

    def test_write_file_creates(self):
        out = write_file(self.root, "new.txt", "hello")
        self.assertIn("wrote", out)
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello")


class ValidationTest(unittest.TestCase):
    def _executor(self):
        registry = ToolRegistry()
        for tool in build_file_tools(Path(".")):
            registry.register(tool)
        return ToolExecutor(registry)

    def test_missing_required_argument(self):
        result = self._executor().execute(
            ToolCall(id="1", name="read_file", arguments={}, arguments_json="{}")
        )
        self.assertIsNotNone(result.error)
        self.assertIn("missing required argument", result.error)

    def test_wrong_type_argument(self):
        result = self._executor().execute(
            ToolCall(id="1", name="read_file", arguments={"path": 123}, arguments_json="{}")
        )
        self.assertIsNotNone(result.error)
        self.assertIn("expected string", result.error)

    def test_valid_arguments_pass(self):
        result = self._executor().execute(
            ToolCall(id="1", name="list_files", arguments={"path": "."}, arguments_json='{"path": "."}')
        )
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
