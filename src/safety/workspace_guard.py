"""Workspace safety boundary.

Every file path an agent touches must resolve to a location inside the
workspace root. ``resolve()`` also resolves symlinks, so symlinks pointing
outside the workspace are rejected too. This is a deterministic check
implemented in code — not a soft prompt telling the model to "be careful".
"""
from __future__ import annotations

from pathlib import Path


class WorkspaceViolationError(Exception):
    """Raised when a path escapes the workspace boundary."""


def resolve_in_workspace(root: Path, rel_path: str) -> Path:
    root = Path(root).resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceViolationError(
            f"Path escapes workspace boundary: {rel_path!r} -> {target}"
        )
    return target
