"""Reproducible eval tasks.

Each task is ``seed files + instruction + deterministic verdict``. The verdict
is a plain Python function (no LLM judging), so a task is graded identically on
every run — the property that makes before/after comparison meaningful.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VerifyFn = Callable[[Path], tuple[bool, str]]


@dataclass(frozen=True)
class Task:
    name: str
    seed: dict[str, str]  # relative path -> file content
    task: str  # natural-language instruction given to the agent
    verify: VerifyFn
    complex: bool = False  # multi-step / cross-file, exercises decomposition + long context


def _run_python(root: Path, code: str, timeout: int = 60) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    return cp.returncode, ((cp.stdout or "") + (cp.stderr or "")).strip()


def _run_script(root: Path, script: str, timeout: int = 60) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            [sys.executable, script],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    return cp.returncode, ((cp.stdout or "") + (cp.stderr or "")).strip()


def _read(root: Path, filename: str) -> tuple[bool, str]:
    p = root / filename
    if not p.is_file():
        return False, f"{filename} not found"
    return True, p.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- #
# Task 1: fix an integer-division bug so the test passes.
# --------------------------------------------------------------------------- #
_CALCULATOR = "def divide(a, b):\n    return a // b\n"

_TEST_CALCULATOR = (
    "import unittest\n"
    "from calculator import divide\n\n"
    "class TestCalculator(unittest.TestCase):\n"
    "    def test_divide(self):\n"
    "        self.assertEqual(divide(7, 2), 3.5)\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


def _verify_fix_divide(root: Path) -> tuple[bool, str]:
    rc, out = _run_script(root, "test_calculator.py")
    return rc == 0, f"test_calculator.py exit={rc}: {out}"


# --------------------------------------------------------------------------- #
# Task 2: create a module with a greeting function.
# --------------------------------------------------------------------------- #
def _verify_create_greet(root: Path) -> tuple[bool, str]:
    rc, out = _run_python(
        root,
        "from greet import greet; assert greet('World') == 'Hello, World!'; print('ok')",
    )
    return rc == 0, out or f"exit={rc}"


# --------------------------------------------------------------------------- #
# Task 3: create a module with a sum function.
# --------------------------------------------------------------------------- #
def _verify_sum_function(root: Path) -> tuple[bool, str]:
    rc, out = _run_python(
        root,
        "from sum import sum_numbers; assert sum_numbers([1, 2, 3]) == 6; print('ok')",
    )
    return rc == 0, out or f"exit={rc}"


# --------------------------------------------------------------------------- #
# Task 4: read a version field from JSON and write it to a text file.
# --------------------------------------------------------------------------- #
_CONFIG_JSON = '{"name": "demo", "version": "1.2.3"}\n'


def _verify_extract_version(root: Path) -> tuple[bool, str]:
    ok, content = _read(root, "version.txt")
    if not ok:
        return False, content
    return content == "1.2.3", f"version.txt = {content!r}"


# --------------------------------------------------------------------------- #
# Task 5: find files containing a TODO comment and list their names.
# --------------------------------------------------------------------------- #
_A_PY = "# TODO: fix this\nx = 1\n"
_B_PY = "x = 2\n"


def _verify_find_todos(root: Path) -> tuple[bool, str]:
    ok, content = _read(root, "todos.txt")
    if not ok:
        return False, content
    lowered = content.lower()
    has_a = "a.py" in lowered
    has_b = "b.py" in lowered
    return has_a and not has_b, f"todos.txt = {content!r}"


# --------------------------------------------------------------------------- #
# Task 6 (complex): move a function to a new module and update every reference.
# --------------------------------------------------------------------------- #
_ARITH_PY = "def double(x):\n    return x * 2\n\n\ndef triple(x):\n    return x * 3\n"

_APP_PY = "from arith import double, triple\n\n\ndef compute(x):\n    return double(x) + triple(x)\n"

_TEST_APP_PY = (
    "import unittest\n"
    "from app import compute\n"
    "from advanced import triple\n\n"
    "class TestApp(unittest.TestCase):\n"
    "    def test_compute(self):\n"
    "        self.assertEqual(compute(2), 10)\n"
    "    def test_triple(self):\n"
    "        self.assertEqual(triple(4), 12)\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


def _verify_split_module(root: Path) -> tuple[bool, str]:
    rc, out = _run_script(root, "test_app.py")
    if rc != 0:
        return False, f"test_app.py exit={rc}: {out}"
    if "def triple" in (root / "arith.py").read_text(encoding="utf-8"):
        return False, "arith.py still defines triple (should be moved to advanced.py)"
    if not (root / "advanced.py").is_file():
        return False, "advanced.py not created"
    return True, "ok"


# --------------------------------------------------------------------------- #
# Task 7 (complex): trace a data-flow bug across three modules and fix two.
# --------------------------------------------------------------------------- #
_DATA_PY = "def load_numbers():\n    return [1, 2, 3, 4]\n"

_STATS_PY = (
    "from data import load_numbers\n\n\n"
    "def average():\n"
    "    nums = load_numbers()\n"
    "    return sum(nums) // len(nums)\n"
)

_REPORT_PY = (
    "from stats import average\n\n\n"
    "def report():\n"
    "    return 'avg={}'.format(average())\n"
)

_TEST_REPORT_PY = (
    "import unittest\n"
    "from report import report\n"
    "from stats import average\n\n"
    "class TestReport(unittest.TestCase):\n"
    "    def test_average(self):\n"
    "        self.assertEqual(average(), 2.5)\n"
    "    def test_report(self):\n"
    "        self.assertEqual(report(), 'average=2.5')\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


def _verify_fix_data_flow(root: Path) -> tuple[bool, str]:
    rc, out = _run_script(root, "test_report.py")
    return rc == 0, f"test_report.py exit={rc}: {out}"


# --------------------------------------------------------------------------- #
# Task 8 (complex, stress): run a command that emits thousands of lines, then
# extract one value. Exercises V1-3-3 (command-output compression).
# --------------------------------------------------------------------------- #
_NOISE_PY = (
    "for i in range(3000):\n"
    "    print(f'noise line {i}')\n"
    "print('FINAL_RESULT=42')\n"
)


def _verify_stress_noise_extract(root: Path) -> tuple[bool, str]:
    ok, content = _read(root, "result.txt")
    if not ok:
        return False, content
    return content == "42", f"result.txt = {content!r}"


TASKS: list[Task] = [
    Task(
        name="fix_divide_bug",
        seed={"calculator.py": _CALCULATOR, "test_calculator.py": _TEST_CALCULATOR},
        task="修复 calculator.py 的除法 bug，让 test_calculator.py 里的测试全部通过。",
        verify=_verify_fix_divide,
    ),
    Task(
        name="create_greet",
        seed={},
        task="创建 greet.py，定义函数 greet(name)，返回 'Hello, {name}!'。",
        verify=_verify_create_greet,
    ),
    Task(
        name="sum_function",
        seed={},
        task="创建 sum.py，定义函数 sum_numbers(nums)，返回列表里所有数字之和。",
        verify=_verify_sum_function,
    ),
    Task(
        name="extract_version",
        seed={"config.json": _CONFIG_JSON},
        task="读取 config.json 里的 version 字段，把它写入 version.txt（只写版本号本身，如 1.2.3）。",
        verify=_verify_extract_version,
    ),
    Task(
        name="find_todos",
        seed={"a.py": _A_PY, "b.py": _B_PY},
        task="在 workspace 中找出所有包含 TODO 注释的 .py 文件，把文件名（不含路径，每行一个）写入 todos.txt。",
        verify=_verify_find_todos,
    ),
    Task(
        name="split_module",
        seed={"arith.py": _ARITH_PY, "app.py": _APP_PY, "test_app.py": _TEST_APP_PY},
        task=(
            "把 arith.py 里的 triple 函数移动到新建的 advanced.py 模块（arith.py 里不再保留 triple），"
            "更新 app.py 的 import（compute 仍需用 double 和 triple），并确保 test_app.py 全部通过。"
        ),
        verify=_verify_split_module,
        complex=True,
    ),
    Task(
        name="fix_data_flow",
        seed={
            "data.py": _DATA_PY,
            "stats.py": _STATS_PY,
            "report.py": _REPORT_PY,
            "test_report.py": _TEST_REPORT_PY,
        },
        task=(
            "追踪 data -> stats -> report 的数据流：stats.py 用了整数除法导致平均值错误，"
            "report.py 的输出格式不对（应为 'average=2.5'）。修复这两处，让 test_report.py 全部通过。"
        ),
        verify=_verify_fix_data_flow,
        complex=True,
    ),
    Task(
        name="stress_noise_extract",
        seed={"noise.py": _NOISE_PY},
        task=(
            "运行 python noise.py，从它的输出里找到 FINAL_RESULT 的值，"
            "把数字写入 result.txt（只写数字本身）。"
        ),
        verify=_verify_stress_noise_extract,
        complex=True,
    ),
]
