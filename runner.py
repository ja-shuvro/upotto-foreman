"""Background job runner for daily_loop — one run at a time."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from state import append_log, load_state, save_state

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_result: dict[str, Any] | None = None
_last_error: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_run_status(status: str, **extra: Any) -> None:
    state = load_state()
    state["run_status"] = status
    state["run_updated_at"] = _utc_now()
    for k, v in extra.items():
        state[k] = v
    save_state(state)


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def get_runner_snapshot() -> dict[str, Any]:
    state = load_state()
    status = state.get("run_status") or "idle"
    if is_running():
        status = "running"
    elif status == "running":
        # thread died without cleanup
        status = "idle"
    return {
        "status": status,
        "running": is_running(),
        "run_updated_at": state.get("run_updated_at"),
        "last_error": _last_error or state.get("last_error"),
        "last_result": _last_result or state.get("last_result"),
        "pending_approval": state.get("pending_approval"),
        "current_plan": state.get("current_plan"),
        "last_summary": state.get("last_summary"),
        "last_run": state.get("last_run"),
        "project_dir": state.get("project_dir"),
        "cost_history": (state.get("cost_history") or [])[-5:],
    }


def start_run(*, skip_human_input: bool = True) -> dict[str, Any]:
    global _thread, _last_error, _last_result

    with _lock:
        if is_running():
            return {"ok": False, "error": "already_running", "status": "running"}

        _stop_event.clear()
        _last_error = None
        _last_result = None
        _set_run_status("running")

        def _target() -> None:
            global _last_error, _last_result, _thread
            from notify import notify_run_event

            notify_run_event("started", "Agent daily loop started.")
            try:
                from daily_loop import run_daily_loop

                if _stop_event.is_set():
                    _set_run_status("idle")
                    notify_run_event("stopped", "Run cancelled before start.")
                    return

                result = run_daily_loop(skip_human_input=skip_human_input)
                _last_result = result
                state = load_state()
                state["last_result"] = {
                    "status": result.get("status"),
                    "summary": (result.get("summary") or "")[:2000],
                }
                if result.get("summary"):
                    state["last_summary"] = result["summary"]
                status = result.get("status") or "completed"
                if status == "pending_approval":
                    _set_run_status("pending_approval")
                    notify_run_event(
                        "pending_approval",
                        f"Paused for approval: {result.get('reason', '')}",
                    )
                else:
                    _set_run_status("idle")
                    notify_run_event("finished", f"Run finished: {status}")
                save_state(state)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Background run failed")
                _last_error = str(exc)
                state = load_state()
                append_log(state, f"Run failed: {exc}", level="ERROR")
                state["last_error"] = str(exc)
                save_state(state)
                _set_run_status("idle", last_error=str(exc))
                try:
                    from notify import notify_run_event

                    notify_run_event("failed", f"Run failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                _thread = None

        _thread = threading.Thread(target=_target, name="foreman-daily-loop", daemon=True)
        _thread.start()
        return {"ok": True, "status": "running"}


def stop_run() -> dict[str, Any]:
    """Request cooperative stop. Hard-kill of CrewAI mid-call is not supported;
    sets flag and marks status stopped for UI/Telegram."""
    if not is_running():
        _set_run_status("idle")
        return {"ok": True, "status": "idle", "message": "not_running"}

    _stop_event.set()
    _set_run_status("stopping")
    try:
        from notify import notify_run_event

        notify_run_event("stopping", "Stop requested — current LLM call may finish first.")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "status": "stopping"}


def stop_requested() -> bool:
    return _stop_event.is_set()
