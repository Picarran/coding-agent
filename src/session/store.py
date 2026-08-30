"""Session persistence (V3-10): save/load the interactive conversation.

Each workspace gets its own session file at
``<workspace>/.coding-agent/session.json``. On startup it is loaded (resume);
after each turn and on exit it is saved, so a crash loses at most the in-flight
turn. This is *conversation* resume, not mid-plan resume: the rolling history and
a snapshot of the last plan persist, while a half-finished MainAgent plan is
rebuilt from scratch on the next task (see the trade-off note in ROADMAP V3-10).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SESSION_VERSION = 1


def default_session_path(root: Path) -> Path:
    """The conventional location: ``<workspace>/.coding-agent/session.json``."""
    return Path(root) / ".coding-agent" / "session.json"


def load_session(path: Path) -> dict[str, Any]:
    """Load a session file, or return a fresh empty session.

    Any read/parse error degrades to a fresh session (fail-open) rather than
    crashing the whole CLI on a corrupt file.
    """
    path = Path(path)
    if not path.is_file():
        return {"version": SESSION_VERSION, "history": [], "last_plan": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("session file unreadable (%s); starting fresh: %s", path, exc)
        return {"version": SESSION_VERSION, "history": [], "last_plan": []}
    if not isinstance(data, dict):
        data = {}
    history = data.get("history") or []
    if not isinstance(history, list):
        history = []
    last_plan = data.get("last_plan") or []
    if not isinstance(last_plan, list):
        last_plan = []
    return {"version": SESSION_VERSION, "history": history, "last_plan": last_plan}


def save_session(
    path: Path,
    history: list[str],
    last_plan: list[dict] | None = None,
) -> None:
    """Atomically-ish write the session file (write full JSON, truncate on open)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "version": SESSION_VERSION,
        "workspace": str(path.parent.parent),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "history": history,
        "last_plan": last_plan or [],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
