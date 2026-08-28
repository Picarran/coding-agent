"""Persistence for eval runs: one JSON file per run under ``eval/runs/``.

Keeps every run (baseline and post-optimization alike) so the dashboard can
show history and compare before/after results.
"""
from __future__ import annotations

import json
from pathlib import Path


class RunStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else Path(__file__).parent / "runs"

    def save(self, record: dict) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        run_id = record["run_id"]
        (self._root / f"{run_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run_id

    def get(self, run_id: str) -> dict | None:
        p = self._root / f"{run_id}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        """Return run summaries, newest first."""
        if not self._root.is_dir():
            return []
        summaries: list[dict] = []
        for p in sorted(self._root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            agg = rec.get("aggregate", {})
            summaries.append(
                {
                    "run_id": rec.get("run_id"),
                    "label": rec.get("label", ""),
                    "created_at": rec.get("created_at"),
                    "mode": rec.get("mode"),
                    "agent_mode": rec.get("agent_mode", "multi"),
                    "tasks": rec.get("params", {}).get("tasks", []),
                    "total": agg.get("tasks"),
                    "passed": agg.get("passed"),
                    "success_rate": agg.get("success_rate"),
                    "avg_tokens": agg.get("avg_tokens"),
                    "avg_duration_ms": agg.get("avg_duration_ms"),
                }
            )
        return summaries
