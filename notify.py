"""Telegram (free) and email (SMTP, free) notification helpers."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Telegram hard limit is 4096 chars per message — plenty of room, but clip anyway.
TELEGRAM_MAX_CHARS = 3800
TELEGRAM_API_BASE = "https://api.telegram.org"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value if value not in (None, "") else default


def _clip(text: str, limit: int = TELEGRAM_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text)} chars total]"


def send_telegram(
    body: str,
    *,
    chat_id: str | None = None,
    parse_mode: str | None = "HTML",
) -> dict[str, Any]:
    """Send a message via Telegram Bot API — free, no per-message cost.

    parse_mode defaults to HTML so Markdown-converted agent replies render bold/code/lists.
    Pass parse_mode=None for plain text.
    """
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    target_chat_id = chat_id or _env("TELEGRAM_CHAT_ID")

    if not all([bot_token, target_chat_id]):
        logger.error("Telegram env vars incomplete — skipping send")
        return {"ok": False, "error": "missing_telegram_config"}

    safe_body = _clip(body)
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {"chat_id": target_chat_id, "text": safe_body}
    if parse_mode:
        payload["parse_mode"] = parse_mode
        payload["disable_web_page_preview"] = True

    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if not data.get("ok") and parse_mode:
            # Fallback plain text if HTML parse fails
            logger.warning("Telegram HTML send failed (%s) — retrying plain", data)
            payload.pop("parse_mode", None)
            resp = requests.post(
                url,
                json={"chat_id": target_chat_id, "text": safe_body},
                timeout=15,
            )
            data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram send failed: %s", data)
            return {"ok": False, "error": data.get("description", "unknown_error")}
        logger.info("Telegram sent message_id=%s", data.get("result", {}).get("message_id"))
        return {"ok": True, "message_id": data.get("result", {}).get("message_id")}
    except requests.RequestException as exc:
        logger.error("Telegram request failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected Telegram error")
        return {"ok": False, "error": str(exc)}


def send_email(
    subject: str,
    body: str,
    *,
    to_addr: str | None = None,
) -> dict[str, Any]:
    """Send email via SMTP as backup notification channel. Creds from .env. Free (Gmail)."""
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    from_addr = _env("SMTP_FROM", user)
    to = to_addr or _env("SMTP_TO")
    use_tls = (_env("SMTP_USE_TLS", "true") or "true").lower() in ("1", "true", "yes")

    if not all([host, user, password, from_addr, to]):
        logger.error("SMTP env vars incomplete — skipping send")
        return {"ok": False, "error": "missing_smtp_config"}

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
        logger.info("Email sent to %s subject=%s", to, subject)
        return {"ok": True, "to": to}
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("SMTP send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def notify_daily_summary(summary: str, cost_line: str = "") -> None:
    body = summary.strip()
    if cost_line:
        body = f"{body}\n\n{cost_line}"
    send_telegram(f"Daily Agent Summary\n\n{body}")
    send_email("Daily Project Agent Summary", body)


def notify_approval_request(reason: str, plan_excerpt: str = "") -> None:
    webhook_base = _env("WEBHOOK_PUBLIC_URL", "http://localhost:5000")
    body = (
        "Approval needed\n\n"
        f"Reason: {reason}\n\n"
        f"{plan_excerpt[:1500]}\n\n"
        "Reply YES or NO in Telegram, or use the desktop Approvals screen.\n"
        f'API: POST {webhook_base}/approve with X-Approval-Secret and {{"approved": true}}'
    )
    send_telegram(body)
    send_email("NEEDS_APPROVAL — Project Agent", body)


def notify_run_event(event: str, detail: str = "") -> None:
    """Push run lifecycle events to Telegram (start/stop/fail/pending)."""
    labels = {
        "started": "RUNNING — agent loop started",
        "finished": "IDLE — agent loop finished",
        "failed": "IDLE — agent loop FAILED",
        "pending_approval": "PENDING APPROVAL — waiting for YES/NO",
        "stopping": "STOPPING — stop requested",
        "stopped": "IDLE — stopped",
    }
    title = labels.get(event, f"Event: {event}")
    body = title if not detail else f"{title}\n\n{detail}"
    send_telegram(body)


def notify_bootstrap_report(mode: str, message: str, report: str = "") -> None:
    """Send docs-first bootstrap / audit report via Telegram + email."""
    from md_view import markdown_to_telegram_html

    header = f"Bootstrap [{mode}]\n\n{message.strip()}"
    body = header if not report else f"{header}\n\n{report.strip()}"
    # Prefer HTML markdown for Telegram; clip happens inside send_telegram
    html_body = markdown_to_telegram_html(body)
    send_telegram(html_body, parse_mode="HTML")
    send_email(f"Upotto Foreman Bootstrap — {mode}", body)

