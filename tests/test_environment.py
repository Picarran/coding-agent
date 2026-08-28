"""Tests for the startup environment context builder."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.context.environment import build_environment_context


class EnvironmentTest(unittest.TestCase):
    def test_builds_context(self):
        ctx = build_environment_context(Path("demo_workspace"))
        self.assertIn("## Environment", ctx)
        self.assertIn("OS:", ctx)
        self.assertIn("Python:", ctx)
        self.assertIn("Workspace:", ctx)

    def test_notes_windows_shell(self):
        import platform

        ctx = build_environment_context(Path("."))
        if platform.system() == "Windows":
            self.assertIn("dir", ctx)
            self.assertIn("ls", ctx)


if __name__ == "__main__":
    unittest.main()
