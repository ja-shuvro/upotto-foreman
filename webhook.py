"""Upotto Foreman — local Flask API, web SPA, Telegram webhook."""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from config import (
    EDITABLE_SETTINGS,
    REQUIRED_DOCS,
    SECRET_SETTINGS,
    docs_checklist,
    get_docs_dir,
    get_project_dir,
    is_git_repo,
    mask_secret,
    read_env_settings,
    set_project_dir,
    write_env_settings,
)
from runner import get_runner_snapshot, start_run, stop_run
from state import (
    PENDING_APPROVAL_PATH,
    BASE_DIR,
    load_pending_approval,
    load_state,
    save_pending_approval,
    save_state,
)
from startup_windows import is_startup_enabled, set_startup
from telegram_chat import handle_telegram_text

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

try:
    from live_events import ensure_listeners

    ensure_listeners()
except Exception:  # noqa: BLE001
    pass

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

APPROVAL_SECRET = os.getenv("APPROVAL_WEBHOOK_SECRET", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reload_secrets() -> None:
    global APPROVAL_SECRET, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_CHAT_ID
    APPROVAL_SECRET = os.getenv("APPROVAL_WEBHOOK_SECRET", "")
    TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _check_manual_secret() -> bool:
    _reload_secrets()
    if not APPROVAL_SECRET:
        return False
    sent = request.headers.get("X-Approval-Secret", "")
    return hmac.compare_digest(sent, APPROVAL_SECRET)


def _check_telegram_secret() -> bool:
    _reload_secrets()
    if not TELEGRAM_WEBHOOK_SECRET:
        return False
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(sent, TELEGRAM_WEBHOOK_SECRET)


def apply_approval(*, approved: bool, source: str, note: str = "") -> dict:
    """Public wrapper for desktop UI / API callers."""
    return _apply_approval(approved=approved, source=source, note=note)


def _apply_approval(*, approved: bool, source: str, note: str = "") -> dict:
    pending = load_pending_approval()
    if pending is None:
        pending = {"reason": "unknown", "plan": "", "requested_at": None}

    pending["approved"] = approved
    pending["status"] = "approved" if approved else "rejected"
    pending["resolved_at"] = _utc_now()
    pending["source"] = source
    if note:
        pending["note"] = note

    save_pending_approval(pending)
    state = load_state()
    state["pending_approval"] = pending
    if approved:
        state["run_status"] = "idle"
    save_state(state)
    logger.info("Approval updated approved=%s source=%s", approved, source)
    return pending


def _send_telegram_ack(text: str) -> None:
    from notify import send_telegram

    send_telegram(text)


# ---------------------------------------------------------------------------
# SPA + static assets
# ---------------------------------------------------------------------------

WEB_DIR = BASE_DIR / "web"
ASSETS_DIR = BASE_DIR / "assets"


@app.get("/")
def spa_index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/web/<path:filename>")
def spa_static(filename: str):
    return send_from_directory(WEB_DIR, filename)


@app.get("/assets/<path:filename>")
def assets_static(filename: str):
    return send_from_directory(ASSETS_DIR, filename)


# Legacy Jinja pages (optional browser fallback)
@app.get("/legacy/")
def legacy_dashboard():
    snap = get_runner_snapshot()
    state = load_state()
    return render_template(
        "dashboard.html",
        snap=snap,
        docs=docs_checklist(create_dir=True),
        git_ok=is_git_repo(),
        project_dir=str(get_project_dir()),
        cost_history=state.get("cost_history") or [],
        active="dashboard",
    )


@app.get("/legacy/project")
def project_page():
    return render_template(
        "project.html",
        project_dir=str(get_project_dir()),
        docs=docs_checklist(create_dir=True),
        required=REQUIRED_DOCS,
        git_ok=is_git_repo(),
        active="project",
    )


@app.get("/legacy/approvals")
def approvals_page():
    pending = load_pending_approval() or load_state().get("pending_approval")
    return render_template("approvals.html", pending=pending, active="approvals")


@app.get("/legacy/logs")
def logs_page():
    state = load_state()
    log_dir = BASE_DIR / "logs"
    file_tail = ""
    logs = sorted(log_dir.glob("agent_*.log")) if log_dir.exists() else []
    if logs:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        file_tail = "\n".join(text.splitlines()[-200:])
    return render_template(
        "logs.html",
        history=state.get("log_history") or [],
        file_tail=file_tail,
        active="logs",
    )


@app.get("/legacy/settings")
def settings_page():
    env = read_env_settings()
    display = {}
    for key in EDITABLE_SETTINGS:
        val = env.get(key, os.getenv(key, ""))
        display[key] = mask_secret(val) if key in SECRET_SETTINGS and val else val
    return render_template(
        "settings.html",
        settings=display,
        startup_enabled=is_startup_enabled(),
        active="settings",
    )


@app.get("/legacy/chat")
def chat_page():
    state = load_state()
    return render_template(
        "chat.html",
        history=state.get("chat_history") or [],
        active="chat",
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "pending_file": str(PENDING_APPROVAL_PATH),
            "project_dir": str(get_project_dir()),
            "runner": get_runner_snapshot(),
        }
    )


@app.get("/api/status")
def api_status():
    snap = get_runner_snapshot()
    snap["docs"] = docs_checklist(create_dir=True)
    snap["git_ok"] = is_git_repo()
    snap["project_dir"] = str(get_project_dir())
    try:
        from live_events import get_live_snapshot

        live = get_live_snapshot()
        snap["live_phase"] = live.get("phase")
        snap["live_phase_detail"] = live.get("phase_detail")
        snap["run_cost_usd"] = live.get("run_cost_usd")
    except Exception:  # noqa: BLE001
        pass
    return jsonify(snap)


@app.get("/api/live-events")
def api_live_events():
    """Server-Sent Events stream of CrewAI / run activity."""
    from flask import Response, stream_with_context

    from live_events import ensure_listeners, sse_stream

    ensure_listeners()
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(
        stream_with_context(sse_stream()()),
        mimetype="text/event-stream",
        headers=headers,
    )


@app.post("/api/project")
def api_project():
    data = request.get_json(silent=True) or {}
    path = data.get("path") or request.form.get("path")
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        resolved = set_project_dir(path)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "project_dir": str(resolved),
            "git_ok": is_git_repo(resolved),
            "docs": docs_checklist(create_dir=True),
        }
    )


@app.get("/api/docs")
def api_docs_list():
    return jsonify({"ok": True, "docs": docs_checklist(create_dir=True), "required": list(REQUIRED_DOCS)})


@app.get("/api/docs/content")
def api_docs_content():
    name = (request.args.get("name") or "").strip()
    if name not in REQUIRED_DOCS:
        return jsonify({"ok": False, "error": "invalid name", "present": False}), 400
    path = get_docs_dir() / name
    if not path.is_file():
        return jsonify({"ok": True, "name": name, "present": False, "content": ""})
    content = path.read_text(encoding="utf-8", errors="replace")
    return jsonify(
        {
            "ok": True,
            "name": name,
            "present": path.stat().st_size > 0,
            "content": content,
            "size": path.stat().st_size,
        }
    )


@app.post("/api/docs")
def api_docs_upload():
    """Upload one of the required MD files (multipart) or JSON {name, content}."""
    docs_dir = get_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)

    if request.files:
        for key in request.files:
            f = request.files[key]
            name = Path(f.filename or key).name
            if name not in REQUIRED_DOCS:
                # allow renaming via form field
                name = request.form.get("name") or name
            if name not in REQUIRED_DOCS:
                return jsonify({"ok": False, "error": f"invalid doc name: {name}"}), 400
            dest = docs_dir / name
            f.save(dest)
        return jsonify({"ok": True, "docs": docs_checklist(create_dir=True)})

    data = request.get_json(silent=True) or {}
    name = data.get("name")
    content = data.get("content")
    if name not in REQUIRED_DOCS:
        return jsonify({"ok": False, "error": "invalid name"}), 400
    if content is None:
        return jsonify({"ok": False, "error": "content required"}), 400
    (docs_dir / name).write_text(str(content), encoding="utf-8")
    return jsonify({"ok": True, "docs": docs_checklist(create_dir=True)})


@app.post("/api/run/start")
def api_run_start():
    result = start_run(skip_human_input=True)
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@app.post("/api/run/stop")
def api_run_stop():
    return jsonify(stop_run())


@app.get("/api/logs")
def api_logs():
    state = load_state()
    lines = int(request.args.get("lines", "200"))
    log_dir = BASE_DIR / "logs"
    file_tail = ""
    logs = sorted(log_dir.glob("agent_*.log")) if log_dir.exists() else []
    if logs:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        file_tail = "\n".join(text.splitlines()[-lines:])
    return jsonify(
        {
            "ok": True,
            "history": (state.get("log_history") or [])[-lines:],
            "file_tail": file_tail,
        }
    )


@app.get("/api/timeline")
def api_timeline():
    """Toast-friendly agent timeline (log_history + optional file tail)."""
    state = load_state()
    lines = int(request.args.get("lines", "100"))
    items = (state.get("log_history") or [])[-lines:]
    log_dir = BASE_DIR / "logs"
    file_tail = ""
    logs = sorted(log_dir.glob("agent_*.log")) if log_dir.exists() else []
    if logs:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        file_tail = "\n".join(text.splitlines()[-min(lines, 200) :])
    return jsonify({"ok": True, "items": items, "file_tail": file_tail})


@app.get("/api/chat/history")
def api_chat_history():
    state = load_state()
    return jsonify({"ok": True, "history": state.get("chat_history") or []})


@app.get("/api/settings")
def api_settings_get():
    env = read_env_settings()
    out = {}
    for key in EDITABLE_SETTINGS:
        val = env.get(key, os.getenv(key, ""))
        out[key] = mask_secret(val) if key in SECRET_SETTINGS and val else val
    out["_startup_enabled"] = is_startup_enabled()
    return jsonify(out)


@app.post("/api/settings")
def api_settings_post():
    data = request.get_json(silent=True) or {}
    updates = {}
    for key in EDITABLE_SETTINGS:
        if key not in data:
            continue
        val = data[key]
        if val is None:
            continue
        val_s = str(val).strip()
        # skip unchanged masked secrets
        if key in SECRET_SETTINGS and ("…" in val_s or val_s == "********"):
            continue
        updates[key] = val_s
    if updates:
        write_env_settings(updates)
        if "PROJECT_DIR" in updates:
            set_project_dir(updates["PROJECT_DIR"])
        _reload_secrets()
    return jsonify({"ok": True, "updated": list(updates.keys())})


@app.post("/api/startup")
def api_startup():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    result = set_startup(enabled)
    code = 200 if result.get("ok") else 500
    return jsonify(result), code


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("message") or data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "message required"}), 400
    from telegram_chat import answer_project_question

    source = str(data.get("source") or "webview")
    if source not in ("desktop", "webview", "telegram"):
        source = "webview"
    reply = answer_project_question(question, source=source)
    return jsonify({"ok": True, "reply": reply})


@app.post("/api/approve")
def api_approve_local():
    """Desktop UI approve — no secret required on localhost loopback."""
    data = request.get_json(silent=True) or {}
    if "approved" not in data:
        return jsonify({"ok": False, "error": "approved required"}), 400
    # Allow without secret when remote_addr is loopback; else require secret
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1") and not _check_manual_secret():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    pending = _apply_approval(
        approved=bool(data["approved"]),
        source=str(data.get("source") or "desktop"),
        note=str(data.get("note") or ""),
    )
    return jsonify({"ok": True, "pending_approval": pending})


# Form POSTs from Jinja pages
@app.post("/actions/project")
def action_project():
    path = request.form.get("path", "").strip()
    if path:
        set_project_dir(path)
    return redirect(url_for("project_page"))


@app.post("/actions/docs")
def action_docs():
    name = request.form.get("name", "")
    content = request.form.get("content", "")
    if name in REQUIRED_DOCS:
        docs_dir = get_docs_dir()
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / name).write_text(content, encoding="utf-8")
    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        fname = Path(uploaded.filename).name
        target_name = request.form.get("upload_name") or fname
        if target_name in REQUIRED_DOCS:
            docs_dir = get_docs_dir()
            docs_dir.mkdir(parents=True, exist_ok=True)
            uploaded.save(docs_dir / target_name)
    return redirect(url_for("project_page"))


@app.post("/actions/run/start")
def action_run_start():
    start_run(skip_human_input=True)
    return redirect(url_for("legacy_dashboard"))


@app.post("/actions/run/stop")
def action_run_stop():
    stop_run()
    return redirect(url_for("legacy_dashboard"))


@app.post("/actions/approve")
def action_approve():
    approved = request.form.get("approved", "true").lower() in ("1", "true", "yes")
    _apply_approval(approved=approved, source="desktop_form")
    return redirect(url_for("approvals_page"))


@app.post("/actions/settings")
def action_settings():
    updates = {}
    for key in EDITABLE_SETTINGS:
        if key in request.form:
            val = request.form.get(key, "").strip()
            if key in SECRET_SETTINGS and ("…" in val or val == "********"):
                continue
            if val != "" or key in request.form:
                updates[key] = val
    if updates:
        write_env_settings(updates)
        if "PROJECT_DIR" in updates and updates["PROJECT_DIR"]:
            set_project_dir(updates["PROJECT_DIR"])
        _reload_secrets()
    set_startup("startup_enabled" in request.form)
    return redirect(url_for("settings_page"))


@app.post("/actions/chat")
def action_chat():
    q = request.form.get("message", "").strip()
    if q:
        from telegram_chat import answer_project_question

        answer_project_question(q, source="desktop")
    return redirect(url_for("chat_page"))


# ---------------------------------------------------------------------------
# Telegram + manual approve webhook
# ---------------------------------------------------------------------------


@app.route("/telegram", methods=["POST"])
@app.route("/approve", methods=["POST"])
def approve():
    approved: bool | None = None
    note = ""
    source = "api"

    payload = request.get_json(silent=True) or {}
    is_telegram_update = "message" in payload or "edited_message" in payload

    if is_telegram_update:
        if not _check_telegram_secret():
            logger.warning("Rejected Telegram webhook — bad/missing secret token")
            return jsonify({"ok": False, "error": "invalid_telegram_secret"}), 403

        message = payload.get("message") or payload.get("edited_message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()

        _reload_secrets()
        from config import AUTHORIZED_TELEGRAM_CHAT_ID
        from telegram_chat import is_authorized_telegram_chat

        if not is_authorized_telegram_chat(chat_id):
            logger.warning(
                "Ignored Telegram message from unauthorized chat_id=%s (authorized=%s)",
                chat_id,
                AUTHORIZED_TELEGRAM_CHAT_ID,
            )
            return jsonify({"ok": True, "ignored": "unauthorized_chat_id"})

        routed = handle_telegram_text(text)
        if routed["kind"] == "approve":
            approved = bool(routed["approved"])
            source = "telegram"
            note = f"chat_id={chat_id}"
            pending = _apply_approval(approved=approved, source=source, note=note)
            status = "approved" if approved else "rejected"
            _send_telegram_ack(f"Request {status}. Thanks.")
            return jsonify({"ok": True, "pending_approval": pending})

        # Async chat: ack webhook fast, think on background thread, reply on Telegram
        if routed.get("async") and routed["kind"] == "chat":
            question = routed.get("question") or text
            _send_telegram_ack("Thinking…")

            def _bg_reply(q: str = question) -> None:
                try:
                    from telegram_chat import answer_project_question

                    answer = answer_project_question(q, source="telegram")
                    _send_telegram_ack(answer)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Telegram chat reply failed")
                    _send_telegram_ack(f"Sorry, chat failed: {exc}")

            import threading

            threading.Thread(target=_bg_reply, name="tg-chat", daemon=True).start()
            return jsonify({"ok": True, "kind": "chat", "async": True})

        reply = routed.get("reply") or ""
        if reply:
            _send_telegram_ack(reply)
        return jsonify({"ok": True, "kind": routed["kind"]})

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
        return jsonify({"ok": False, "error": "Provide approved=true/false"}), 400

    pending = _apply_approval(approved=approved, source=source, note=note)
    return jsonify({"ok": True, "pending_approval": pending})


def create_app() -> Flask:
    return app


if __name__ == "__main__":
    host = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
