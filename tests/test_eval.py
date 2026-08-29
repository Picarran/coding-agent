"""Tests for the eval tasks (deterministic grading) and runner helpers."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from eval.runner import aggregate, seed_workspace
from eval.store import RunStore
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

    def test_split_module_passes_when_moved(self):
        task = _by_name("split_module")
        seed_workspace(self.root, task.seed)
        self._write("advanced.py", "def triple(x):\n    return x * 3\n")
        self._write("arith.py", "def double(x):\n    return x * 2\n")
        self._write(
            "app.py",
            "from arith import double\nfrom advanced import triple\n\n\ndef compute(x):\n    return double(x) + triple(x)\n",
        )
        passed, _ = task.verify(self.root)
        self.assertTrue(passed)

    def test_split_module_fails_on_original(self):
        task = _by_name("split_module")
        seed_workspace(self.root, task.seed)
        passed, _ = task.verify(self.root)
        self.assertFalse(passed)

    def test_fix_data_flow_passes_when_fixed(self):
        task = _by_name("fix_data_flow")
        seed_workspace(self.root, task.seed)
        self._write(
            "stats.py",
            "from data import load_numbers\n\n\ndef average():\n    nums = load_numbers()\n    return sum(nums) / len(nums)\n",
        )
        self._write(
            "report.py",
            "from stats import average\n\n\ndef report():\n    return 'average={}'.format(average())\n",
        )
        passed, _ = task.verify(self.root)
        self.assertTrue(passed)

    def test_fix_data_flow_fails_on_original(self):
        task = _by_name("fix_data_flow")
        seed_workspace(self.root, task.seed)
        passed, _ = task.verify(self.root)
        self.assertFalse(passed)

    def test_stress_noise_extract_passes(self):
        self._write("result.txt", "42")
        passed, _ = _by_name("stress_noise_extract").verify(self.root)
        self.assertTrue(passed)

    def test_stress_noise_extract_fails_when_missing(self):
        passed, _ = _by_name("stress_noise_extract").verify(self.root)
        self.assertFalse(passed)

    def test_parallel_summarize_passes(self):
        self._write("total.txt", "66")
        passed, _ = _by_name("parallel_summarize").verify(self.root)
        self.assertTrue(passed)

    def test_parallel_summarize_fails_when_wrong(self):
        self._write("total.txt", "65")
        passed, _ = _by_name("parallel_summarize").verify(self.root)
        self.assertFalse(passed)


class RunStoreTest(unittest.TestCase):
    def _record(self, run_id, label, rate):
        return {
            "run_id": run_id,
            "label": label,
            "created_at": run_id,
            "mode": "real",
            "params": {"tasks": ["a"], "max_steps": 20},
            "tasks": [],
            "aggregate": {"tasks": 1, "passed": rate, "success_rate": rate,
                          "avg_tokens": 10.0, "avg_duration_ms": 100.0},
        }

    def test_save_list_get(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d))
            store.save(self._record("r1", "baseline", 1.0))
            time.sleep(0.02)
            store.save(self._record("r2", "after", 0.0))

            runs = store.list()
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0]["run_id"], "r2")  # newest first
            self.assertEqual(runs[0]["success_rate"], 0.0)
            self.assertEqual(store.get("r1")["label"], "baseline")
            self.assertIsNone(store.get("missing"))


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

    def test_aggregate_derived_metrics(self):
        records = [
            {"passed": True, "total_tokens": 100, "tool_calls": 5, "context_compactions": 1},
            {"passed": True, "total_tokens": 50, "tool_calls": 5, "context_compactions": 3},
        ]
        agg = aggregate(records)
        self.assertEqual(agg["tokens_per_tool_call"], 15.0)  # 150 tokens / 10 tools
        self.assertEqual(agg["context_compactions"], 4)

    def test_aggregate_direct_and_parallel(self):
        records = [
            {"passed": True, "direct_steps": 2, "parallel_batches": 1},
            {"passed": True, "direct_steps": 1, "parallel_batches": 0},
        ]
        agg = aggregate(records)
        self.assertEqual(agg["direct_steps"], 3)
        self.assertEqual(agg["parallel_batches"], 1)


if __name__ == "__main__":
    unittest.main()
