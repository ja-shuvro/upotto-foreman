"""Telegram / local status Q&A — replies always go back to Telegram when sourced there."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import (
    ALLOWED_BASE_DIR,
    AUTHORIZED_TELEGRAM_CHAT_ID,
    get_project_dir,
    list_allowed_projects,
    read_doc_excerpts,
    resolve_project_under_base,
    set_project_dir,
)
from runner import get_runner_snapshot
from state import load_state, save_state

logger = logging.getLogger(__name__)


def format_status_message() -> str:
    snap = get_runner_snapshot()
    status = snap.get("status") or "idle"
    try:
        project = str(get_project_dir())
    except Exception:  # noqa: BLE001
        project = snap.get("project_dir") or "?"
    pending = snap.get("pending_approval")
    plan = (snap.get("current_plan") or "")[:800]
    summary = (snap.get("last_summary") or "")[:800]
    cost_tail = snap.get("cost_history") or []
    last_cost = cost_tail[-1] if cost_tail else None
    cost_line = ""
    if isinstance(last_cost, dict):
        cost_line = f"Last cost: ${last_cost.get('total_cost_usd', '?')}"

    state = load_state()
    directive = (state.get("user_directive") or "").strip()

    lines = [
        f"Status: {status.upper()}",
        f"Project: {project}",
        f"Last run: {snap.get('last_run') or 'never'}",
    ]
    if directive:
        lines.append(f"Queued task: {directive[:300]}")
    if cost_line:
        lines.append(cost_line)
    if pending and (pending.get("status") == "pending" or not pending.get("approved")):
        lines.append(f"Pending approval: {pending.get('reason', '')}")
    if summary:
        lines.append(f"\nLast summary:\n{summary}")
    elif plan:
        lines.append(f"\nCurrent plan (excerpt):\n{plan}")
    if snap.get("last_error"):
        lines.append(f"\nLast error: {snap['last_error']}")
    return "\n".join(lines)


def answer_project_question(question: str, *, source: str = "desktop") -> str:
    """Answer a free-text question about project status using Crew LLM (one shot)."""
    state = load_state()
    context = (
        f"Runner status:\n{format_status_message()}\n\n"
        f"Log history (last 15):\n"
        + "\n".join(
            f"- [{e.get('timestamp')}] {e.get('message')}"
            for e in (state.get("log_history") or [])[-15:]
        )
        + "\n\nSpec docs excerpts:\n"
        + read_doc_excerpts(1500)
    )

    prompt = (
        "You are the Upotto Foreman status assistant — the same agent users reach via "
        "Telegram and the desktop Chat panel. Answer clearly using GitHub-flavored Markdown "
        "(## headings, **bold**, `code`, - bullets). Keep under 3500 chars. "
        "Use only the provided context. If unknown, say so.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"USER QUESTION:\n{question}\n"
    )

    try:
        from agents import build_crew_llm

        llm = build_crew_llm(temperature=0.2)
        if hasattr(llm, "call"):
            reply = llm.call([{"role": "user", "content": prompt}])
        elif hasattr(llm, "invoke"):
            reply = llm.invoke(prompt)
        else:
            reply = str(llm)
        text = str(getattr(reply, "content", None) or reply).strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Status chat LLM failed")
        text = (
            f"Could not reach the LLM ({exc}). Here is raw status instead:\n\n"
            f"{format_status_message()}"
        )

    text = text[:3800]
    append_chat_turn(question, text, source=source)
    return text


def append_chat_turn(question: str, answer: str, *, source: str = "desktop") -> None:
    state = load_state()
    history = state.setdefault("chat_history", [])
    history.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "answer": answer[:2000],
            "source": source,
        }
    )
    state["chat_history"] = history[-80:]
    save_state(state)


def help_text() -> str:
    return (
        "Upotto Foreman Telegram commands:\n"
        "/status — running / idle / pending approval\n"
        "/currentproject — active PROJECT_DIR\n"
        "/listprojects — folders under C:\\langs\\projects\n"
        "/setproject <folder> — switch project (sandbox)\n"
        "/newtask <text> — queue priority directive for next run\n"
        "/runnow — start daily loop now\n"
        "/help — this message\n"
        "YES / NO — approve or reject pending change\n"
        "Any other message — chat with the agent (replies here on Telegram)\n"
    )


def _cmd_setproject(arg: str) -> str:
    try:
        path = resolve_project_under_base(arg)
        resolved = set_project_dir(path)
        return f"Project switched to: {resolved}"
    except ValueError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("setproject failed")
        return f"ERROR: could not switch project ({exc})"


def _cmd_newtask(arg: str) -> str:
    text = (arg or "").strip()
    if not text:
        return "ERROR: usage — /newtask <description>"
    state = load_state()
    state["user_directive"] = text
    save_state(state)
    return f"Task queued for next run: {text}"


def _cmd_listprojects() -> str:
    names = list_allowed_projects()
    if not names:
        return f"No projects found under {ALLOWED_BASE_DIR}"
    lines = [f"Projects under {ALLOWED_BASE_DIR}:"]
    for i, name in enumerate(names, 1):
        lines.append(f"{i}. {name}")
    lines.append("\nUse: /setproject <folder>  e.g. /setproject frontend/agriflow-landing")
    return "\n".join(lines)


def _cmd_currentproject() -> str:
    try:
        return f"Current project: {get_project_dir()}"
    except ValueError as exc:
        return f"ERROR: {exc}"


def _cmd_runnow() -> str:
    from runner import get_runner_snapshot, start_run

    snap = get_runner_snapshot()
    if snap.get("running"):
        return "ERROR: a run is already in progress"
    result = start_run(skip_human_input=True)
    if not result.get("ok"):
        return f"ERROR: {result.get('error') or 'could not start'}"
    try:
        project = get_project_dir()
    except ValueError as exc:
        return f"ERROR: {exc}"
    return f"Run started for: {project}"


def handle_telegram_text(text: str) -> dict[str, Any]:
    """Route a Telegram message. Returns {kind, reply, approved?}."""
    raw = (text or "").strip()
    upper = raw.upper()

    if upper in {"YES", "APPROVE", "APPROVED", "Y", "OK"}:
        return {"kind": "approve", "approved": True, "reply": None}
    if upper in {"NO", "REJECT", "REJECTED", "N"}:
        return {"kind": "approve", "approved": False, "reply": None}

    # Parse /command args (Telegram may send /cmd@BotName)
    if raw.startswith("/"):
        parts = raw.split(maxsplit=1)
        cmd_token = parts[0][1:]
        cmd = cmd_token.split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"status", "stat"}:
            return {"kind": "status", "reply": format_status_message()}
        if cmd in {"help", "start"}:
            return {"kind": "help", "reply": help_text()}
        if cmd == "setproject":
            return {"kind": "setproject", "reply": _cmd_setproject(arg)}
        if cmd == "newtask":
            return {"kind": "newtask", "reply": _cmd_newtask(arg)}
        if cmd == "listprojects":
            return {"kind": "listprojects", "reply": _cmd_listprojects()}
        if cmd == "currentproject":
            return {"kind": "currentproject", "reply": _cmd_currentproject()}
        if cmd == "runnow":
            return {"kind": "runnow", "reply": _cmd_runnow()}

    # Legacy bare commands without slash
    lower = raw.lower()
    if lower in {"status", "stat"}:
        return {"kind": "status", "reply": format_status_message()}
    if lower in {"help", "start"}:
        return {"kind": "help", "reply": help_text()}

    if not raw:
        return {"kind": "empty", "reply": help_text()}

    return {"kind": "chat", "reply": None, "question": raw, "async": True}


def is_authorized_telegram_chat(chat_id: str | int | None) -> bool:
    """Strict gate: only AUTHORIZED_TELEGRAM_CHAT_ID may control the bot."""
    return str(chat_id or "").strip() == AUTHORIZED_TELEGRAM_CHAT_ID
