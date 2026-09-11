"""Code execution tool — runs INSIDE the target project dir, with basic guardrails."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel, Field

from config import get_project_dir, validate_project_path
from path_guard import assert_project_dir_consistent, guard_write_target

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = int(os.getenv("CODE_TOOL_MAX_OUTPUT_CHARS", "20000"))

# Only allow commands starting with these — block arbitrary shell (rm -rf, curl, etc).
ALLOWED_COMMAND_PREFIXES = (
    "python", "python3", "pytest", "npm", "npx", "pnpm", "yarn",
    "git", "node", "pip", "pip3", "ls", "cat",
)


class CodeExecutionInput(BaseModel):
    command: str = Field(
        ...,
        description=(
            "Shell command to run inside the project directory, e.g. "
            "'pytest -q', 'git add -A && git commit -m \"msg\"', 'npm test'. "
            "Only python/pytest/npm/git/node/pip/ls/cat style commands allowed."
        ),
    )
    timeout_seconds: int = Field(120, description="Max execution time in seconds")


def _base_tool_cls() -> Type[Any]:
    try:
        from crewai.tools import BaseTool
        return BaseTool
    except ImportError:
        try:
            from langchain.tools import BaseTool  # type: ignore
            return BaseTool
        except ImportError:
            class _FallbackTool:
                name: str = "tool"
                description: str = ""

                def run(self, *args: Any, **kwargs: Any) -> str:
                    return self._run(*args, **kwargs)

                def _run(self, *args: Any, **kwargs: Any) -> str:
                    raise NotImplementedError

            return _FallbackTool  # type: ignore[return-value]


BaseTool = _base_tool_cls()


def _is_allowed(command: str) -> bool:
    segments = [s.strip() for s in command.replace("&&", ";").split(";") if s.strip()]
    if not segments:
        return False
    for seg in segments:
        head = seg.split()[0] if seg.split() else ""
        if head not in ALLOWED_COMMAND_PREFIXES:
            return False
    return True


def _git_block_reason(command: str) -> str | None:
    """Extra safety net — never allow main-mutating / force git ops."""
    try:
        from git_safe import is_dangerous_git_command

        return is_dangerous_git_command(command)
    except Exception:  # noqa: BLE001
        low = command.lower()
        if "git" in low.split()[0:1] or " git " in f" {low} ":
            if any(x in low for x in ("--force", " -f ", "push origin main", "push origin master")):
                return "dangerous git pattern"
            if "checkout main" in low and "merge" in low:
                return "checkout main + merge blocked"
        return None


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text)} chars total]"


def _stream_command_events(command: str, output: str, exit_code: int) -> None:
    try:
        from live_events import push_event

        low = command.lower()
        is_test = any(k in low for k in ("pytest", "npm test", "jest", "vitest"))
        is_git = low.strip().startswith("git") or "&& git" in low
        if is_test:
            for line in (output or "").splitlines()[-40:]:
                push_event(
                    {
                        "type": "test_line",
                        "message": line,
                        "meta": {"exit_code": exit_code},
                    }
                )
        if is_git and ("diff" in low or "commit" in low or "merge" in low):
            # Surface a short diff snippet when available
            snippet = "\n".join((output or "").splitlines()[:80])
            if snippet.strip():
                push_event(
                    {
                        "type": "code_diff",
                        "message": f"$ {command}",
                        "meta": {"diff": snippet[:4000], "exit_code": exit_code},
                    }
                )
    except Exception:  # noqa: BLE001
        pass


class LocalCodeExecutionTool(BaseTool):  # type: ignore[misc]
    # Primary name matches what LLMs commonly invent ("run_code_execution").
    # An alias tool named "code_execution" is also registered — see build_code_execution_tools.
    name: str = "run_code_execution"
    description: str = (
        "Run a shell command (git, pytest, npm, node, pip, python, ls, dir) inside the "
        "target project directory. Alias name: code_execution. "
        "Use this to run tests and create git commits. "
        "Commands are restricted to a safe allowlist and jailed to the project dir. "
        "Never merge/push to main or force-push. "
        "Args: command (string), timeout_seconds (optional int)."
    )
    args_schema: Type[BaseModel] = CodeExecutionInput

    def _run(self, command: str, timeout_seconds: int = 120) -> str:
        try:
            project_dir = assert_project_dir_consistent()
            project_dir = Path(validate_project_path(project_dir))
            guard_write_target(project_dir, project_dir=project_dir)
        except Exception as exc:  # noqa: BLE001
            logger.error("Sandbox blocked code_execution: %s", exc)
            return f"ERROR: sandbox — {exc}"

        if not project_dir.exists():
            return f"ERROR: PROJECT_DIR does not exist: {project_dir}"

        if not _is_allowed(command):
            logger.warning("Blocked disallowed command: %s", command)
            return (
                f"ERROR: command blocked by allowlist. Allowed prefixes: "
                f"{', '.join(ALLOWED_COMMAND_PREFIXES)}"
            )

        blocked = _git_block_reason(command)
        if blocked:
            logger.warning("Blocked dangerous git command (%s): %s", blocked, command)
            return f"ERROR: git blocked — {blocked}"

        logger.info("code_execution cwd=%s cmd=%s", project_dir, command[:120])
        env = os.environ.copy()
        scripts_dir = str(Path(sys.executable).parent)
        if scripts_dir not in env.get("PATH", ""):
            env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(project_dir),
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.error("Code execution timed out after %ss", timeout_seconds)
            return f"ERROR: timed out after {timeout_seconds}s"
        except OSError as exc:
            logger.error("Code execution failed: %s", exc)
            return f"ERROR: {exc}"

        out = _truncate(completed.stdout or "")
        err = _truncate(completed.stderr or "")
        combined = (
            f"cwd={project_dir}\n"
            f"exit_code={completed.returncode}\n"
            f"--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        )
        _stream_command_events(command, combined, completed.returncode)
        return combined


class CodeExecutionAliasTool(LocalCodeExecutionTool):  # type: ignore[misc]
    """Same implementation under the shorter name prompts historically used."""

    name: str = "code_execution"


def build_code_execution_tool():
    """Single primary tool (run_code_execution). Prefer build_code_execution_tools()."""
    return build_code_execution_tools()[0]


def build_code_execution_tools() -> list:
    """
    Prefer sandboxed CodeInterpreterTool (Docker); fall back to jailed local tools.

    Returns both run_code_execution and code_execution so model name drift doesn't
    produce UNKNOWN_TOOL failures.
    """
    try:
        from crewai_tools import CodeInterpreterTool

        # unsafe_mode=False -> runs in Docker sandbox, not direct on host.
        return [CodeInterpreterTool(unsafe_mode=False)]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "CodeInterpreterTool unavailable/unusable (%s) — using jailed LocalCodeExecutionTool",
            exc,
        )
        return [LocalCodeExecutionTool(), CodeExecutionAliasTool()]
