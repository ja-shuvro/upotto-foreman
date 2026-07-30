"""Windows Startup shortcut helpers for Upotto Foreman."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SHORTCUT_NAME = "UpottoForeman.lnk"


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA not set — not a Windows user profile?")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_folder() / SHORTCUT_NAME


def is_startup_enabled() -> bool:
    try:
        return shortcut_path().exists()
    except Exception:  # noqa: BLE001
        return False


def _target_command() -> tuple[str, str]:
    """Return (target_path, arguments) for the Startup shortcut."""
    base = Path(__file__).resolve().parent
    app_main = base / "app_main.py"
    # Prefer frozen exe when packaged
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = pythonw if pythonw.exists() else Path(sys.executable)
    return str(python), f'"{app_main}"'


def enable_startup() -> dict:
    if sys.platform != "win32":
        return {"ok": False, "error": "startup_only_supported_on_windows"}

    folder = startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target, args = _target_command()
    path = shortcut_path()
    work_dir = str(Path(__file__).resolve().parent)

    try:
        # Prefer win32com when available
        try:
            import win32com.client  # type: ignore

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(path))
            shortcut.Targetpath = target
            shortcut.Arguments = args
            shortcut.WorkingDirectory = work_dir
            shortcut.IconLocation = target
            shortcut.save()
        except ImportError:
            # PowerShell COM fallback — no pywin32 required
            ps = (
                f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{path}");'
                f'$s.TargetPath = "{target}";'
                f'$s.Arguments = \'{args}\';'
                f'$s.WorkingDirectory = "{work_dir}";'
                f"$s.Save()"
            )
            import subprocess

            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                return {
                    "ok": False,
                    "error": completed.stderr or completed.stdout or "powershell_failed",
                }
        logger.info("Startup shortcut created at %s", path)
        return {"ok": True, "path": str(path), "enabled": True}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enable startup")
        return {"ok": False, "error": str(exc)}


def disable_startup() -> dict:
    path = shortcut_path()
    try:
        if path.exists():
            path.unlink()
        return {"ok": True, "enabled": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def set_startup(enabled: bool) -> dict:
    return enable_startup() if enabled else disable_startup()
