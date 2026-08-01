"""Parse Phases.md / Memory.md and persist phase analysis."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_docs_dir, get_project_dir

logger = logging.getLogger(__name__)

PHASE_HEADING_RE = re.compile(
    r"^(#{1,4})\s*Phase\s+(\d+)\s*[:\-–—]?\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
PHASE_BOLD_RE = re.compile(
    r"^\*\*Phase\s+(\d+)\s*[:\-–—]?\s*(.+?)\*\*\s*$",
    re.IGNORECASE | re.MULTILINE,
)
STATUS_BLOCK_RE = re.compile(
    r"<!--\s*FOREMAN_PHASE_STATUS\s*(.*?)\s*-->",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class PhaseInfo:
    number: int
    title: str
    body: str = ""
    status: str = "pending"  # pending | done | blocked | current

    def slug(self) -> str:
        raw = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return (raw[:40] or "work").strip("-")


@dataclass
class PhaseAnalysis:
    project_name: str
    phases: list[PhaseInfo] = field(default_factory=list)
    done: list[PhaseInfo] = field(default_factory=list)
    remaining: list[PhaseInfo] = field(default_factory=list)
    next_phase: PhaseInfo | None = None
    last_analyzed_commit: str = ""
    cached: bool = False
    case: str = "existing"  # existing | empty
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "phases": [asdict(p) for p in self.phases],
            "done": [asdict(p) for p in self.done],
            "remaining": [asdict(p) for p in self.remaining],
            "next_phase": asdict(self.next_phase) if self.next_phase else None,
            "last_analyzed_commit": self.last_analyzed_commit,
            "cached": self.cached,
            "case": self.case,
            "summary": self.summary,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_doc(name: str, *, project_dir: Path | None = None) -> str:
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    path = root / "docs" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_doc(name: str, content: str, *, project_dir: Path | None = None) -> Path:
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    path = docs / name
    path.write_text(content, encoding="utf-8")
    return path


def parse_phases_md(text: str) -> list[PhaseInfo]:
    """Extract Phase N headings and their body sections."""
    if not (text or "").strip():
        return []

    matches: list[tuple[int, int, str, int]] = []
    for m in PHASE_HEADING_RE.finditer(text):
        matches.append((m.start(), int(m.group(2)), m.group(3).strip(), m.end()))
    for m in PHASE_BOLD_RE.finditer(text):
        matches.append((m.start(), int(m.group(1)), m.group(2).strip(), m.end()))

    if not matches:
        # Fallback: numbered list "1. Title"
        for m in re.finditer(r"^\s*(\d+)\.\s+(.+)$", text, re.MULTILINE):
            matches.append((m.start(), int(m.group(1)), m.group(2).strip(), m.end()))

    if not matches:
        return []

    matches.sort(key=lambda x: (x[1], x[0]))
    # de-dupe by number (keep first)
    seen: set[int] = set()
    uniq: list[tuple[int, int, str, int]] = []
    for item in matches:
        if item[1] in seen:
            continue
        seen.add(item[1])
        uniq.append(item)
    uniq.sort(key=lambda x: x[1])

    phases: list[PhaseInfo] = []
    for i, (_start, num, title, body_start) in enumerate(uniq):
        end = uniq[i + 1][0] if i + 1 < len(uniq) else len(text)
        body = text[body_start:end].strip()
        phases.append(PhaseInfo(number=num, title=title, body=body))
    return phases


def parse_memory_status(text: str) -> dict[str, Any]:
    """Read <!-- FOREMAN_PHASE_STATUS ... --> block from Memory.md."""
    m = STATUS_BLOCK_RE.search(text or "")
    if not m:
        return {}
    data: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip().lower()
        val = v.strip()
        if key in {"phases_done", "phases_completed"}:
            nums = []
            for part in re.split(r"[,\s]+", val):
                if part.isdigit():
                    nums.append(int(part))
            data["phases_completed"] = nums
        elif key == "last_analyzed_commit":
            data["last_analyzed_commit"] = val
        elif key == "current_phase":
            if val.isdigit():
                data["current_phase"] = int(val)
        else:
            data[key] = val
    return data


def working_tree_fingerprint(project_dir: Path | None = None) -> str:
    """Cheap fingerprint of tracked+untracked project files (excludes ignored dirs)."""
    from config import IGNORED_DIRS

    root = Path(project_dir) if project_dir is not None else get_project_dir()
    h = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(p in IGNORED_DIRS or p.startswith(".") for p in rel_parts[:-1]):
            if ".git" in rel_parts:
                continue
            if any(p in IGNORED_DIRS for p in rel_parts):
                continue
        if path.name in {".DS_Store"}:
            continue
        # skip heavy/binary-ish
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".exe", ".dll", ".zip"}:
            continue
        files.append(path)
    files.sort(key=lambda p: str(p).lower())
    for path in files[:2000]:
        try:
            st = path.stat()
            rel = path.relative_to(root).as_posix()
            h.update(rel.encode("utf-8", errors="replace"))
            h.update(str(st.st_mtime_ns).encode())
            h.update(str(st.st_size).encode())
        except OSError:
            continue
    return h.hexdigest()[:40]


def apply_completion_to_phases(
    phases: list[PhaseInfo],
    completed: list[int],
    *,
    current: int | None = None,
) -> list[PhaseInfo]:
    done_set = set(completed or [])
    out: list[PhaseInfo] = []
    for p in phases:
        status = "pending"
        if p.number in done_set:
            status = "done"
        elif current is not None and p.number == current:
            status = "current"
        out.append(
            PhaseInfo(number=p.number, title=p.title, body=p.body, status=status)
        )
    return out


def update_memory_phase_status(
    *,
    project_dir: Path | None = None,
    last_analyzed_commit: str = "",
    phases_completed: list[int] | None = None,
    current_phase: int | None = None,
    note: str = "",
) -> None:
    """Upsert FOREMAN_PHASE_STATUS block + append a run note."""
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    text = read_doc("Memory.md", project_dir=root)
    block_lines = [
        "<!-- FOREMAN_PHASE_STATUS",
        f"last_analyzed_commit: {last_analyzed_commit}",
        f"phases_completed: {','.join(str(n) for n in (phases_completed or []))}",
    ]
    if current_phase is not None:
        block_lines.append(f"current_phase: {current_phase}")
    block_lines.append("updated_at: " + _utc_now())
    block_lines.append("-->")
    block = "\n".join(block_lines)

    if STATUS_BLOCK_RE.search(text):
        text = STATUS_BLOCK_RE.sub(block, text, count=1)
    else:
        text = (text.rstrip() + "\n\n" + block + "\n").lstrip()

    if note:
        text = text.rstrip() + f"\n\n### Foreman run — {_utc_now()}\n{note.strip()}\n"

    write_doc("Memory.md", text, project_dir=root)


def append_phase_completion_memory(
    phase: PhaseInfo,
    *,
    project_dir: Path | None = None,
    branch: str,
    commit: str,
    implemented: list[str],
    deferred: list[str],
    tests: str,
    merged: bool,
) -> None:
    bullets_impl = "\n".join(f"- {x}" for x in implemented) or "- (see commit)"
    bullets_def = "\n".join(f"- {x}" for x in deferred) or "- none"
    note = (
        f"**Phase {phase.number}: {phase.title} — DONE**\n"
        f"- Branch: `{branch}`\n"
        f"- Commit: `{commit or 'n/a'}`\n"
        f"- Merged into dev: {'yes' if merged else 'no'}\n"
        f"- Tests: {tests}\n"
        f"**Implemented:**\n{bullets_impl}\n"
        f"**Deferred:**\n{bullets_def}\n"
    )
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    mem = read_doc("Memory.md", project_dir=root)
    status = parse_memory_status(mem)
    completed = list(status.get("phases_completed") or [])
    if phase.number not in completed:
        completed.append(phase.number)
    completed = sorted(set(completed))
    update_memory_phase_status(
        project_dir=root,
        last_analyzed_commit=status.get("last_analyzed_commit") or commit,
        phases_completed=completed,
        current_phase=phase.number + 1,
        note=note,
    )


def project_display_name(project_dir: Path | None = None) -> str:
    root = Path(project_dir) if project_dir is not None else get_project_dir()
    return root.name
