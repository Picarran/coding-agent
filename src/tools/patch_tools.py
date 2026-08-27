"""Write tools: ``patch_file`` (exact replace) and ``write_file`` (create/overwrite)."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from src.safety.workspace_guard import resolve_in_workspace
from src.tools.definitions import ToolDefinition

_PREVIEW_LIMIT = 160


def _preview(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[{len(text) - limit} more chars]"


def patch_file(
    root: Path,
    path: str,
    old_text: str,
    new_text: str = "",
    expected_count: int | None = None,
) -> str:
    target = resolve_in_workspace(root, path)
    if not target.is_file():
        return f"Error: not a file: {path}"
    if not old_text:
        return "Error: old_text must not be empty"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error reading {path}: {exc}"

    occurrences = text.count(old_text)
    if occurrences == 0:
        return f"Error: old_text not found in {path}"
    if expected_count is None:
        if occurrences != 1:
            return (
                f"Error: old_text appears {occurrences} times in {path}; "
                "provide more context to make it unique, or set expected_count"
            )
    else:
        expected_count = int(expected_count)
        if occurrences != expected_count:
            return f"Error: old_text appears {occurrences} times, expected {expected_count}"

    new_text = new_text or ""
    new_content = text.replace(old_text, new_text)
    try:
        target.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path}: {exc}"

    first_line = text.count("\n", 0, text.index(old_text)) + 1
    return "\n".join(
        [
            f"patched {path}: replaced {occurrences} occurrence(s) (first at line {first_line})",
            f"- old_text: {_preview(old_text)}",
            f"+ new_text: {_preview(new_text)}",
        ]
    )


def write_file(root: Path, path: str, content: str) -> str:
    target = resolve_in_workspace(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path}: {exc}"
    return f"wrote {path} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"


def build_patch_tools(root: Path) -> list[ToolDefinition]:
    root = Path(root).resolve()
    return [
        ToolDefinition(
            name="patch_file",
            description=(
                "Replace an exact old_text with new_text in a file. old_text must be "
                "unique in the file (or set expected_count). The tool verifies uniqueness "
                "before writing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file to patch."},
                    "old_text": {"type": "string", "description": "Exact text to replace (must be unique)."},
                    "new_text": {"type": "string", "description": "Replacement text (default empty = delete)."},
                    "expected_count": {"type": "integer", "description": "Optional: exact number of occurrences to replace."},
                },
                "required": ["path", "old_text"],
            },
            func=partial(patch_file, root),
        ),
        ToolDefinition(
            name="write_file",
            description=(
                "Create or overwrite a file with the given content. Overwrites existing "
                "files, so use carefully; prefer patch_file for small edits."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file to create/overwrite."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
            func=partial(write_file, root),
        ),
    ]
