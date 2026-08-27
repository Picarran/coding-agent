"""File tools: ``list_files`` and ``read_file`` (read-only).

Each tool is bound to a workspace root, so it can only ever touch paths inside
that root (enforced by ``resolve_in_workspace``).
"""
from __future__ import annotations

from functools import partial
from pathlib import Path

from src.safety.workspace_guard import resolve_in_workspace
from src.tools.definitions import ToolDefinition


def list_files(root: Path, path: str = ".", depth: int = 2) -> str:
    target = resolve_in_workspace(root, path)
    if not target.exists():
        return f"Error: path does not exist: {path}"
    if target.is_file():
        return f"{target.relative_to(root)} (file)"

    lines: list[str] = []

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        entries = sorted(
            directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        )
        for entry in entries:
            rel = entry.relative_to(root)
            prefix = "  " * level
            if entry.is_dir():
                lines.append(f"{prefix}{rel}/")
                walk(entry, level + 1)
            else:
                lines.append(f"{prefix}{rel}")

    walk(target, 0)
    return "\n".join(lines) if lines else "(empty directory)"


def read_file(
    root: Path,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    target = resolve_in_workspace(root, path)
    if not target.is_file():
        return f"Error: not a file: {path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading {path}: {exc}"

    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return f"{path}: (empty file)"

    start = max(1, start_line)
    end = total if end_line is None else min(total, end_line)
    if start > total:
        return f"{path}: start_line {start} exceeds file length {total}"

    numbered = [f"{i + 1:>4} | {lines[i]}" for i in range(start - 1, end)]
    header = f"{path} (lines {start}-{end} of {total})"
    return header + "\n" + "\n".join(numbered)


def build_file_tools(root: Path) -> list[ToolDefinition]:
    root = Path(root).resolve()
    return [
        ToolDefinition(
            name="list_files",
            description=(
                "List files and directories under a path inside the workspace. "
                "Returns paths relative to the workspace root."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to list; use '.' for the workspace root.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Directory nesting depth to show (default 2).",
                    },
                },
                "required": ["path"],
            },
            func=partial(list_files, root),
        ),
        ToolDefinition(
            name="read_file",
            description=(
                "Read a file inside the workspace and return numbered lines. "
                "Use start_line/end_line to read only part of a large file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file."},
                    "start_line": {
                        "type": "integer",
                        "description": "1-based first line to return.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based last line to return (inclusive).",
                    },
                },
                "required": ["path"],
            },
            func=partial(read_file, root),
        ),
    ]
