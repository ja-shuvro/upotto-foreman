"""Load / save project_state.json and pending_approval.json."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "project_state.json"
PENDING_APPROVAL_PATH = BASE_DIR / "pending_approval.json"

DEFAULT_STATE: dict[str, Any] = {
    "completed_tasks": [],
    "pending_approval": None,
    "log_history": [],
    "last_run": None,
    "cost_history": [],
    "current_plan": None,
    "last_summary": None,
    "project_dir": None,
    "run_status": "idle",
    "run_updated_at": None,
    "last_error": None,
    "last_result": None,
    "chat_history": [],
    "bootstrap": None,
    "user_directive": None,
    # Phase engine
    "current_phase": None,
    "phase_status": "idle",  # idle | awaiting_approval | in_progress | blocked | done
    "phase_branch": None,
    "phases_completed": [],
    "phase_total": 0,
    "last_analyzed_commit": None,
    "test_retry_count": 0,
    "phase_analysis": None,
    "tasks": [],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path | None = None) -> dict[str, Any]:
    state_file = path or STATE_PATH
    if not state_file.exists():
        state = deepcopy(DEFAULT_STATE)
        save_state(state, state_file)
        logger.info("Initialized new project state at %s", state_file)
        return state

    try:
        with state_file.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        for key, default in DEFAULT_STATE.items():
            data.setdefault(key, deepcopy(default))
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load state from %s: %s — resetting", state_file, exc)
        state = deepcopy(DEFAULT_STATE)
        save_state(state, state_file)
        return state


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    state_file = path or STATE_PATH
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(state_file)
    logger.info("Saved project state to %s", state_file)


def append_log(state: dict[str, Any], message: str, *, level: str = "INFO") -> None:
    entry = {"timestamp": _utc_now(), "level": level, "message": message}
    state.setdefault("log_history", []).append(entry)
    logger.log(getattr(logging, level.upper(), logging.INFO), message)


def mark_completed(state: dict[str, Any], task_id: str, detail: str = "") -> None:
    state.setdefault("completed_tasks", []).append(
        {
            "task_id": task_id,
            "detail": detail,
            "completed_at": _utc_now(),
        }
    )


def set_pending_approval(
    state: dict[str, Any],
    reason: str,
    plan: str = "",
    *,
    kind: str | None = None,
) -> None:
    pending = {
        "reason": reason,
        "plan": plan,
        "requested_at": _utc_now(),
        "status": "pending",
        "approved": False,
    }
    if kind:
        pending["kind"] = kind
    state["pending_approval"] = pending
    save_pending_approval(pending)


def clear_pending_approval(state: dict[str, Any]) -> None:
    state["pending_approval"] = None
    if PENDING_APPROVAL_PATH.exists():
        PENDING_APPROVAL_PATH.unlink()


def save_pending_approval(pending: dict[str, Any]) -> None:
    PENDING_APPROVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PENDING_APPROVAL_PATH.open("w", encoding="utf-8") as fh:
        json.dump(pending, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    logger.info("Wrote pending approval to %s", PENDING_APPROVAL_PATH)


def load_pending_approval() -> dict[str, Any] | None:
    if not PENDING_APPROVAL_PATH.exists():
        return None
    try:
        with PENDING_APPROVAL_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load pending_approval.json: %s", exc)
        return None


def is_approval_granted() -> bool:
    pending = load_pending_approval()
    if not pending:
        return False
    return bool(pending.get("approved") or pending.get("status") == "approved")


def update_after_run(
    state: dict[str, Any],
    *,
    plan: str | None = None,
    summary: str | None = None,
    cost_summary: dict[str, Any] | None = None,
    needs_approval: str | None = None,
) -> dict[str, Any]:
    state["last_run"] = _utc_now()
    if plan is not None:
        state["current_plan"] = plan
    if summary:
        append_log(state, f"Daily summary: {summary}")
    if cost_summary:
        state.setdefault("cost_history", []).append(
            {"timestamp": _utc_now(), **cost_summary}
        )
    if needs_approval:
        set_pending_approval(state, needs_approval, plan=plan or "")
        append_log(state, f"NEEDS_APPROVAL: {needs_approval}", level="WARNING")
    save_state(state)
    return state
