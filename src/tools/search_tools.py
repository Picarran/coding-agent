"""Search tool: ``search_text`` — literal substring search across workspace files."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from src.safety.workspace_guard import resolve_in_workspace
from src.tools.definitions import ToolDefinition

_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules"}
_MAX_FILE_BYTES = 512 * 1024
_MAX_RESULTS = 200


def _should_skip(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    for part in rel_parts:
        if part in _SKIP_DIRS or part.startswith("."):
            return True
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return True
    except OSError:
        return True
    return False


def search_text(
    root: Path,
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    target = resolve_in_workspace(root, path)
    if not target.exists():
        return f"Error: path does not exist: {path}"
    query = (query or "").strip()
    if not query:
        return "Error: empty query"
    file_pattern = file_pattern or "*"
    limit = max(1, min(int(max_results or 50), _MAX_RESULTS))

    if target.is_file():
        candidates = [target]
    else:
        candidates = [p for p in target.rglob(file_pattern) if p.is_file()]

    results: list[str] = []
    for file_path in sorted(candidates, key=lambda p: str(p.relative_to(root))):
        if len(results) >= limit:
            break
        if _should_skip(file_path, root):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = file_path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query in line:
                results.append(f"{rel}:{lineno}: {line.strip()}")
                if len(results) >= limit:
                    break

    header = f'search "{query}" in {path} (pattern={file_pattern}): {len(results)} match(es)'
    return header + ("\n(no matches)" if not results else "\n" + "\n".join(results))


def build_search_tools(root: Path) -> list[ToolDefinition]:
    root = Path(root).resolve()
    return [
        ToolDefinition(
            name="search_text",
            description=(
                "Search for a literal text substring in workspace files and return "
                "matching lines as 'file:line: text'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Literal substring to search for."},
                    "path": {"type": "string", "description": "Relative file or directory to search; '.' for root."},
                    "file_pattern": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')."},
                    "max_results": {"type": "integer", "description": "Max matching lines to return (default 50)."},
                },
                "required": ["query"],
            },
            func=partial(search_text, root),
        ),
    ]
