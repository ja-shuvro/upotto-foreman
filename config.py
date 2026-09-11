"""Runtime configuration — PROJECT_DIR sandbox and docs paths."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

# Hard sandbox — agents may only work under this tree
ALLOWED_BASE_DIR = os.path.normpath(os.path.abspath(r"C:\langs\projects"))

# Only this Telegram chat may issue control commands
AUTHORIZED_TELEGRAM_CHAT_ID = "7877332152"

REQUIRED_DOCS = (
    "Architecture.md",
    "Design.md",
    "Memory.md",
    "Phases.md",
    "PRD.md",
    "Rules.md",
)

# Shared ignore set for emptiness checks and project scans
IGNORED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv"})

_DEFAULT_PROJECT = BASE_DIR / "target_project"


def validate_project_path(candidate: str | Path) -> str:
    """Resolve and ensure path stays inside ALLOWED_BASE_DIR (no .. / symlink escape)."""
    raw = str(candidate).strip()
    if not raw:
        raise ValueError("Project path is empty")

    resolved = os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
    base = ALLOWED_BASE_DIR

    try:
        if os.path.commonpath([resolved, base]) != base:
            raise ValueError(f"Path {resolved} outside allowed base {base}")
    except ValueError as exc:
        if "outside allowed base" in str(exc):
            raise
        raise ValueError(f"Path {resolved} outside allowed base {base}") from exc

    real = os.path.normpath(os.path.realpath(resolved))
    try:
        if os.path.commonpath([real, base]) != base:
            raise ValueError(f"Path realpath {real} outside allowed base {base}")
    except ValueError as exc:
        if "outside allowed base" in str(exc):
            raise
        raise ValueError(f"Path realpath {real} outside allowed base {base}") from exc

    probe = Path(resolved)
    for node in [probe, *probe.parents]:
        try:
            if node.exists() and node.is_symlink():
                raise ValueError("Symlink escape not allowed")
        except OSError:
            break
        if str(node) == base or node == Path(base):
            break

    if os.path.islink(resolved) or real != resolved:
        if real.casefold() != resolved.casefold():
            raise ValueError("Symlink escape not allowed")

    return resolved


def get_project_dir() -> Path:
    """Resolve PROJECT_DIR from process env every call — never a module-level cache.

    Raises EnvironmentError if unset. Always re-validates the sandbox.
    """
    raw = (os.getenv("PROJECT_DIR") or "").strip()
    if not raw:
        raise EnvironmentError(
            "PROJECT_DIR not set — set it in .env or via /setproject / Project tab"
        )
    return Path(validate_project_path(raw))


def set_project_dir(path: str | Path) -> Path:
    """Set PROJECT_DIR in process env and persist to .env + state (sandbox-gated)."""
    resolved = Path(validate_project_path(path))
    resolved.mkdir(parents=True, exist_ok=True)
    resolved = Path(validate_project_path(resolved))
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

    logger.info("Active PROJECT_DIR set to %s", resolved)
    return resolved


def resolve_project_under_base(folder_name: str) -> str:
    """Map a folder name / relative path under ALLOWED_BASE_DIR to an absolute path."""
    name = (folder_name or "").strip().strip("/\\")
    if not name:
        raise ValueError("Folder name required")
    parts = Path(name).parts
    if any(p == ".." for p in parts):
        raise ValueError("Path traversal (..) not allowed")
    if os.path.isabs(name):
        return validate_project_path(name)
    candidate = os.path.join(ALLOWED_BASE_DIR, name)
    return validate_project_path(candidate)


def list_allowed_projects() -> list[str]:
    """Directories under ALLOWED_BASE_DIR (top-level + one nested level)."""
    root = Path(ALLOWED_BASE_DIR)
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in IGNORED_DIRS or child.name.startswith("."):
            continue
        names.append(child.name)
        try:
            for nested in sorted(child.iterdir(), key=lambda p: p.name.lower()):
                if not nested.is_dir():
                    continue
                if nested.name in IGNORED_DIRS or nested.name.startswith("."):
                    continue
                names.append(f"{child.name}/{nested.name}")
        except OSError:
            continue
    return names


def get_docs_dir() -> Path:
    return get_project_dir() / "docs"


def docs_checklist(*, create_dir: bool = False, project_dir: Path | None = None) -> list[dict]:
    """Return presence/size for the 6 required docs.

    create_dir=False by default so bootstrap emptiness checks never mkdir docs/.
    """
    if project_dir is not None:
        root = Path(validate_project_path(project_dir))
    else:
        root = get_project_dir()
    docs = root / "docs"
    if create_dir:
        docs.mkdir(parents=True, exist_ok=True)
    items = []
    for name in REQUIRED_DOCS:
        path = docs / name
        present = path.is_file() and path.stat().st_size > 0
        items.append(
            {
                "name": name,
                "path": str(path),
                "present": present,
                "size": path.stat().st_size if path.is_file() else 0,
            }
        )
    return items


def all_docs_present(project_dir: Path | None = None) -> bool:
    return all(
        item["present"] for item in docs_checklist(create_dir=False, project_dir=project_dir)
    )


def missing_docs(project_dir: Path | None = None) -> list[str]:
    return [
        item["name"]
        for item in docs_checklist(create_dir=False, project_dir=project_dir)
        if not item["present"]
    ]


def is_git_repo(path: Path | None = None) -> bool:
    root = path or get_project_dir()
    if path is not None:
        root = Path(validate_project_path(path))
    return (root / ".git").exists()


def load_project_brief(
    *,
    max_chars_per_file: int | None = None,
    project_dir: Path | None = None,
) -> str:
    """Load all present docs into a single 'Project Brief' block (shared by bootstrap + tasks)."""
    parts: list[str] = ["=== PROJECT BRIEF (docs/) ==="]
    for item in docs_checklist(create_dir=False, project_dir=project_dir):
        if not item["present"]:
            parts.append(f"\n### {item['name']}\n(missing)\n")
            continue
        text = Path(item["path"]).read_text(encoding="utf-8", errors="replace")
        if max_chars_per_file is not None:
            text = text[:max_chars_per_file]
        parts.append(f"\n### {item['name']}\n{text}\n")
    parts.append("=== END PROJECT BRIEF ===")
    return "\n".join(parts)


def read_doc_excerpts(max_chars_per_file: int = 2000) -> str:
    return load_project_brief(max_chars_per_file=max_chars_per_file)


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
            if key == "PROJECT_DIR":
                set_project_dir(value)
                continue
            _upsert_env_key(key, str(value))
            os.environ[key] = str(value)


EDITABLE_SETTINGS = (
    "PROJECT_DIR",
    "LLM_PROVIDER",
    "LLM_FAILOVER_ENABLED",
    "LLM_PROVIDER_PRIORITY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "AGENTROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
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
    "GEMINI_API_KEY",
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
