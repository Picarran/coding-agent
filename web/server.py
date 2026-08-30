"""FastAPI server for the Web Agent Workspace (V3-9).

Run:
    python -m uvicorn web.server:app --port 8001
or:
    python -m web.server

The agent runs in a background thread; its Event Bus is fanned out to the browser
over SSE (live plan timeline, tool trace, streaming answer), and approvals are
resolved by a blocking ``WebApprover`` that the frontend answers via POST.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.core.events import EventBus, EventType
from src.core.models import AgentResult
from src.llm.deepseek_client import DeepSeekClient
from src.llm.router import build_model_router
from src.main import build_agent
from src.mcp.config import default_config_path, load_mcp_config
from src.mcp.manager import MCPManager
from src.skills.registry import SkillRegistry, discover_skill_dirs
from web.broker import EventBroker, WebApprover

load_dotenv()

WEB_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Coding Agent Workspace")

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


class TaskRequest(BaseModel):
    task: str
    workspace: str = "demo_workspace"
    permission_mode: str = "default"
    orchestration: str = "auto"
    max_steps: int = 20


class ApprovalRequest(BaseModel):
    approve: bool = True
    always: bool = False


def _sse(event) -> str:
    return f"data: {json.dumps(event.to_dict(), ensure_ascii=False, default=str)}\n\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/sessions")
def start_session(req: TaskRequest) -> dict:
    """Start a new agent run in the background; return its session id."""
    session_id = uuid.uuid4().hex
    broker = EventBroker()
    bus = EventBus([broker], session_id=session_id)
    approver = WebApprover(publish=broker.publish)
    state = {
        "status": "starting",
        "task": req.task,
        "result": None,
        "error": None,
        "broker": broker,
        "approver": approver,
    }
    with _lock:
        _sessions[session_id] = state
    threading.Thread(
        target=_run_session, args=(session_id, req, bus, state), daemon=True
    ).start()
    return {"session_id": session_id, "status": "starting"}


def _run_session(session_id: str, req: TaskRequest, bus: EventBus, state: dict) -> None:
    """Build the agent and run the task; publish everything through the bus."""
    mcp_manager: MCPManager | None = None
    try:
        root = Path(req.workspace).resolve()
        if not root.is_dir():
            raise RuntimeError(f"workspace not found: {root}")

        llm = DeepSeekClient()
        router = build_model_router(llm)
        skill_registry = SkillRegistry.load_dirs(discover_skill_dirs(root))

        # MCP (same discovery as the CLI).
        mcp_manager = MCPManager()
        extra_tools = []
        mcp_path = default_config_path(root)
        if mcp_path.exists():
            extra_tools = mcp_manager.start(load_mcp_config(mcp_path))

        agent = build_agent(
            root,
            llm,
            max_steps=req.max_steps,
            orchestration=req.orchestration,
            permission_mode=req.permission_mode,
            interactive=False,
            event_bus=bus,
            router=router,
            skill_registry=skill_registry,
            extra_tools=extra_tools,
            streaming=True,
            approver=state["approver"],
        )

        with _lock:
            state["status"] = "running"
        bus.emit_simple(
            EventType.SESSION_START,
            payload={"task": req.task, "workspace": str(root)},
        )
        result = agent.run(req.task)
        bus.emit_simple(EventType.SESSION_END, status=result.status.value)
        with _lock:
            state["status"] = "done"
            state["result"] = result
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        bus.emit_simple(EventType.SESSION_END, status="ERROR")
        with _lock:
            state["status"] = "error"
            state["error"] = str(exc)
    finally:
        if mcp_manager is not None:
            mcp_manager.close()


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    with _lock:
        return [
            {"id": sid, "status": s["status"], "task": s.get("task")}
            for sid, s in _sessions.items()
        ]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    with _lock:
        state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "session not found")
    result: AgentResult | None = state.get("result")
    return {
        "id": session_id,
        "status": state["status"],
        "task": state.get("task"),
        "error": state.get("error"),
        "result": (
            {"status": result.status.value, "summary": result.summary}
            if result is not None
            else None
        ),
    }


@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request) -> StreamingResponse:
    with _lock:
        state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "session not found")
    broker: EventBroker = state["broker"]
    live, history = broker.subscribe()

    async def gen():
        try:
            for event in history:
                yield _sse(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(live.get, timeout=15.0)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event)
                if event.event_type == EventType.SESSION_END:
                    break
        finally:
            broker.unsubscribe(live)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/approve/{approval_id}")
def approve(session_id: str, approval_id: str, req: ApprovalRequest) -> dict:
    with _lock:
        state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "session not found")
    if state["approver"].resolve(approval_id, req.approve, req.always):
        return {"ok": True}
    raise HTTPException(404, "approval not found or already resolved")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.server:app", host="127.0.0.1", port=8001, reload=False)
