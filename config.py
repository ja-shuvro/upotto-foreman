"""Runtime configuration — PROJECT_DIR and docs paths (not frozen at import)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

REQUIRED_DOCS = (
    "Architecture.md",
    "Design.md",
    "Memory.md",
    "Phases.md",
    "PRD.md",
    "Rules.md",
)

_DEFAULT_PROJECT = BASE_DIR / "target_project"


def get_project_dir() -> Path:
    """Resolve current project directory from env (updated when UI sets path)."""
    raw = os.getenv("PROJECT_DIR", str(_DEFAULT_PROJECT)).strip() or str(_DEFAULT_PROJECT)
    return Path(raw).expanduser().resolve()


def set_project_dir(path: str | Path) -> Path:
    """Set PROJECT_DIR in process env and persist to .env + state."""
    resolved = Path(path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / "docs").mkdir(parents=True, exist_ok=True)
    os.environ["PROJECT_DIR"] = str(resolved)
    _upsert_env_key("PROJECT_DIR", str(resolved))

    try:
        from state import load_state, save_state

        state = load_state()
        state["project_dir"] = str(resolved)
        save_state(state)
    except Exception:
        pass

    return resolved


def get_docs_dir() -> Path:
    return get_project_dir() / "docs"


def docs_checklist() -> list[dict]:
    docs = get_docs_dir()
    docs.mkdir(parents=True, exist_ok=True)
    items = []
    for name in REQUIRED_DOCS:
        path = docs / name
        items.append(
            {
                "name": name,
                "path": str(path),
                "present": path.is_file() and path.stat().st_size > 0,
                "size": path.stat().st_size if path.is_file() else 0,
            }
        )
    return items


def all_docs_present() -> bool:
    return all(item["present"] for item in docs_checklist())


def is_git_repo(path: Path | None = None) -> bool:
    root = path or get_project_dir()
    return (root / ".git").exists()


def read_doc_excerpts(max_chars_per_file: int = 2000) -> str:
    parts: list[str] = []
    for item in docs_checklist():
        if not item["present"]:
            parts.append(f"### {item['name']}\n(missing)\n")
            continue
        text = Path(item["path"]).read_text(encoding="utf-8", errors="replace")
        parts.append(f"### {item['name']}\n{text[:max_chars_per_file]}\n")
    return "\n".join(parts)


def _upsert_env_key(key: str, value: str) -> None:
    """Create or replace a KEY=value line in .env."""
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    found = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix) or line.strip().startswith(f"#{prefix}"):
            if not found:
                new_lines.append(f"{key}={value}")
                found = True
            # drop duplicate old keys
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def read_env_settings() -> dict[str, str]:
    """Read .env into a dict (for settings UI). Missing file → empty."""
    result: dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        result[k.strip()] = v.strip()
    return result


def write_env_settings(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        if key and value is not None:
            _upsert_env_key(key, str(value))
            os.environ[key] = str(value)


# Settings keys editable from the desktop UI
EDITABLE_SETTINGS = (
    "PROJECT_DIR",
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "AGENTROUTER_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_MAX_TOKENS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_WEBHOOK_SECRET",
    "APPROVAL_WEBHOOK_SECRET",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_TO",
    "SMTP_USE_TLS",
    "WEBHOOK_PUBLIC_URL",
    "WEBHOOK_HOST",
    "WEBHOOK_PORT",
    "SKIP_HUMAN_INPUT",
    "LOG_LEVEL",
)

SECRET_SETTINGS = {
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "AGENTROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "APPROVAL_WEBHOOK_SECRET",
    "SMTP_PASSWORD",
}


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return value[:4] + "…" + value[-4:]
