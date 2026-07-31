"""Docs-first bootstrap — EMPTY / DOCS_PRESENT / DOCS_MISSING before daily crew."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from config import (
    IGNORED_DIRS,
    REQUIRED_DOCS,
    all_docs_present,
    get_docs_dir,
    get_project_dir,
    is_git_repo,
    load_project_brief,
    missing_docs,
)

logger = logging.getLogger(__name__)

# Cap how many source files we fully read for analysis (token budget)
MAX_FULL_READ_FILES = int(os.getenv("BOOTSTRAP_MAX_READ_FILES", "40"))
MAX_CHARS_PER_SOURCE = int(os.getenv("BOOTSTRAP_MAX_CHARS_PER_FILE", "6000"))
RELEVANT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".rb",
    ".php",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".json",
}


class BootstrapMode(str, Enum):
    EMPTY = "EMPTY"
    DOCS_PRESENT = "DOCS_PRESENT"
    DOCS_MISSING = "DOCS_MISSING"
    INVALID = "INVALID"  # path missing / not a directory


@dataclass
class BootstrapResult:
    mode: BootstrapMode
    message: str
    report: str = ""
    docs_written: list[str] = field(default_factory=list)
    project_dir: str = ""
    stop_crew: bool = True  # True = do not run normal daily loop after


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_project_empty(project_dir: Path) -> bool:
    """True if no real project files exist (ignoring .git / venv / __pycache__)."""
    if not project_dir.is_dir():
        return True
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        # also skip ignored dir names if somehow listed as files
        real = [f for f in files if f not in {".DS_Store"}]
        if real:
            return False
    return True


def list_project_files(project_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in files:
            paths.append(Path(root) / name)
    paths.sort(key=lambda p: str(p).lower())
    return paths


def resolve_bootstrap_mode(project_dir: Path | None = None) -> BootstrapMode:
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    if not root.exists() or not root.is_dir():
        return BootstrapMode.INVALID
    if is_project_empty(root):
        return BootstrapMode.EMPTY
    if all_docs_present(root):
        return BootstrapMode.DOCS_PRESENT
    return BootstrapMode.DOCS_MISSING


def _run_git(command: str, *, project_dir: Path | None = None) -> str:
    """Run an allowlisted git/ls command via LocalCodeExecutionTool."""
    from code_tool import LocalCodeExecutionTool

    root = Path(project_dir) if project_dir is not None else get_project_dir()
    prev = os.environ.get("PROJECT_DIR")
    os.environ["PROJECT_DIR"] = str(root)
    try:
        tool = LocalCodeExecutionTool()
        return tool._run(command=command, timeout_seconds=60)
    finally:
        if prev is None:
            os.environ.pop("PROJECT_DIR", None)
        else:
            os.environ["PROJECT_DIR"] = prev


def collect_git_snapshot(project_dir: Path | None = None) -> str:
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    parts: list[str] = []
    if not is_git_repo(root):
        return "Git: not a repository (no .git)."
    for cmd in (
        "git status --porcelain",
        "git log --stat -10",
        "git diff HEAD",
        "git rev-parse --short HEAD",
    ):
        parts.append(f"$ {cmd}\n{_run_git(cmd, project_dir=root)}\n")
    return "\n".join(parts)


def collect_codebase_snapshot(project_dir: Path) -> str:
    files = list_project_files(project_dir)
    lines = [f"Project: {project_dir}", f"File count: {len(files)}", "", "Structure:"]
    for p in files[:500]:
        try:
            rel = p.relative_to(project_dir)
        except ValueError:
            rel = p
        lines.append(f"- {rel}")

    # Prefer source/config files for full reads
    candidates = [
        p
        for p in files
        if p.suffix.lower() in RELEVANT_SUFFIXES and "docs" not in p.parts[:1]
    ]
    # Prefer shorter / root-near files
    candidates.sort(key=lambda p: (len(p.parts), p.stat().st_size if p.exists() else 0))
    lines.append("\n--- Selected file contents ---")
    for p in candidates[:MAX_FULL_READ_FILES]:
        try:
            rel = p.relative_to(project_dir)
            text = p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS_PER_SOURCE]
            lines.append(f"\n#### {rel}\n```\n{text}\n```\n")
        except OSError as exc:
            lines.append(f"\n#### {p}: read error {exc}\n")

    lines.append("\n--- Git snapshot ---\n")
    lines.append(collect_git_snapshot(project_dir))
    return "\n".join(lines)


def _llm_call(prompt: str) -> str:
    from agents import build_crew_llm

    llm = build_crew_llm(temperature=0.2)
    if hasattr(llm, "call"):
        reply = llm.call([{"role": "user", "content": prompt}])
    elif hasattr(llm, "invoke"):
        reply = llm.invoke(prompt)
    else:
        reply = str(llm)
    return str(getattr(reply, "content", None) or reply).strip()


def run_full_audit(project_dir: Path | None = None) -> BootstrapResult:
    """DOCS_PRESENT: cross-check docs vs codebase; return audit report; stop crew."""
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    brief = load_project_brief(project_dir=root)
    codebase = collect_codebase_snapshot(root)
    prompt = (
        "You are a Senior Software Architect performing a FULL PROJECT AUDIT.\n"
        "Cross-reference the Project Brief (6 docs) against the actual codebase + git state.\n\n"
        "Produce a Markdown AUDIT REPORT with sections:\n"
        "1. **Current state** — what phase/stage the project is actually at\n"
        "2. **Implemented vs docs** — what matches PRD/Architecture/Phases\n"
        "3. **Gaps / drift** — inconsistencies between docs and code\n"
        "4. **Suggested next steps** — concrete improvements\n\n"
        "Be factual. Do not invent features not in code or docs.\n\n"
        f"{brief}\n\n"
        f"=== CODEBASE + GIT ===\n{codebase}\n"
    )
    report = _llm_call(prompt)
    msg = (
        f"Docs present under {root / 'docs'}. Audit complete — "
        "review the report before running the normal daily loop."
    )
    _record_bootstrap_state(BootstrapMode.DOCS_PRESENT, root, report)
    return BootstrapResult(
        mode=BootstrapMode.DOCS_PRESENT,
        message=msg,
        report=report,
        project_dir=str(root),
        stop_crew=True,
    )


def run_full_analysis_and_generate_docs(project_dir: Path | None = None) -> BootstrapResult:
    """DOCS_MISSING: analyze codebase, generate the 6 docs, return report; stop crew."""
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    missing = missing_docs(root)
    codebase = collect_codebase_snapshot(root)
    near_empty = len(list_project_files(root)) <= 3  # e.g. only README

    prompt = (
        "You are a Senior Software Architect. Analyze the codebase snapshot and "
        "GENERATE content for these documentation files (GitHub-flavored Markdown).\n\n"
        "Return ONE response with exactly 6 fenced blocks, each tagged with the filename:\n"
        "```Architecture.md\n...\n```\n"
        "```Design.md\n...\n```\n"
        "```Memory.md\n...\n```\n"
        "```Phases.md\n...\n```\n"
        "```PRD.md\n...\n```\n"
        "```Rules.md\n...\n```\n\n"
        "Content rules:\n"
        "- Architecture.md — real structure/modules/data flow found (or note fresh start)\n"
        "- Design.md — patterns/decisions observed\n"
        "- Memory.md — start a running project memory log with today's date\n"
        "- Phases.md — inferred current phase + proposed upcoming phases\n"
        "- PRD.md — reconstruct requirements from code; clearly mark "
        "'reconstructed from code, please review'\n"
        "- Rules.md — conventions from code/config; include security/allowlist spirit "
        "if visible\n"
        f"{'NOTE: near-empty project — do NOT invent elaborate architecture.' if near_empty else ''}\n\n"
        f"Missing files currently: {', '.join(missing) or 'all'}\n\n"
        f"=== CODEBASE + GIT ===\n{codebase}\n"
    )
    raw = _llm_call(prompt)
    written = _write_docs_from_llm_output(docs_dir, raw)

    # Fill any still-missing with minimal stubs so all 6 exist
    for name in REQUIRED_DOCS:
        path = docs_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            path.write_text(
                _fallback_doc(name, root, near_empty=near_empty),
                encoding="utf-8",
            )
            if name not in written:
                written.append(name)

    report_prompt = (
        "Write a concise Markdown bootstrap REPORT for the user after generating docs/:\n"
        "1. Current project state / stage\n"
        "2. What was found in the codebase\n"
        "3. Which docs were generated (and that PRD is reconstructed — please review)\n"
        "4. What should improve next\n"
        "Keep under 2500 chars.\n\n"
        f"Docs written: {', '.join(written)}\nNear-empty: {near_empty}\n\n"
        f"=== CODEBASE SUMMARY (truncated) ===\n{codebase[:12000]}\n"
    )
    report = _llm_call(report_prompt)
    msg = (
        f"Docs were missing/incomplete under {docs_dir}. "
        f"Generated/updated: {', '.join(written)}. Review docs before daily loop."
    )
    _record_bootstrap_state(BootstrapMode.DOCS_MISSING, root, report, docs_written=written)
    return BootstrapResult(
        mode=BootstrapMode.DOCS_MISSING,
        message=msg,
        report=report,
        docs_written=written,
        project_dir=str(root),
        stop_crew=True,
    )


def _write_docs_from_llm_output(docs_dir: Path, raw: str) -> list[str]:
    """Parse ```Filename.md ... ``` blocks and write files."""
    import re

    written: list[str] = []
    pattern = re.compile(
        r"```(?:markdown\s+)?(" + "|".join(re.escape(n) for n in REQUIRED_DOCS) + r")\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    # Also accept ```Architecture.md without markdown prefix — already in pattern
    found = {m.group(1): m.group(2).strip() for m in pattern.finditer(raw)}

    # Alternate: ### Architecture.md headers with content until next ###
    if len(found) < 3:
        alt = re.split(r"(?m)^(?:#{1,3}\s*)?(" + "|".join(re.escape(n) for n in REQUIRED_DOCS) + r")\s*$", raw)
        # split keeps delimiters: [pre, name, body, name, body, ...]
        i = 1
        while i + 1 < len(alt):
            name, body = alt[i], alt[i + 1]
            if name in REQUIRED_DOCS and body.strip():
                found.setdefault(name, body.strip())
            i += 2

    for name in REQUIRED_DOCS:
        content = found.get(name)
        if not content:
            # case-insensitive key match
            for k, v in found.items():
                if k.lower() == name.lower():
                    content = v
                    break
        if content:
            (docs_dir / name).write_text(content.strip() + "\n", encoding="utf-8")
            written.append(name)
    return written


def _fallback_doc(name: str, root: Path, *, near_empty: bool) -> str:
    stamp = _utc_now()
    note = "Fresh/near-empty project." if near_empty else f"Auto-stub for {root}."
    templates = {
        "Architecture.md": f"# Architecture\n\n_{note}_\n\nGenerated: {stamp}\n",
        "Design.md": f"# Design\n\n_{note}_\n\nGenerated: {stamp}\n",
        "Memory.md": f"# Memory\n\n- {stamp}: Bootstrap created Memory.md. {note}\n",
        "Phases.md": f"# Phases\n\n## Current\nBootstrap / discovery\n\n_{note}_\n",
        "PRD.md": (
            f"# PRD\n\n> reconstructed from code, please review\n\n_{note}_\n\nGenerated: {stamp}\n"
        ),
        "Rules.md": (
            f"# Rules\n\n- Prefer small, reviewable changes\n"
            f"- Never commit secrets\n- {note}\n\nGenerated: {stamp}\n"
        ),
    }
    return templates.get(name, f"# {name}\n\n{note}\n")


def _record_bootstrap_state(
    mode: BootstrapMode,
    root: Path,
    report: str,
    *,
    docs_written: list[str] | None = None,
) -> None:
    from state import load_state, save_state

    head = ""
    try:
        if is_git_repo(root):
            head = _run_git("git rev-parse --short HEAD", project_dir=root).strip().splitlines()[-1]
    except Exception:  # noqa: BLE001
        pass

    state = load_state()
    state["bootstrap"] = {
        "mode": mode.value,
        "timestamp": _utc_now(),
        "project_dir": str(root),
        "head": head,
        "docs_written": docs_written or [],
        "report_excerpt": (report or "")[:2000],
    }
    state["last_summary"] = report
    save_state(state)


def run_bootstrap(project_dir: Path | None = None) -> BootstrapResult:
    """Main entry: resolve mode and execute the corresponding branch."""
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    mode = resolve_bootstrap_mode(root)
    logger.info("Bootstrap mode=%s project=%s", mode.value, root)

    if mode == BootstrapMode.INVALID:
        msg = f"PROJECT_DIR does not exist or is not a folder: {root}"
        return BootstrapResult(mode=mode, message=msg, project_dir=str(root), stop_crew=True)

    if mode == BootstrapMode.EMPTY:
        msg = (
            "Project folder is empty. Add existing code, or create docs/ with the "
            "6 required files (Architecture, Design, Memory, Phases, PRD, Rules), "
            "before starting."
        )
        _record_bootstrap_state(mode, root, msg)
        return BootstrapResult(mode=mode, message=msg, report=msg, project_dir=str(root), stop_crew=True)

    if mode == BootstrapMode.DOCS_PRESENT:
        return run_full_audit(root)

    return run_full_analysis_and_generate_docs(root)
