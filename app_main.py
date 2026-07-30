"""Windows desktop entry — Flask (Telegram/API) + CustomTkinter UI."""

from __future__ import annotations

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


def _host_port() -> tuple[str, int]:
    host = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    port = int(os.getenv("WEBHOOK_PORT", "5000"))
    return host, port


def start_flask() -> None:
    from webhook import app

    host, port = _host_port()
    logger.info("Starting Flask on %s:%s (Telegram + API)", host, port)
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


def main() -> None:
    thread = threading.Thread(target=start_flask, name="flask", daemon=True)
    thread.start()

    if not wait_for_server():
        logger.warning("Flask health check timed out — UI will still open")

    # Telegram → Agent → Telegram (long polling; no ngrok required)
    try:
        from telegram_poller import start_telegram_polling

        start_telegram_polling()
        logger.info("Telegram poller on — message your bot; replies go to Telegram")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start Telegram poller")

    logger.info("Launching CustomTkinter desktop UI")
    from ui_app import run_ui

    run_ui()


if __name__ == "__main__":
    main()
