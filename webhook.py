"""Flask webhook — Telegram reply / manual approve updates pending_approval.json."""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from state import (
    PENDING_APPROVAL_PATH,
    load_pending_approval,
    load_state,
    save_pending_approval,
    save_state,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

APPROVAL_SECRET = os.getenv("APPROVAL_WEBHOOK_SECRET", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_manual_secret() -> bool:
    """For JSON/query API callers — must send X-Approval-Secret header."""
    if not APPROVAL_SECRET:
        logger.error("APPROVAL_WEBHOOK_SECRET not set — refusing manual approvals")
        return False
    sent = request.headers.get("X-Approval-Secret", "")
    return hmac.compare_digest(sent, APPROVAL_SECRET)


def _check_telegram_secret() -> bool:
    """Telegram sends this header when you set_webhook(..., secret_token=...)."""
    if not TELEGRAM_WEBHOOK_SECRET:
        logger.error("TELEGRAM_WEBHOOK_SECRET not set — refusing Telegram webhook")
        return False
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(sent, TELEGRAM_WEBHOOK_SECRET)


def _apply_approval(*, approved: bool, source: str, note: str = "") -> dict:
    pending = load_pending_approval()
    if pending is None:
        pending = {
            "reason": "unknown",
            "plan": "",
            "requested_at": None,
        }

    pending["approved"] = approved
    pending["status"] = "approved" if approved else "rejected"
    pending["resolved_at"] = _utc_now()
    pending["source"] = source
    if note:
        pending["note"] = note

    save_pending_approval(pending)

    state = load_state()
    state["pending_approval"] = pending
    save_state(state)

    logger.info("Approval updated approved=%s source=%s", approved, source)
    return pending


@app.get("/health")
def health():
    return jsonify({"status": "ok", "pending_file": str(PENDING_APPROVAL_PATH)})


@app.route("/approve", methods=["POST"])  # GET removed — approving must never be a safe/idempotent GET
def approve():
    """
    Update pending_approval.json from Telegram reply webhook or manual POST.

    Manual/API JSON callers MUST send header: X-Approval-Secret: <APPROVAL_WEBHOOK_SECRET>
      {"approved": true}
      {"approved": false, "note": "too risky"}

    Telegram inbound: verified via X-Telegram-Bot-Api-Secret-Token header
    (set this when calling setWebhook — see .env.example / TELEGRAM_WEBHOOK_SECRET).
    Telegram sends JSON "Update" objects, not form data.
    """
    approved: bool | None = None
    note = ""
    source = "api"

    payload = request.get_json(silent=True) or {}
    is_telegram_update = "message" in payload or "edited_message" in payload

    if is_telegram_update:
        if not _check_telegram_secret():
            logger.warning("Rejected Telegram webhook — bad/missing secret token header")
            return jsonify({"ok": False, "error": "invalid_telegram_secret"}), 403

        message = payload.get("message") or payload.get("edited_message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip().upper()

        if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
            logger.warning("Ignored Telegram message from unexpected chat_id=%s", chat_id)
            return jsonify({"ok": True, "ignored": "unexpected_chat_id"})

        source = "telegram"
        note = f"chat_id={chat_id}"
        if text in {"YES", "APPROVE", "APPROVED", "Y", "OK"}:
            approved = True
        elif text in {"NO", "REJECT", "REJECTED", "N"}:
            approved = False
        else:
            # Not a YES/NO reply — nothing to do, but must return 200 so Telegram doesn't retry.
            return jsonify({"ok": True, "ignored": "not_a_yes_no_reply"})
    else:
        # Manual/API path — require shared secret regardless of JSON or query string.
        if not _check_manual_secret():
            logger.warning("Rejected manual approval — bad/missing X-Approval-Secret")
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        if payload and "approved" in payload:
            approved = bool(payload["approved"])
            note = str(payload.get("note") or "")
            source = str(payload.get("source") or "api")
        else:
            q = request.args.get("approved")
            if q is not None:
                approved = q.lower() in ("1", "true", "yes", "y")
                source = "query"

    if approved is None:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Provide approved=true/false (JSON body or query string).",
                }
            ),
            400,
        )

    pending = _apply_approval(approved=approved, source=source, note=note)

    if source == "telegram":
        status = "approved" if approved else "rejected"
        chat_id = TELEGRAM_CHAT_ID
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if bot_token and chat_id:
            try:
                import requests

                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"Request {status}. Thanks."},
                    timeout=10,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not send Telegram ack: %s", exc)
        return jsonify({"ok": True, "pending_approval": pending})

    return jsonify({"ok": True, "pending_approval": pending})


if __name__ == "__main__":
    if not APPROVAL_SECRET:
        logger.warning(
            "APPROVAL_WEBHOOK_SECRET not set in .env — manual /approve calls will be refused"
        )
    if not TELEGRAM_WEBHOOK_SECRET:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set in .env — Telegram /approve calls will be refused"
        )
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
