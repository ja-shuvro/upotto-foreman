"""Telegram / local status Q&A — replies always go back to Telegram when sourced there."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import get_project_dir, read_doc_excerpts
from runner import get_runner_snapshot
from state import load_state, save_state

logger = logging.getLogger(__name__)


def format_status_message() -> str:
    snap = get_runner_snapshot()
    status = snap.get("status") or "idle"
    project = snap.get("project_dir") or str(get_project_dir())
    pending = snap.get("pending_approval")
    plan = (snap.get("current_plan") or "")[:800]
    summary = (snap.get("last_summary") or "")[:800]
    cost_tail = snap.get("cost_history") or []
    last_cost = cost_tail[-1] if cost_tail else None
    cost_line = ""
    if isinstance(last_cost, dict):
        cost_line = f"Last cost: ${last_cost.get('total_cost_usd', '?')}"

    lines = [
        f"Status: {status.upper()}",
        f"Project: {project}",
        f"Last run: {snap.get('last_run') or 'never'}",
    ]
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
        "/help — this message\n"
        "YES / NO — approve or reject pending change\n"
        "Any other message — chat with the agent (replies here on Telegram)\n"
    )


def handle_telegram_text(text: str) -> dict[str, Any]:
    """Route a Telegram message. Returns {kind, reply, approved?}."""
    raw = (text or "").strip()
    upper = raw.upper()

    if upper in {"YES", "APPROVE", "APPROVED", "Y", "OK"}:
        return {"kind": "approve", "approved": True, "reply": None}
    if upper in {"NO", "REJECT", "REJECTED", "N"}:
        return {"kind": "approve", "approved": False, "reply": None}

    cmd = raw.lower().lstrip("/")
    if cmd in {"status", "stat"}:
        return {"kind": "status", "reply": format_status_message()}
    if cmd in {"help", "start"}:
        return {"kind": "help", "reply": help_text()}

    if not raw:
        return {"kind": "empty", "reply": help_text()}

    # Chat — caller should reply async so Telegram webhook doesn't time out
    return {"kind": "chat", "reply": None, "question": raw, "async": True}
