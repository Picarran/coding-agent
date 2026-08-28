"""Tests for the eval tasks (deterministic grading) and runner helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eval.runner import aggregate, seed_workspace
from eval.tasks import TASKS


def _by_name(name: str):
    for task in TASKS:
        if task.name == name:
            return task
    raise KeyError(name)


class SeedWorkspaceTest(unittest.TestCase):
    def test_seeds_files_and_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_workspace(root, {"sub/a.py": "x = 1\n", "b.txt": "hi"})
            self.assertEqual((root / "sub" / "a.py").read_text(), "x = 1\n")
            self.assertEqual((root / "b.txt").read_text(), "hi")


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel: str, content: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_fix_divide_bug_passes_when_fixed(self):
        task = _by_name("fix_divide_bug")
        seed_workspace(self.root, task.seed)
        self._write("calculator.py", "def divide(a, b):\n    return a / b\n")
        passed, _ = task.verify(self.root)
        self.assertTrue(passed)

    def test_fix_divide_bug_fails_on_original_bug(self):
        task = _by_name("fix_divide_bug")
        seed_workspace(self.root, task.seed)
        passed, _ = task.verify(self.root)
        self.assertFalse(passed)

    def test_create_greet_passes(self):
        task = _by_name("create_greet")
        self._write("greet.py", "def greet(name):\n    return f'Hello, {name}!'\n")
        passed, _ = task.verify(self.root)
        self.assertTrue(passed)

    def test_create_greet_fails_when_missing(self):
        passed, reason = _by_name("create_greet").verify(self.root)
        self.assertFalse(passed)
        self.assertTrue(reason)

    def test_sum_function_passes(self):
        self._write("sum.py", "def sum_numbers(nums):\n    return sum(nums)\n")
        passed, _ = _by_name("sum_function").verify(self.root)
        self.assertTrue(passed)

    def test_extract_version_passes(self):
        self._write("version.txt", "1.2.3")
        passed, _ = _by_name("extract_version").verify(self.root)
        self.assertTrue(passed)

    def test_extract_version_fails_when_missing(self):
        passed, _ = _by_name("extract_version").verify(self.root)
        self.assertFalse(passed)

    def test_find_todos_passes(self):
        self._write("todos.txt", "a.py\n")
        passed, _ = _by_name("find_todos").verify(self.root)
        self.assertTrue(passed)

    def test_find_todos_rejects_wrong_file(self):
        self._write("todos.txt", "b.py\n")
        passed, _ = _by_name("find_todos").verify(self.root)
        self.assertFalse(passed)


class AggregateTest(unittest.TestCase):
    def test_aggregate_averages(self):
        records = [
            {"passed": True, "plan_steps": 1, "tool_calls": 2, "total_tokens": 100, "duration_ms": 10.0},
            {"passed": False, "plan_steps": 2, "tool_calls": 4, "total_tokens": 200, "duration_ms": 20.0},
        ]
        agg = aggregate(records)
        self.assertEqual(agg["tasks"], 2)
        self.assertEqual(agg["passed"], 1)
        self.assertEqual(agg["success_rate"], 0.5)
        self.assertEqual(agg["avg_plan_steps"], 1.5)
        self.assertEqual(agg["avg_tool_calls"], 3.0)
        self.assertEqual(agg["avg_tokens"], 150.0)
        self.assertEqual(agg["avg_duration_ms"], 15.0)


if __name__ == "__main__":
    unittest.main()
