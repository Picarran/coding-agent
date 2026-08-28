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
]
