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
    first_word = command.strip().split()[0] if command.strip() else ""
    # allow chained commands like "git add -A && git commit ..." — check each segment
    segments = [s.strip() for s in command.replace("&&", ";").split(";") if s.strip()]
    if not segments:
        return False
    for seg in segments:
        head = seg.split()[0] if seg.split() else ""
        if head not in ALLOWED_COMMAND_PREFIXES:
            return False
    return True


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text)} chars total]"


class LocalCodeExecutionTool(BaseTool):  # type: ignore[misc]
    name: str = "code_execution"
    description: str = (
        "Run a shell command (git, pytest, npm, node, pip, python) inside the "
        "target project directory. Use this to run tests and create git commits. "
        "Commands are restricted to a safe allowlist and jailed to the project dir."
    )
    args_schema: Type[BaseModel] = CodeExecutionInput

    def _run(self, command: str, timeout_seconds: int = 120) -> str:
        try:
            project_dir = Path(validate_project_path(get_project_dir()))
        except ValueError as exc:
            logger.error("Sandbox blocked code_execution cwd: %s", exc)
            return f"ERROR: sandbox — {exc}"

        if not project_dir.exists():
            return f"ERROR: PROJECT_DIR does not exist: {project_dir}"

        if not _is_allowed(command):
            logger.warning("Blocked disallowed command: %s", command)
            return (
                f"ERROR: command blocked by allowlist. Allowed prefixes: "
                f"{', '.join(ALLOWED_COMMAND_PREFIXES)}"
            )

        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(project_dir),
            )
        except subprocess.TimeoutExpired:
            logger.error("Code execution timed out after %ss", timeout_seconds)
            return f"ERROR: timed out after {timeout_seconds}s"
        except OSError as exc:
            logger.error("Code execution failed: %s", exc)
            return f"ERROR: {exc}"

        out = _truncate(completed.stdout or "")
        err = _truncate(completed.stderr or "")
        return (
            f"cwd={project_dir}\n"
            f"exit_code={completed.returncode}\n"
            f"--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        )


def build_code_execution_tool():
    """Prefer sandboxed CodeInterpreterTool (Docker); fall back to jailed local tool."""
    try:
        from crewai_tools import CodeInterpreterTool

        # unsafe_mode=False -> runs in Docker sandbox, not direct on host.
        return CodeInterpreterTool(unsafe_mode=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "CodeInterpreterTool unavailable/unusable (%s) — using jailed LocalCodeExecutionTool",
            exc,
        )
        return LocalCodeExecutionTool()
