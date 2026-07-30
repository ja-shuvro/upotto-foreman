"""Telegram long-polling — receive messages and reply ON Telegram (no ngrok needed).

Flow:
  You (Telegram) → Bot API getUpdates → Agent → sendMessage → You (Telegram)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

_stop = threading.Event()
_thread: threading.Thread | None = None
_offset = 0


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value if value not in (None, "") else default


def _bot_token() -> str | None:
    return _env("TELEGRAM_BOT_TOKEN")


def _allowed_chat_id() -> str | None:
    return _env("TELEGRAM_CHAT_ID")


def _api(method: str, **params: Any) -> dict[str, Any]:
    token = _bot_token()
    if not token:
        return {"ok": False, "error": "missing_token"}
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        resp = requests.post(url, json=params, timeout=params.get("timeout", 35) + 10)
        return resp.json()
    except requests.RequestException as exc:
        logger.error("Telegram API %s failed: %s", method, exc)
        return {"ok": False, "error": str(exc)}


def reply_telegram(text: str, chat_id: str | None = None, *, markdown: bool = True) -> dict[str, Any]:
    """Always reply on Telegram to the user's chat (HTML markdown when possible)."""
    from notify import send_telegram

    if markdown:
        from md_view import markdown_to_telegram_html

        html_body = markdown_to_telegram_html(text)
        return send_telegram(html_body, chat_id=chat_id or _allowed_chat_id(), parse_mode="HTML")
    return send_telegram(text, chat_id=chat_id or _allowed_chat_id(), parse_mode=None)

def _delete_webhook() -> None:
    """Polling and webhook cannot both be active — drop webhook for local desktop use."""
    data = _api("deleteWebhook", drop_pending_updates=False)
    if data.get("ok"):
        logger.info("Telegram webhook cleared — using long polling")
    else:
        logger.warning("deleteWebhook: %s", data)


def _handle_update(update: dict[str, Any]) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (message.get("text") or "").strip()
    if not text:
        return

    allowed = _allowed_chat_id()
    if allowed and chat_id != str(allowed):
        logger.warning("Ignoring Telegram chat_id=%s (expected %s)", chat_id, allowed)
        return

    from telegram_chat import answer_project_question, handle_telegram_text
    from webhook import apply_approval

    routed = handle_telegram_text(text)

    if routed["kind"] == "approve":
        approved = bool(routed["approved"])
        apply_approval(approved=approved, source="telegram", note=f"chat_id={chat_id}")
        status = "approved" if approved else "rejected"
        reply_telegram(f"Request {status}. Thanks.", chat_id=chat_id)
        return

    if routed.get("async") and routed["kind"] == "chat":
        question = routed.get("question") or text
        # Acknowledge on Telegram, then send the real agent reply on Telegram
        reply_telegram("Thinking…", chat_id=chat_id, markdown=False)
        try:
            answer = answer_project_question(question, source="telegram")
            reply_telegram(answer, chat_id=chat_id, markdown=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Telegram agent reply failed")
            reply_telegram(f"Sorry, failed: {exc}", chat_id=chat_id, markdown=False)
        return

    reply = routed.get("reply")
    if reply:
        reply_telegram(reply, chat_id=chat_id)


def _poll_loop() -> None:
    global _offset

    token = _bot_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN missing — Telegram polling disabled")
        return

    _delete_webhook()
    logger.info(
        "Telegram polling started — message the bot to ask about the project; "
        "replies come back on Telegram"
    )

    while not _stop.is_set():
        data = _api(
            "getUpdates",
            offset=_offset,
            timeout=25,
            allowed_updates=["message", "edited_message"],
        )
        if not data.get("ok"):
            time.sleep(2)
            continue

        for update in data.get("result") or []:
            try:
                uid = int(update.get("update_id", 0)) + 1
                if uid > _offset:
                    _offset = uid
                _handle_update(update)
            except Exception:  # noqa: BLE001
                logger.exception("Failed handling Telegram update")


def start_telegram_polling() -> None:
    """Start background long-poller (call once from app_main)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    enabled = (_env("TELEGRAM_POLLING", "true") or "true").lower() in ("1", "true", "yes")
    if not enabled:
        logger.info("TELEGRAM_POLLING disabled")
        return
    if not _bot_token():
        logger.warning("No TELEGRAM_BOT_TOKEN — skip polling")
        return

    _stop.clear()
    _thread = threading.Thread(target=_poll_loop, name="telegram-poller", daemon=True)
    _thread.start()


def stop_telegram_polling() -> None:
    _stop.set()
