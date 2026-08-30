"""Disk persistence for web sessions (V3-9 refactor).

Each web session is saved as ``<base>/.coding-agent/web-sessions/<id>.json`` so
that a browser refresh or a server restart does not lose the conversation or the
trace. Only the summary fields are returned by ``list``; the full record (messages
+ events) is loaded on demand by ``load``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WebSessionStore:
    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(session_id).write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def load(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("web session %s unreadable: %s", session_id, exc)
            return None

    def list(self) -> list[dict[str, Any]]:
        """Lightweight summaries (no events) for the sidebar."""
        if not self._dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(
                {
                    "id": d.get("id"),
                    "workspace": d.get("workspace"),
                    "title": d.get("title") or (d.get("messages") or [{}])[0].get("content", "")[:40],
                    "created_at": d.get("created_at"),
                    "updated_at": d.get("updated_at"),
                    "status": d.get("status"),
                    "message_count": len(d.get("messages") or []),
                }
            )
        out.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        return out

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to delete session %s: %s", session_id, exc)
