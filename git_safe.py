"""Safe git helpers for phase engine — main is permanently off-limits."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from config import get_project_dir, validate_project_path

logger = logging.getLogger(__name__)

PROTECTED_BRANCHES = frozenset({"main", "master"})


def is_dangerous_git_command(command: str) -> str | None:
    """Return reason string if git command is blocked, else None."""
    raw = (command or "").strip()
    if not raw:
        return "empty command"
    low = raw.lower()

    # Force push / force flags on git
    if re.search(r"\bgit\b", low) and re.search(r"(^|\s)(-f|--force|--force-with-lease)\b", low):
        return "force git operations are blocked"

    if re.search(r"\bgit\s+push\b.*\b(origin\s+)?(main|master)\b", low):
        return "push to main/master is blocked"
    if re.search(r"\bgit\s+push\b[^\n]*:(main|master)\b", low):
        return "push refspec to main/master is blocked"

    if re.search(r"\bgit\s+merge\b[^\n]*\b(main|master)\b", low):
        # merging main INTO current is ok-ish; merging INTO main is the danger
        # block: checkout main && merge, or merge ... while specifying main as target awkwardly
        pass

    # checkout main followed by merge in same chained command
    if "checkout main" in low or "checkout master" in low:
        if "merge" in low:
            return "checkout main/master combined with merge is blocked"
        if "reset" in low or "rebase" in low:
            return "mutating main/master is blocked"

    if re.search(r"\bgit\s+rebase\b[^\n]*\b(main|master)\b", low):
        return "rebase involving main/master is blocked"

    if re.search(r"\bgit\s+branch\s+(-d|-D)\s+(main|master)\b", low):
        return "deleting main/master is blocked"
    if re.search(r"\bgit\s+branch\s+(-d|-D)\s+dev\b", low):
        return "deleting dev is blocked"

    # Explicit merge into main via checkout then merge is covered above.
    # Also block: git merge X main (unusual)
    if re.search(r"\bgit\s+merge\b.+\s+into\s+(main|master)\b", low):
        return "merge into main/master is blocked"

    return None


def run_git(
    *args: str,
    project_dir: Path | None = None,
    check: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    cmd = ["git", *args]
    # Reconstruct for blocklist in shell-ish form
    blocked = is_dangerous_git_command(" ".join(cmd))
    if blocked:
        raise PermissionError(f"Blocked git command: {blocked} ({' '.join(cmd)})")

    logger.info("git %s (cwd=%s)", " ".join(args), root)
    return subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def git_out(*args: str, project_dir: Path | None = None) -> str:
    cp = run_git(*args, project_dir=project_dir)
    return ((cp.stdout or "") + (cp.stderr or "")).strip()


def git_ok(*args: str, project_dir: Path | None = None) -> bool:
    return run_git(*args, project_dir=project_dir).returncode == 0


def is_git_repo(project_dir: Path | None = None) -> bool:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    return (root / ".git").exists()


def current_branch(project_dir: Path | None = None) -> str:
    return git_out("rev-parse", "--abbrev-ref", "HEAD", project_dir=project_dir)


def head_commit(project_dir: Path | None = None) -> str:
    cp = run_git("rev-parse", "HEAD", project_dir=project_dir)
    if cp.returncode != 0:
        return ""
    return (cp.stdout or "").strip()


def ensure_main_and_dev(project_dir: Path | None = None) -> dict[str, Any]:
    """Init repo if needed; ensure main + dev branches exist. Never deletes them."""
    root = Path(validate_project_path(project_dir or get_project_dir()))
    created = False
    if not is_git_repo(root):
        run_git("init", "-b", "main", project_dir=root)
        # empty commit so branches exist
        run_git("commit", "--allow-empty", "-m", "chore: initial empty commit", project_dir=root)
        created = True

    # Normalize default branch name if possible
    branch = current_branch(root)
    if branch in {"master"} and not git_ok("show-ref", "--verify", "refs/heads/main", project_dir=root):
        run_git("branch", "-M", "main", project_dir=root)
        branch = "main"

    if not git_ok("show-ref", "--verify", "refs/heads/dev", project_dir=root):
        # create dev from main/master/current
        base = "main" if git_ok("show-ref", "--verify", "refs/heads/main", project_dir=root) else branch
        run_git("branch", "dev", base, project_dir=root)

    run_git("checkout", "dev", project_dir=root)
    return {
        "created_repo": created,
        "branch": current_branch(root),
        "head": head_commit(root),
    }


def create_phase_branch(phase_num: int, slug: str, *, project_dir: Path | None = None) -> str:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    ensure_main_and_dev(root)
    run_git("checkout", "dev", project_dir=root)
    # best-effort pull
    run_git("pull", "--ff-only", project_dir=root)
    name = f"phase-{phase_num}-{slug}"
    # if exists, checkout; else create
    if git_ok("show-ref", "--verify", f"refs/heads/{name}", project_dir=root):
        run_git("checkout", name, project_dir=root)
    else:
        run_git("checkout", "-b", name, project_dir=root)
    return name


def commit_all(message: str, *, project_dir: Path | None = None) -> str:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    br = current_branch(root)
    if br in PROTECTED_BRANCHES:
        raise PermissionError(f"Refusing to commit on protected branch {br}")
    run_git("add", "-A", project_dir=root)
    # skip if nothing to commit
    staged = run_git("diff", "--cached", "--quiet", project_dir=root)
    if staged.returncode == 0:
        return head_commit(root)
    cp = run_git("commit", "-m", message, project_dir=root)
    if cp.returncode != 0:
        logger.warning("commit failed: %s", cp.stderr)
    return head_commit(root)


def merge_phase_into_dev(branch: str, *, project_dir: Path | None = None) -> dict[str, Any]:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    if branch in PROTECTED_BRANCHES or branch == "dev":
        raise PermissionError(f"Invalid phase branch: {branch}")
    run_git("checkout", "dev", project_dir=root)
    cp = run_git("merge", "--no-ff", branch, "-m", f"merge: {branch} into dev", project_dir=root)
    ok = cp.returncode == 0
    if not ok:
        run_git("merge", "--abort", project_dir=root)
    return {
        "ok": ok,
        "stdout": cp.stdout or "",
        "stderr": cp.stderr or "",
        "head": head_commit(root),
        "branch": current_branch(root),
    }


def push_branch(branch: str, *, project_dir: Path | None = None) -> dict[str, Any]:
    root = Path(validate_project_path(project_dir or get_project_dir()))
    if branch in PROTECTED_BRANCHES:
        raise PermissionError("push of main/master blocked")
    # only if remote exists
    remotes = git_out("remote", project_dir=root)
    if not remotes:
        return {"ok": True, "skipped": True, "reason": "no_remote"}
    cp = run_git("push", "-u", "origin", branch, project_dir=root)
    return {"ok": cp.returncode == 0, "stdout": cp.stdout, "stderr": cp.stderr}
