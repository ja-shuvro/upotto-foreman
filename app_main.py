"""Windows desktop entry — Flask (API/Telegram) + pywebview SPA."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app_main")

try:
    from path_guard import hydrate_project_dir_from_state

    hydrate_project_dir_from_state()
    logger.info("PROJECT_DIR=%s", os.getenv("PROJECT_DIR"))
except Exception:  # noqa: BLE001
    pass

# Unique AppUserModelID so Windows taskbar uses our .ico instead of python.exe
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "Upotto.Foreman.Desktop"
        )
    except Exception:  # noqa: BLE001
        pass


def _asset_icon() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets" / "upotto-foreman.ico"
    return BASE_DIR / "assets" / "upotto-foreman.ico"


def _host_port() -> tuple[str, int]:
    host = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    return host, port


def start_flask() -> None:
    from webhook import app

    host, port = _host_port()
    logger.info("Starting Flask on %s:%s (SPA + Telegram + API)", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def wait_for_server(timeout: float = 30.0) -> bool:
    import urllib.request

    _, port = _host_port()
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def run_webview() -> None:
    import webview

    _, port = _host_port()
    url = f"http://127.0.0.1:{port}/"
    icon = _asset_icon()
    icon_path = str(icon.resolve()) if icon.is_file() else None

    webview.create_window(
        title="Upotto Foreman",
        url=url,
        width=1280,
        height=840,
        min_size=(1024, 700),
        background_color="#0c1016",
    )
    # Windows: icon is applied in winforms via webview.start(..., icon=...)
    start_kwargs: dict = {"debug": os.getenv("WEBVIEW_DEBUG", "0") == "1"}
    if icon_path:
        start_kwargs["icon"] = icon_path
        logger.info("Window/taskbar icon: %s", icon_path)
    else:
        logger.warning("Icon missing: %s", icon)

    webview.start(**start_kwargs)


def main() -> None:
    ui_mode = (os.getenv("UI_MODE") or "webview").strip().lower()

    thread = threading.Thread(target=start_flask, name="flask", daemon=True)
    thread.start()

    if not wait_for_server():
        logger.warning("Flask health check timed out — UI will still open")

    try:
        from telegram_poller import start_telegram_polling

        start_telegram_polling()
        logger.info("Telegram poller on — message your bot; replies go to Telegram")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start Telegram poller")

    if ui_mode in ("ctk", "customtkinter", "tk"):
        logger.info("Launching CustomTkinter desktop UI (UI_MODE=%s)", ui_mode)
        from ui_app import run_ui

        run_ui()
        return

    logger.info("Launching pywebview SPA at http://127.0.0.1:%s/", _host_port()[1])
    try:
        run_webview()
    except Exception:  # noqa: BLE001
        logger.exception("pywebview failed — falling back to CustomTkinter")
        from ui_app import run_ui

        run_ui()


if __name__ == "__main__":
    main()
