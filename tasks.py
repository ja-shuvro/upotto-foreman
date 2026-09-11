"""Task definitions, schema, and persistence for Upotto Foreman."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from crewai import Agent, Task as CrewAITask

from config import REQUIRED_DOCS, get_docs_dir, get_project_dir, load_project_brief
from state import load_state, save_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CrewAI Planning Tasks (daily loop compatibility)
# ---------------------------------------------------------------------------


def create_plan_task(architect: Agent) -> CrewAITask:
    project_dir = get_project_dir()
    docs_dir = get_docs_dir()
    docs_list = ", ".join(REQUIRED_DOCS)
    brief = load_project_brief(max_chars_per_file=8000)

    directive_block = ""
    try:
        directive = (load_state().get("user_directive") or "").strip()
        if directive:
            directive_block = (
                f"USER DIRECTIVE (priority): {directive}\n"
                "Incorporate this into today's plan as the highest priority. "
                "Scan + auto-discover only fills gaps around it.\n\n"
            )
    except Exception:  # noqa: BLE001
        pass

    return CrewAITask(
        description=(
            f"You are planning today's engineering work for the project at: {project_dir}\n\n"
            f"{directive_block}"
            f"{brief}\n\n"
            f"REQUIRED: Treat the Project Brief above as authoritative. You may also re-read "
            f"files under {docs_dir} ({docs_list}) with FileReadTool if needed.\n"
            "Use list_project_files to scan the codebase after reviewing the docs.\n"
            "Also consider git state (uncommitted work) when choosing the next task.\n\n"
            "Steps:\n"
            "1. Internalize the USER DIRECTIVE (if present) — it outranks auto-discovery.\n"
            "2. Internalize PRD / Architecture / Design / Memory / Phases / Rules from the brief.\n"
            "3. Scan the project structure and key source files (fill gaps around the directive).\n"
            "4. Use web_search for industry-standard approaches relevant to the next step "
            "in Phases.md / PRD.\n"
            "5. Decide the single best next task for today (one focused change).\n"
            "6. Produce a clear implementation plan: goals, files to touch, tests, "
            "acceptance criteria, and risks.\n"
            "7. If the change is large / architectural / breaking, include exactly:\n"
            "   NEEDS_APPROVAL: <reason>\n"
            "   Otherwise state: APPROVED_FOR_DEV (no human approval gate).\n"
            "Respect Rules.md constraints at all times.\n\n"
            "Output a structured daily plan the Developer can execute."
        ),
        expected_output=(
            "A structured daily plan with: objective, rationale, file list, steps, "
            "tests, risks, and either NEEDS_APPROVAL: <reason> or APPROVED_FOR_DEV."
        ),
        agent=architect,
        human_input=True,
    )


def create_dev_task(developer: Agent, plan_task: CrewAITask | None = None) -> CrewAITask:
    project_dir = get_project_dir()
    return CrewAITask(
        description=(
            f"Implement today's plan for the project at: {project_dir}\n\n"
            "Rules:\n"
            "- Follow the Architect plan exactly (from context / injected plan text).\n"
            "- Follow constraints in docs/Rules.md and Architecture.md when writing code.\n"
            "- If the plan contains NEEDS_APPROVAL and human approval was not given, "
            "do NOT implement; report BLOCKED_PENDING_APPROVAL.\n"
            "- Write / update code with FileWriterTool and FileReadTool.\n"
            "- Add or update tests.\n"
            "- Use the run_code_execution tool (alias: code_execution) to run shell commands "
            "INSIDE the project dir, "
            "e.g. command='pytest -q', command='npm test', "
            "command='git add -A && git commit -m \"...\"'. "
            "Only python/pytest/npm/npx/pnpm/yarn/git/node/pip/ls/cat commands are allowed.\n"
            "- Create a git commit with a concise message (never commit secrets / .env).\n"
            "- Summarize what you changed, including commands run and their output."
        ),
        expected_output=(
            "Implementation report: files changed, exact test command + result, git commit "
            "hash or message, or BLOCKED_PENDING_APPROVAL with reason."
        ),
        agent=developer,
        context=[plan_task] if plan_task is not None else [],
    )


def create_summary_task(architect: Agent, plan_task: CrewAITask | None, dev_task: CrewAITask) -> CrewAITask:
    return CrewAITask(
        description=(
            "Write a concise 5–10 line daily summary covering:\n"
            "- What was planned\n"
            "- What was implemented (or blocked)\n"
            "- Tests / commit status\n"
            "- Any remaining risks or next steps\n"
            "- Whether NEEDS_APPROVAL is still open\n\n"
            "Keep it suitable for Telegram / email notification."
        ),
        expected_output="A 5–10 line plain-text daily summary.",
        agent=architect,
        context=[t for t in (plan_task, dev_task) if t is not None],
    )


# ---------------------------------------------------------------------------
# Phase Task & Bug Schema
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    QA = "qa"
    BUG = "bug"
    DONE = "done"


class BugSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class BugStatus(str, Enum):
    OPEN = "open"
    FIXED = "fixed"
    VERIFIED = "verified"


@dataclass
class BugReport:
    bug_id: str
    severity: str  # "critical" | "major" | "minor"
    steps_to_reproduce: str
    expected: str
    actual: str
    reported_by: str = "QA Tester"
    status: str = BugStatus.OPEN.value

    def __post_init__(self) -> None:
        if isinstance(self.severity, BugSeverity):
            self.severity = self.severity.value
        if isinstance(self.status, BugStatus):
            self.status = self.status.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BugReport:
        return cls(
            bug_id=str(data.get("bug_id", "")),
            severity=str(data.get("severity", "major")),
            steps_to_reproduce=str(data.get("steps_to_reproduce", "")),
            expected=str(data.get("expected", "")),
            actual=str(data.get("actual", "")),
            reported_by=str(data.get("reported_by", "QA Tester")),
            status=str(data.get("status", BugStatus.OPEN.value)),
        )

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class Task:
    task_id: str
    phase: str
    role: str
    title: str
    status: str = TaskStatus.TODO.value
    depends_on: list[str] = field(default_factory=list)
    bug_reports: list[dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: str = ""
    output: str = ""

    def __post_init__(self) -> None:
        self.phase = str(self.phase)
        if isinstance(self.status, TaskStatus):
            self.status = self.status.value
        if self.depends_on is None:
            self.depends_on = []
        if self.bug_reports is None:
            self.bug_reports = []

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            task_id=str(data.get("task_id", "")),
            phase=str(data.get("phase", "")),
            role=str(data.get("role", "")),
            title=str(data.get("title", "")),
            status=str(data.get("status", TaskStatus.TODO.value)),
            depends_on=list(data.get("depends_on") or []),
            bug_reports=list(data.get("bug_reports") or []),
            acceptance_criteria=str(data.get("acceptance_criteria", "")),
            output=str(data.get("output", "")),
        )

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# Task Persistence Helpers (using project_state.json)
# ---------------------------------------------------------------------------


def get_all_tasks() -> list[Task]:
    """Retrieve all tasks from project_state.json."""
    state = load_state()
    raw_tasks = state.get("tasks", [])
    if isinstance(raw_tasks, dict):
        raw_tasks = list(raw_tasks.values())
    return [Task.from_dict(t) if isinstance(t, dict) else t for t in raw_tasks]


def _save_tasks(tasks: list[Task]) -> None:
    """Save tasks list to project_state.json under 'tasks' key."""
    state = load_state()
    state["tasks"] = [t.to_dict() if isinstance(t, Task) else t for t in tasks]
    save_state(state)


def get_task(task_id: str) -> Task | None:
    """Find a task by task_id."""
    for task in get_all_tasks():
        if task.task_id == task_id:
            return task
    return None


def create_task(
    task_id: str,
    phase: str | int,
    role: str,
    title: str,
    status: str | TaskStatus = TaskStatus.TODO,
    depends_on: list[str] | None = None,
    bug_reports: list[dict[str, Any]] | None = None,
    acceptance_criteria: str = "",
    output: str = "",
    **kwargs: Any,
) -> Task:
    """Create a new task and persist it inside project_state.json."""
    status_str = status.value if isinstance(status, TaskStatus) else str(status)
    task = Task(
        task_id=str(task_id),
        phase=str(phase),
        role=str(role),
        title=str(title),
        status=status_str,
        depends_on=list(depends_on or []),
        bug_reports=list(bug_reports or []),
        acceptance_criteria=acceptance_criteria or str(kwargs.get("criteria", "")),
        output=output or str(kwargs.get("result", "")),
    )

    tasks = get_all_tasks()
    # Replace existing if same task_id, otherwise append
    updated = False
    for idx, existing in enumerate(tasks):
        if existing.task_id == task.task_id:
            tasks[idx] = task
            updated = True
            break
    if not updated:
        tasks.append(task)

    _save_tasks(tasks)
    logger.info("Saved task %s (%s) phase=%s role=%s", task.task_id, task.status, task.phase, task.role)
    return task


def update_task_status(task_id: str, new_status: str | TaskStatus) -> Task | None:
    """Update status of a task by task_id and persist."""
    status_str = new_status.value if isinstance(new_status, TaskStatus) else str(new_status)
    tasks = get_all_tasks()
    target_task: Task | None = None
    for task in tasks:
        if task.task_id == task_id:
            task.status = status_str
            target_task = task
            break

    if target_task:
        _save_tasks(tasks)
        logger.info("Updated task %s status -> %s", task_id, status_str)
    else:
        logger.warning("Task %s not found for status update", task_id)

    return target_task


def get_tasks_by_phase(phase: str | int) -> list[Task]:
    """Retrieve all tasks matching given phase."""
    phase_str = str(phase)
    return [t for t in get_all_tasks() if t.phase == phase_str]


def get_tasks_by_status(status: str | TaskStatus) -> list[Task]:
    """Retrieve all tasks matching given status."""
    status_str = status.value if isinstance(status, TaskStatus) else str(status)
    return [t for t in get_all_tasks() if t.status == status_str]


def add_bug_report(
    task_id: str,
    bug_report_dict: dict[str, Any] | BugReport,
) -> dict[str, Any] | None:
    """Append bug report to task and persist."""
    if isinstance(bug_report_dict, BugReport):
        report_data = bug_report_dict.to_dict()
    elif isinstance(bug_report_dict, dict):
        report_data = dict(bug_report_dict)
    else:
        report_data = asdict(bug_report_dict)

    # Ensure defaults
    report_data.setdefault("reported_by", "QA Tester")
    report_data.setdefault("status", BugStatus.OPEN.value)

    tasks = get_all_tasks()
    target_task: Task | None = None
    for task in tasks:
        if task.task_id == task_id:
            task.bug_reports.append(report_data)
            target_task = task
            break

    if target_task:
        _save_tasks(tasks)
        logger.info("Added bug report %s to task %s", report_data.get("bug_id"), task_id)
        return report_data

    logger.warning("Task %s not found to add bug report", task_id)
    return None
