"""Live CrewAI activity stream — event bus → queue → SSE clients."""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_HISTORY = 200
SUBSCRIBER_QUEUE_SIZE = 100
KEEPALIVE_SECONDS = 15.0

# High-level UI phase: Idle | Planning | Developing | Awaiting Approval | Error
Phase = str

_lock = threading.RLock()
_history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
_subscribers: list[queue.Queue] = []
_phase: Phase = "Idle"
_phase_detail: str = ""
_run_cost_usd: float = 0.0
_task_started_at: dict[str, float] = {}
_listeners_installed = False
_install_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_live_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "phase": _phase,
            "phase_detail": _phase_detail,
            "run_cost_usd": round(_run_cost_usd, 6),
            "recent": list(_history)[-50:],
        }


def set_run_cost(total_cost_usd: float) -> None:
    global _run_cost_usd
    with _lock:
        _run_cost_usd = float(total_cost_usd or 0.0)


def emit_cost(summary: dict[str, Any] | None = None, *, total_cost_usd: float | None = None) -> None:
    """Push a cost_update event (from cost_tracker after LLM usage)."""
    total = total_cost_usd
    if total is None and summary:
        total = float(summary.get("total_cost_usd") or 0.0)
    if total is None:
        return
    set_run_cost(total)
    push_event(
        {
            "type": "cost_update",
            "message": f"Run cost ${total:.6f}",
            "total_cost_usd": round(total, 6),
            "input_tokens": (summary or {}).get("total_input_tokens"),
            "output_tokens": (summary or {}).get("total_output_tokens"),
        }
    )


def emit_phase(phase: Phase, detail: str = "") -> None:
    global _phase, _phase_detail
    label = _normalize_phase(phase)
    with _lock:
        _phase = label
        _phase_detail = detail or ""
    push_event(
        {
            "type": "phase",
            "phase": label,
            "message": detail or label,
            "phase_detail": detail or "",
        }
    )


def reset_run_live(*, phase: Phase = "Planning") -> None:
    """Call at the start of a daily loop run."""
    global _run_cost_usd, _task_started_at
    with _lock:
        _run_cost_usd = 0.0
        _task_started_at.clear()
    emit_phase(phase, "Agent run started")


def _normalize_phase(phase: str) -> Phase:
    key = (phase or "Idle").strip().lower().replace("_", " ").replace("-", " ")
    mapping = {
        "idle": "Idle",
        "planning": "Planning",
        "developing": "Developing",
        "development": "Developing",
        "running": "Planning",
        "awaiting approval": "Awaiting Approval",
        "pending approval": "Awaiting Approval",
        "pending_approval": "Awaiting Approval",
        "error": "Error",
        "failed": "Error",
        "stopping": "Idle",
        "stopped": "Idle",
        "completed": "Idle",
    }
    if key in mapping:
        return mapping[key]
    # allow already-pretty labels
    for pretty in ("Idle", "Planning", "Developing", "Awaiting Approval", "Error"):
        if phase == pretty:
            return pretty
    return phase.strip().title() or "Idle"


def push_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Enqueue a UI event for all SSE subscribers (non-blocking)."""
    event = {
        "id": payload.get("id") or str(uuid.uuid4()),
        "timestamp": payload.get("timestamp") or _utc_now(),
        "type": payload.get("type") or "info",
        "message": payload.get("message") or "",
        "agent": payload.get("agent"),
        "tool": payload.get("tool"),
        "task": payload.get("task"),
        "phase": payload.get("phase"),
        "elapsed_ms": payload.get("elapsed_ms"),
        "error": payload.get("error"),
        "total_cost_usd": payload.get("total_cost_usd"),
        "meta": payload.get("meta") or {},
    }
    # drop Nones for smaller SSE payloads
    event = {k: v for k, v in event.items() if v is not None}

    with _lock:
        _history.append(event)
        dead: list[queue.Queue] = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
        for q in dead:
            if q in _subscribers:
                _subscribers.remove(q)
    return event


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def _agent_name(event: Any) -> str | None:
    agent = getattr(event, "agent", None)
    if agent is not None:
        role = getattr(agent, "role", None)
        if role:
            return str(role)
    role = getattr(event, "agent_role", None)
    if role:
        return str(role)
    return None


def _task_label(event: Any) -> str | None:
    name = getattr(event, "task_name", None)
    if name:
        text = str(name).strip().replace("\n", " ")
        return text[:120] + ("…" if len(text) > 120 else "")
    task = getattr(event, "task", None)
    if task is not None:
        tname = getattr(task, "name", None) or getattr(task, "description", None)
        if tname:
            text = str(tname).strip().replace("\n", " ")
            return text[:120] + ("…" if len(text) > 120 else "")
    return None


def _task_key(event: Any) -> str:
    tid = getattr(event, "task_id", None)
    if tid:
        return str(tid)
    task = getattr(event, "task", None)
    if task is not None and getattr(task, "id", None) is not None:
        return str(task.id)
    return _task_label(event) or "task"


def _short_tool(name: str | None) -> str:
    if not name:
        return "tool"
    # LocalCodeExecutionTool → nicer label
    n = name.replace("Tool", "").strip() or name
    return n


def _handle_crewai_event(_source: Any, event: Any) -> None:
    try:
        etype = getattr(event, "type", None) or type(event).__name__
        ts = getattr(event, "timestamp", None)
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else _utc_now()
        agent = _agent_name(event)
        task = _task_label(event)
        tool = getattr(event, "tool_name", None)

        if etype == "task_started":
            key = _task_key(event)
            with _lock:
                _task_started_at[key] = time.monotonic()
            # Heuristic phase from task text
            low = (task or "").lower()
            if any(k in low for k in ("plan", "architect", "audit", "bootstrap")):
                emit_phase("Planning", f"Task started: {task or 'plan'}")
            elif any(k in low for k in ("dev", "implement", "code", "summary")):
                emit_phase("Developing", f"Task started: {task or 'develop'}")
            push_event(
                {
                    "type": "task_started",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "task": task,
                    "message": f"Task started — {task or 'untitled'}",
                }
            )
            return

        if etype == "task_completed":
            key = _task_key(event)
            elapsed_ms = None
            with _lock:
                started = _task_started_at.pop(key, None)
            if started is not None:
                elapsed_ms = int((time.monotonic() - started) * 1000)
            push_event(
                {
                    "type": "task_completed",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "task": task,
                    "elapsed_ms": elapsed_ms,
                    "message": f"Task completed — {task or 'untitled'}"
                    + (f" ({elapsed_ms / 1000:.1f}s)" if elapsed_ms is not None else ""),
                }
            )
            return

        if etype == "task_failed":
            err = str(getattr(event, "error", "") or "task failed")
            emit_phase("Error", err[:200])
            push_event(
                {
                    "type": "task_failed",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "task": task,
                    "error": err[:500],
                    "message": f"Task failed — {err[:200]}",
                }
            )
            return

        if etype == "agent_execution_started":
            push_event(
                {
                    "type": "agent_execution_started",
                    "timestamp": ts_iso,
                    "agent": agent or "Agent",
                    "task": task,
                    "message": f"{agent or 'Agent'} started working",
                }
            )
            return

        if etype == "agent_execution_completed":
            push_event(
                {
                    "type": "agent_execution_completed",
                    "timestamp": ts_iso,
                    "agent": agent or "Agent",
                    "task": task,
                    "message": f"{agent or 'Agent'} finished",
                }
            )
            return

        if etype == "agent_execution_error":
            err = str(getattr(event, "error", "") or "agent error")
            emit_phase("Error", err[:200])
            push_event(
                {
                    "type": "agent_execution_error",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "error": err[:500],
                    "message": f"Agent error — {err[:200]}",
                }
            )
            return

        if etype == "tool_usage_started":
            tname = _short_tool(str(tool) if tool else None)
            push_event(
                {
                    "type": "tool_usage_started",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "tool": tname,
                    "task": task,
                    "message": f"Running {tname}…",
                }
            )
            return

        if etype == "tool_usage_finished":
            tname = _short_tool(str(tool) if tool else None)
            push_event(
                {
                    "type": "tool_usage_finished",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "tool": tname,
                    "task": task,
                    "message": f"{tname} completed",
                }
            )
            return

        if etype == "tool_usage_error":
            tname = _short_tool(str(tool) if tool else None)
            err = str(getattr(event, "error", "") or "tool error")
            push_event(
                {
                    "type": "tool_usage_error",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "tool": tname,
                    "error": err[:500],
                    "message": f"{tname} failed — {err[:160]}",
                }
            )
            return

        if etype == "llm_call_started":
            model = getattr(event, "model", None) or ""
            push_event(
                {
                    "type": "llm_call_started",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "message": f"LLM call{' · ' + model if model else ''}…",
                    "meta": {"model": model} if model else {},
                }
            )
            return

        if etype == "llm_call_failed":
            err = str(getattr(event, "error", "") or "LLM failed")
            emit_phase("Error", err[:200])
            push_event(
                {
                    "type": "llm_call_failed",
                    "timestamp": ts_iso,
                    "agent": agent,
                    "error": err[:500],
                    "message": f"LLM error — {err[:200]}",
                }
            )
            return

        if etype == "crew_kickoff_started":
            name = getattr(event, "crew_name", None) or "Crew"
            push_event(
                {
                    "type": "crew_kickoff_started",
                    "timestamp": ts_iso,
                    "message": f"Crew started — {name}",
                    "meta": {"crew": name},
                }
            )
            return

        if etype == "crew_kickoff_completed":
            name = getattr(event, "crew_name", None) or "Crew"
            tokens = getattr(event, "total_tokens", 0) or 0
            push_event(
                {
                    "type": "crew_kickoff_completed",
                    "timestamp": ts_iso,
                    "message": f"Crew completed — {name}"
                    + (f" ({tokens} tokens)" if tokens else ""),
                    "meta": {"crew": name, "total_tokens": tokens},
                }
            )
            return

        if etype == "crew_kickoff_failed":
            err = str(getattr(event, "error", "") or "crew failed")
            emit_phase("Error", err[:200])
            push_event(
                {
                    "type": "crew_kickoff_failed",
                    "timestamp": ts_iso,
                    "error": err[:500],
                    "message": f"Crew failed — {err[:200]}",
                }
            )
            return
    except Exception:  # noqa: BLE001
        logger.exception("live_events handler failed for %s", type(event).__name__)


def ensure_listeners() -> bool:
    """Subscribe to CrewAIEventsBus once. Safe to call repeatedly."""
    global _listeners_installed
    with _install_lock:
        if _listeners_installed:
            return True
        try:
            from crewai.events import crewai_event_bus
            from crewai.events.types.agent_events import (
                AgentExecutionCompletedEvent,
                AgentExecutionErrorEvent,
                AgentExecutionStartedEvent,
            )
            from crewai.events.types.crew_events import (
                CrewKickoffCompletedEvent,
                CrewKickoffFailedEvent,
                CrewKickoffStartedEvent,
            )
            from crewai.events.types.llm_events import (
                LLMCallFailedEvent,
                LLMCallStartedEvent,
            )
            from crewai.events.types.task_events import (
                TaskCompletedEvent,
                TaskFailedEvent,
                TaskStartedEvent,
            )
            from crewai.events.types.tool_usage_events import (
                ToolUsageErrorEvent,
                ToolUsageFinishedEvent,
                ToolUsageStartedEvent,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CrewAI events unavailable: %s", exc)
            return False

        types = [
            TaskStartedEvent,
            TaskCompletedEvent,
            TaskFailedEvent,
            AgentExecutionStartedEvent,
            AgentExecutionCompletedEvent,
            AgentExecutionErrorEvent,
            ToolUsageStartedEvent,
            ToolUsageFinishedEvent,
            ToolUsageErrorEvent,
            LLMCallStartedEvent,
            LLMCallFailedEvent,
            CrewKickoffStartedEvent,
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
        ]
        for et in types:
            crewai_event_bus.register_handler(et, _handle_crewai_event)

        _listeners_installed = True
        logger.info("Live activity listeners registered (%d event types)", len(types))
        return True


def sse_stream(gen_id: str | None = None) -> Callable[[], Any]:
    """Return a generator factory for Flask Response streaming."""

    def generate():
        import json

        ensure_listeners()
        q = subscribe()
        client = gen_id or str(uuid.uuid4())[:8]
        try:
            snap = get_live_snapshot()
            hello = {
                "type": "connected",
                "id": str(uuid.uuid4()),
                "timestamp": _utc_now(),
                "message": "Live feed connected",
                "phase": snap["phase"],
                "total_cost_usd": snap["run_cost_usd"],
                "meta": {"client": client},
            }
            yield f"data: {json.dumps(hello)}\n\n"
            # optional: last few events for context (not full history replay)
            for past in snap["recent"][-15:]:
                yield f"data: {json.dumps(past)}\n\n"

            while True:
                try:
                    ev = q.get(timeout=KEEPALIVE_SECONDS)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("SSE stream error client=%s", client)
        finally:
            unsubscribe(q)

    return generate
