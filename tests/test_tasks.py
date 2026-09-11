from __future__ import annotations

import pytest
from pathlib import Path

from tasks import (
    BugReport,
    BugSeverity,
    BugStatus,
    Task,
    TaskStatus,
    add_bug_report,
    create_task,
    get_all_tasks,
    get_task,
    get_tasks_by_phase,
    get_tasks_by_status,
    update_task_status,
)
import state


@pytest.fixture(autouse=True)
def mock_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_state = tmp_path / "project_state.json"
    monkeypatch.setattr(state, "STATE_PATH", fake_state)
    return fake_state


def test_create_and_get_task():
    task = create_task(
        task_id="task-1",
        phase="1",
        role="backend",
        title="Build Auth API",
        status=TaskStatus.TODO,
        depends_on=[],
        acceptance_criteria="JWT auth returns 200 on valid credentials",
    )
    assert task.task_id == "task-1"
    assert task.phase == "1"
    assert task.role == "backend"
    assert task.title == "Build Auth API"
    assert task.status == "todo"
    assert task.acceptance_criteria == "JWT auth returns 200 on valid credentials"

    # Dict-like access
    assert task["status"] == "todo"
    assert task.get("role") == "backend"

    fetched = get_task("task-1")
    assert fetched is not None
    assert fetched.task_id == "task-1"
    assert fetched.status == "todo"


def test_update_task_status():
    create_task("task-2", "1", "frontend", "Login Form")
    updated = update_task_status("task-2", TaskStatus.IN_PROGRESS)
    assert updated is not None
    assert updated.status == "in_progress"

    fetched = get_task("task-2")
    assert fetched is not None
    assert fetched.status == "in_progress"


def test_get_tasks_by_phase_and_status():
    create_task("t1", "1", "backend", "Task 1", status=TaskStatus.DONE)
    create_task("t2", "1", "frontend", "Task 2", status=TaskStatus.TODO)
    create_task("t3", "2", "backend", "Task 3", status=TaskStatus.TODO)

    phase_1 = get_tasks_by_phase("1")
    assert len(phase_1) == 2
    assert {t.task_id for t in phase_1} == {"t1", "t2"}

    todo_tasks = get_tasks_by_status(TaskStatus.TODO)
    assert len(todo_tasks) == 2
    assert {t.task_id for t in todo_tasks} == {"t2", "t3"}

    done_tasks = get_tasks_by_status("done")
    assert len(done_tasks) == 1
    assert done_tasks[0].task_id == "t1"


def test_add_bug_report():
    create_task("t4", "1", "backend", "Task 4")
    bug = {
        "bug_id": "BUG-001",
        "severity": "critical",
        "steps_to_reproduce": "Send invalid JSON to /login",
        "expected": "400 Bad Request",
        "actual": "500 Server Error",
    }
    report = add_bug_report("t4", bug)
    assert report is not None
    assert report["bug_id"] == "BUG-001"
    assert report["reported_by"] == "QA Tester"
    assert report["status"] == "open"

    t4 = get_task("t4")
    assert t4 is not None
    assert len(t4.bug_reports) == 1
    assert t4.bug_reports[0]["bug_id"] == "BUG-001"


def test_bug_report_dataclass():
    report = BugReport(
        bug_id="BUG-002",
        severity=BugSeverity.MAJOR,
        steps_to_reproduce="Click login with empty pass",
        expected="Show error message",
        actual="Blank screen",
        status=BugStatus.OPEN,
    )
    assert report.severity == "major"
    assert report.status == "open"
    assert report["bug_id"] == "BUG-002"
    d = report.to_dict()
    assert d["reported_by"] == "QA Tester"

