"""Strict project-path helpers — active PROJECT_DIR only, re-read every call."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def hydrate_project_dir_from_state() -> None:
    """On boot: if state has project_dir, push it into process env (Telegram switches)."""
    try:
        from config import validate_project_path
        from state import load_state

        raw = (load_state().get("project_dir") or "").strip()
        if raw:
            os.environ["PROJECT_DIR"] = validate_project_path(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("hydrate_project_dir_from_state skipped: %s", exc)


def assert_project_dir_consistent() -> Path:
    """Abort if active project cannot be resolved or env is missing/mismatched."""
    from config import get_project_dir, validate_project_path

    env_raw = (os.getenv("PROJECT_DIR") or "").strip()
    if not env_raw:
        raise EnvironmentError("PROJECT_DIR not set in environment")

    env_path = Path(validate_project_path(env_raw))
    active = get_project_dir()
    if active.resolve() != env_path.resolve():
        raise RuntimeError(
            f"PROJECT_DIR mismatch: active={active} vs env={env_path}"
        )
    return active


def guard_write_target(target_path: str | Path, *, project_dir: Path | None = None) -> str:
    """Block any write whose resolved path is outside the ACTIVE project dir."""
    from config import get_project_dir, validate_project_path

    proj = Path(validate_project_path(project_dir or get_project_dir())).resolve()
    resolved = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(target_path)))))
    try:
        resolved = resolved.resolve()
    except OSError:
        pass

    try:
        if os.path.commonpath([str(resolved), str(proj)]) != str(proj):
            raise ValueError(
                f"Write BLOCKED: {resolved} is outside active project {proj}"
            )
    except ValueError as exc:
        if "Write BLOCKED" in str(exc):
            raise
        raise ValueError(
            f"Write BLOCKED: {resolved} is outside active project {proj}"
        ) from exc

    # Extra: never allow writing into the Foreman tool itself when targeting another project
    try:
        from config import BASE_DIR

        foreman = Path(BASE_DIR).resolve()
        if proj.resolve() != foreman and os.path.commonpath(
            [str(resolved), str(foreman)]
        ) == str(foreman):
            raise ValueError(
                f"Write BLOCKED: refused to write into Foreman home {foreman} "
                f"while active project is {proj}"
            )
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        pass

    return str(resolved)


def notify_path_abort(exc: BaseException) -> None:
    try:
        from notify import send_telegram

        send_telegram(
            f"ERROR: PROJECT_DIR guard aborted run.\n{exc}\n"
            f"env PROJECT_DIR={os.getenv('PROJECT_DIR')}",
            parse_mode=None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to notify path abort")
