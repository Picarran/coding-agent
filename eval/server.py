"""FastAPI server for the eval dashboard.

Run:
  python -m uvicorn eval.server:app --port 8000
or:
  python -m eval.server

Endpoints:
  GET  /                 -> dashboard (single-page frontend)
  GET  /api/tasks        -> task catalog for the run form
  POST /api/runs         -> start a run (background thread)
  GET  /api/runs         -> completed run summaries (history)
  GET  /api/runs/{id}    -> one run's status + full result
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from eval.runner import run_eval
from eval.store import RunStore
from eval.tasks import TASKS

load_dotenv()

WEB_DIR = Path(__file__).parent / "web"
store = RunStore()

app = FastAPI(title="Coding Agent Eval Dashboard")

_runs: dict[str, dict] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    label: str = ""
    tasks: list[str] = []
    max_steps: int = 20
    dry_run: bool = False
    agent_mode: str = "multi"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/tasks")
def api_tasks() -> list[dict]:
    return [{"name": t.name, "task": t.task, "complex": t.complex} for t in TASKS]


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    names = req.tasks or [t.name for t in TASKS]
    tasks = [t for t in TASKS if t.name in names]
    if not tasks:
        raise HTTPException(400, "no known tasks selected")

    with _lock:
        _runs[run_id] = {
            "status": "running",
            "record": None,
            "error": None,
            "progress": {"done": [], "current": None},
        }

    def worker() -> None:
        try:
            records, agg = run_eval(
                tasks,
                req.max_steps,
                req.dry_run,
                agent_mode=req.agent_mode,
                progress_cb=lambda ev: _update_progress(run_id, ev),
            )
            record = {
                "run_id": run_id,
                "label": req.label or run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mode": "dry-run" if req.dry_run else "real",
                "agent_mode": req.agent_mode,
                "params": {"tasks": names, "max_steps": req.max_steps},
                "tasks": records,
                "aggregate": agg,
            }
            store.save(record)
            with _lock:
                _runs[run_id] = {"status": "done", "record": record, "error": None}
        except Exception as exc:  # noqa: BLE001 - surface any run failure to the UI
            with _lock:
                _runs[run_id] = {"status": "error", "record": None, "error": str(exc)}

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id, "status": "running"}


def _update_progress(run_id: str, event: dict) -> None:
    with _lock:
        state = _runs.get(run_id)
        if state is None:
            return
        prog = state.setdefault("progress", {"done": [], "current": None})
        phase = event.get("phase")
        if phase == "task_start":
            prog["current"] = event.get("task")
        elif phase == "task_done":
            prog["done"].append(event.get("task"))
            prog["current"] = None


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return store.list()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    with _lock:
        inmem = _runs.get(run_id)
    if inmem is not None:
        return inmem
    record = store.get(run_id)
    if record is None:
        raise HTTPException(404, "run not found")
    return {"status": "done", "record": record}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("eval.server:app", host="127.0.0.1", port=8000, reload=False)
