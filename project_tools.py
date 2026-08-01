"""File tools locked to the active PROJECT_DIR (fresh get_project_dir every call)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel, Field

from config import get_project_dir
from path_guard import assert_project_dir_consistent, guard_write_target

logger = logging.getLogger(__name__)


def _base_tool_cls() -> Type[Any]:
    from crewai.tools import BaseTool

    return BaseTool


BaseTool = _base_tool_cls()


class GuardedWriteInput(BaseModel):
    filename: str = Field(..., description="File name relative to the project (or subdirectory path).")
    content: str = Field(..., description="Text content to write.")
    directory: str | None = Field(
        "./",
        description="Subdirectory inside the active PROJECT_DIR only (default ./).",
    )
    overwrite: str | bool = Field(True, description="Overwrite existing file (default true).")


class ProjectFileWriterTool(BaseTool):  # type: ignore[misc]
    name: str = "File Writer Tool"
    description: str = (
        "Write a file INSIDE the active PROJECT_DIR only. "
        "Paths outside the current project are rejected. "
        "Pass filename + content; optional directory relative to project root."
    )
    args_schema: Type[BaseModel] = GuardedWriteInput

    def _run(
        self,
        filename: str,
        content: str,
        directory: str | None = "./",
        overwrite: str | bool = True,
    ) -> str:
        try:
            project = assert_project_dir_consistent()
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

        from crewai_tools import FileWriterTool

        tool = FileWriterTool(base_dir=str(project))
        # Pre-resolve intended path and guard
        directory = directory or "./"
        candidate = Path(project) / directory / filename
        try:
            guard_write_target(candidate, project_dir=project)
        except ValueError as exc:
            logger.error("%s", exc)
            return f"ERROR: {exc}"

        result = tool._run(
            filename=filename,
            content=content,
            directory=directory,
            overwrite=overwrite,
        )
        # Post-check: if tool returned a path, ensure still inside project
        try:
            # Best-effort: re-validate candidate
            guard_write_target(candidate, project_dir=project)
        except ValueError as exc:
            return f"ERROR: {exc}"
        logger.info("Wrote under PROJECT_DIR=%s → %s", project, candidate)
        return f"{result}\n[guard] PROJECT_DIR={project}"


class GuardedReadInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Path to read — relative to PROJECT_DIR or absolute inside PROJECT_DIR.",
    )


class ProjectFileReadTool(BaseTool):  # type: ignore[misc]
    name: str = "Read a file's content"
    description: str = (
        "Read a file from the active PROJECT_DIR only. Absolute paths outside the "
        "project are rejected."
    )
    args_schema: Type[BaseModel] = GuardedReadInput

    def _run(self, file_path: str) -> str:
        try:
            project = assert_project_dir_consistent()
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

        raw = (file_path or "").strip()
        if not raw:
            return "ERROR: file_path required"
        path = Path(raw)
        if not path.is_absolute():
            path = project / path
        try:
            guard_write_target(path, project_dir=project)  # same containment check
        except ValueError as exc:
            return f"ERROR: {exc}"

        from crewai_tools import FileReadTool

        # FileReadTool may accept file_path — prefer reading ourselves for certainty
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: {exc}"
        return text[:100_000]
