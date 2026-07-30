"""Logging helpers — timestamped agent action logs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone


def log_agent_action(agent: str, action: str, detail: str = "") -> None:
    logger = logging.getLogger("agent_actions")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = f"[{ts}] agent={agent} action={action}"
    if detail:
        msg = f"{msg} | {detail}"
    logger.info(msg)
