"""FastAPI server for the Web Agent Workspace (V3-9 refactor).

Run:
    python -m uvicorn web.server:app --port 8001

Layout (DeepSeek Harness style): a left sidebar lists workspaces and their
sessions; the right pane is a conversation with the agent, toggleable between
"conversation" and "trace" views. Sessions persist to disk so a refresh or
restart does not lose history.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agents.main_agent_session import COMMANDS, MainAgentSession
from src.core.events import EventBus, EventType, SessionMetrics, TraceEvent
from src.llm.deepseek_client import DeepSeekClient
from src.llm.router import build_model_router
from src.main import build_agent
from src.mcp.config import default_config_path, load_mcp_config
from src.mcp.manager import MCPManager
from src.skills.registry import SkillRegistry, discover_skill_dirs
from web.broker import EventBroker, WebApprover
from web.store import WebSessionStore

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).parent / "static"
FRONTEND_DIST = PROJECT_ROOT / "web" / "frontend" / "dist"
STORE_DIR = PROJECT_ROOT / ".coding-agent" / "web-sessions"
store = WebSessionStore(STORE_DIR)

app = FastAPI(title="Coding Agent Workspace")

# Serve the built Vite+Vue frontend assets (created by ``npm run build``).
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

_live: dict[str, dict] = {}
_lock = threading.Lock()


class CreateSessionRequest(BaseModel):
    workspace: str = "demo_workspace"
    orchestration: str = "auto"
    permission_mode: str = "default"
    max_steps: int = 20


class MessageRequest(BaseModel):
    content: str
    orchestration: str | None = None
    permission: str | None = None
    max_steps: int | None = None


class ApprovalRequest(BaseModel):
    approve: bool = True
    always: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_workspace(name: str) -> Path:
    p = Path(name)
    if p.is_absolute():
        return p.resolve()
    return (PROJECT_ROOT / name).resolve()


def _serialize_event(event: TraceEvent) -> str:
    return f"data: {json.dumps(event.to_dict(), ensure_ascii=False, default=str)}\n\n"


class StopRequested(Exception):
    """Raised from the agent's checkpoint callback to interrupt a turn."""


def _make_checkpoint(state: dict):
    """A checkpoint hook that raises StopRequested once ``/stop`` fires."""

    def _cb() -> None:
        if state.get("stop_event") and state["stop_event"].is_set():
            raise StopRequested()

    return _cb


def _is_command(content: str) -> bool:
    """True when the message is a recognized slash command."""
    if not content.startswith("/"):
        return False
    name = content.split()[0].lower()
    return name in COMMANDS


# --------------------------------------------------------------------------- #
# Workspaces + sessions
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    if (FRONTEND_DIST / "index.html").is_file():
        return FileResponse(FRONTEND_DIST / "index.html")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/workspaces")
def workspaces() -> list[dict]:
    """Candidate working directories: the project root + its subdirectories."""
    entries = [{"name": "(项目根目录)", "path": str(PROJECT_ROOT)}]
    try:
        for child in sorted(PROJECT_ROOT.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__":
                entries.append({"name": child.name, "path": str(child)})
    except OSError:
        pass
    return entries


@app.get("/api/fs/list")
def fs_list(path: str = "") -> dict:
    """List subdirectories of a server path, for the workspace-picker modal.

    ``dirs`` carries full child paths so the frontend never has to join paths"
    itself (platform separators differ).
    """
    root = Path(path) if path else Path.home()
    if not root.is_dir():
        return {"path": str(root), "dirs": [], "parent": None, "error": "not a directory"}
    dirs: list[dict] = []
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                dirs.append({"name": child.name, "path": str(child)})
    except (PermissionError, OSError):
        pass
    parent = str(root.parent) if root.parent != root else None
    return {"path": str(root), "dirs": dirs, "parent": parent}


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return store.list()


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest) -> dict:
    root = _resolve_workspace(req.workspace)
    if not root.is_dir():
        raise HTTPException(400, f"workspace not found: {root}")
    session_id = uuid.uuid4().hex
    broker = EventBroker()
    bus = EventBus([broker], session_id=session_id)
    approver = WebApprover(publish=broker.publish)
    metrics = SessionMetrics()
    bus.subscribe(metrics)
    state = {
        "id": session_id,
        "workspace": str(root),
        "orchestration": req.orchestration,
        "permission": req.permission_mode,
        "max_steps": req.max_steps,
        "bus": bus,
        "broker": broker,
        "approver": approver,
        "metrics": metrics,
        "agent_session": None,  # built lazily on first message
        "mcp_manager": None,
        "stop_event": threading.Event(),
        "running": False,
        "messages": [],
        "title": "",
        "status": "idle",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        _live[session_id] = state
    _persist(state)
    return {"session_id": session_id}


def _persist(state: dict) -> None:
    store.save(
        state["id"],
        {
            "id": state["id"],
            "workspace": state["workspace"],
            "orchestration": state["orchestration"],
            "permission": state["permission"],
            "max_steps": state["max_steps"],
            "title": state.get("title") or "",
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "status": state.get("status"),
            "messages": state.get("messages") or [],
            "events": [e.to_dict() for e in state["broker"].history()],
        },
    )


def _ensure_live(session_id: str) -> dict:
    """Return a live state, warming a cold (persisted) session if needed."""
    with _lock:
        state = _live.get(session_id)
    if state is not None:
        return state
    data = store.load(session_id)
    if data is None:
        raise HTTPException(404, "session not found")
    root = Path(data.get("workspace") or "demo_workspace")
    broker = EventBroker()
    bus = EventBus([broker], session_id=session_id)
    approver = WebApprover(publish=broker.publish)
    metrics = SessionMetrics()
    bus.subscribe(metrics)
    state = {
        "id": session_id,
        "workspace": str(root),
        "orchestration": data.get("orchestration") or "auto",
        "permission": data.get("permission") or "default",
        "max_steps": int(data.get("max_steps") or 20),
        "bus": bus,
        "broker": broker,
        "approver": approver,
        "metrics": metrics,
        "agent_session": None,
        "mcp_manager": None,
        "stop_event": threading.Event(),
        "running": False,
        "messages": list(data.get("messages") or []),
        "title": data.get("title") or "",
        "status": data.get("status") or "idle",
        "created_at": data.get("created_at") or _now(),
        "updated_at": data.get("updated_at") or _now(),
    }
    with _lock:
        _live[session_id] = state
    # Seed the fresh broker's history from persisted events (for trace replay)
    # and re-feed them through the metrics collector so a cold session still
    # reports its token/latency metrics.
    current_label = ""
    for ev_dict in data.get("events") or []:
        try:
            ev = TraceEvent.from_dict(ev_dict)
            broker.replay([ev])
            metrics.on_event(ev)
            if ev.event_type == EventType.SESSION_START:
                current_label = (ev.payload or {}).get("task", "")
            elif ev.event_type == EventType.TURN_END:
                metrics.finish_task(current_label)
        except Exception:  # noqa: BLE001 - skip malformed persisted events
            continue
    return state


def _reset_agent(state: dict) -> None:
    """Discard the built agent so it rebuilds with the latest settings."""
    if state.get("mcp_manager") is not None:
        state["mcp_manager"].close()
        state["mcp_manager"] = None
    state["agent_session"] = None


def _build_agent_session(state: dict) -> None:
    """Build the agent + conversation wrapper once, then reuse across turns."""
    if state["agent_session"] is not None:
        return
    root = Path(state["workspace"])
    llm = DeepSeekClient()
    router = build_model_router(llm)
    skill_registry = SkillRegistry.load_dirs(discover_skill_dirs(root))

    mcp_manager = MCPManager()
    extra_tools = []
    mcp_path = default_config_path(root)
    if mcp_path.exists():
        extra_tools = mcp_manager.start(load_mcp_config(mcp_path))
    state["mcp_manager"] = mcp_manager

    agent = build_agent(
        root,
        llm,
        max_steps=state["max_steps"],
        orchestration=state["orchestration"],
        permission_mode=state["permission"],
        interactive=False,
        event_bus=state["bus"],
        router=router,
        skill_registry=skill_registry,
        extra_tools=extra_tools,
        streaming=True,
        approver=state["approver"],
        checkpoint_cb=_make_checkpoint(state),
    )
    session = MainAgentSession(
        agent, llm=llm, skill_registry=skill_registry, event_bus=state["bus"]
    )
    # Seed the conversation from persisted messages (resume across restart).
    session.set_history(_messages_to_history(state["messages"]))
    state["agent_session"] = session


def _messages_to_history(messages: list[dict]) -> list[str]:
    out = []
    for m in messages:
        if m.get("role") == "user":
            out.append(f"User: {m.get('content', '')}")
        elif m.get("role") == "assistant":
            out.append(f"Agent: {m.get('content', '')}")
    return out


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    state = _ensure_live(session_id)
    return {
        "id": session_id,
        "workspace": state["workspace"],
        "orchestration": state["orchestration"],
        "permission": state["permission"],
        "title": state.get("title") or "",
        "status": state.get("status"),
        "messages": state.get("messages") or [],
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    }


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    with _lock:
        _live.pop(session_id, None)
    store.delete(session_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Conversation (multi-turn)
# --------------------------------------------------------------------------- #
@app.post("/api/sessions/{session_id}/messages")
def send_message(session_id: str, req: MessageRequest) -> dict:
    if not (req.content or "").strip():
        raise HTTPException(400, "empty message")
    state = _ensure_live(session_id)
    if state["running"]:
        raise HTTPException(409, "session is busy")
    content = req.content.strip()
    # Apply per-message setting overrides (rebuild the agent lazily on change).
    changed = False
    if req.orchestration and req.orchestration != state["orchestration"]:
        state["orchestration"] = req.orchestration
        changed = True
    if req.permission and req.permission != state["permission"]:
        state["permission"] = req.permission
        changed = True
    if req.max_steps and req.max_steps != state["max_steps"]:
        state["max_steps"] = req.max_steps
        changed = True
    if changed:
        _reset_agent(state)

    state["running"] = True
    state["status"] = "running"
    state["stop_event"].clear()
    is_command = _is_command(content)
    if not is_command and not state.get("title"):
        state["title"] = content[:40]
    # Persist both commands and tasks as user messages so the conversation is
    # coherent on reload.
    state["messages"].append({"role": "user", "content": content})
    _persist(state)
    target = _run_command if is_command else _run_turn
    threading.Thread(target=target, args=(state, content), daemon=True).start()
    return {"ok": True, "status": "running"}


def _run_command(state: dict, content: str) -> None:
    """Execute a slash command via the session (no agent turn)."""
    try:
        _build_agent_session(state)
        # Emit SESSION_START so the frontend opens a turn for the command, and
        # TURN_END carries its output back (otherwise the reply is dropped).
        state["bus"].emit_simple(EventType.SESSION_START, payload={"task": content})
        out = state["agent_session"].handle_command(content)
        if out is None:
            out = f"未知命令：{content.split()[0]}"
        state["messages"].append({"role": "assistant", "content": out})
        state["bus"].emit_simple(EventType.TURN_END, status="done", payload={"summary": out})
        state["status"] = "idle"
    except Exception as exc:  # noqa: BLE001
        state["messages"].append({"role": "assistant", "content": f"Error: {exc}"})
        state["bus"].emit_simple(EventType.TURN_END, status="ERROR", payload={"summary": str(exc)})
        state["status"] = "error"
    finally:
        state["running"] = False
        state["updated_at"] = _now()
        state["metrics"].finish_task(content)
        _persist(state)


def _run_turn(state: dict, content: str) -> None:
    try:
        _build_agent_session(state)
        state["bus"].emit_simple(EventType.SESSION_START, payload={"task": content})
        result = state["agent_session"].send(content)
        state["messages"].append({"role": "assistant", "content": result.summary})
        state["bus"].emit_simple(
            EventType.TURN_END,
            status=result.status.value,
            payload={"summary": result.summary},
        )
        state["status"] = "idle"
    except StopRequested:
        state["bus"].emit_simple(
            EventType.TURN_END, status="blocked", payload={"summary": "(已中断)"}
        )
        state["messages"].append({"role": "assistant", "content": "(已中断)"})
        state["status"] = "blocked"
    except Exception as exc:  # noqa: BLE001 - surface failure to the UI
        state["bus"].emit_simple(EventType.TURN_END, status="ERROR", payload={"summary": str(exc)})
        state["messages"].append({"role": "assistant", "content": f"Error: {exc}"})
        state["status"] = "error"
    finally:
        state["running"] = False
        state["updated_at"] = _now()
        state["metrics"].finish_task(content)
        _persist(state)


# --------------------------------------------------------------------------- #
# Events + approval
# --------------------------------------------------------------------------- #
@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request) -> StreamingResponse:
    state = _ensure_live(session_id)
    broker: EventBroker = state["broker"]
    live, history = broker.subscribe()

    async def gen():
        try:
            for event in history:
                yield _serialize_event(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(live.get, timeout=15.0)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield _serialize_event(event)
        finally:
            broker.unsubscribe(live)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_sse_headers())


def _sse_headers() -> dict:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/api/sessions/{session_id}/approve/{approval_id}")
def approve(session_id: str, approval_id: str, req: ApprovalRequest) -> dict:
    state = _ensure_live(session_id)
    if state["approver"].resolve(approval_id, req.approve, req.always):
        return {"ok": True}
    raise HTTPException(404, "approval not found or already resolved")


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: str) -> dict:
    """Request the running turn to stop; takes effect at the next checkpoint."""
    state = _ensure_live(session_id)
    state["stop_event"].set()
    return {"ok": True}


@app.get("/api/commands")
def list_commands() -> dict:
    return dict(COMMANDS)


@app.get("/api/sessions/{session_id}/metrics")
def session_metrics(session_id: str) -> dict:
    """Session aggregate + per-turn metric rows (V3-11)."""
    state = _ensure_live(session_id)
    return {
        "aggregate": state["metrics"].summary(),
        "tasks": state["metrics"].tasks(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.server:app", host="127.0.0.1", port=8001, reload=False)
