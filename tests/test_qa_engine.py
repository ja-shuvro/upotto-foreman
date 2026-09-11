from __future__ import annotations

import pytest
from pathlib import Path

from tasks import BugStatus, Task, TaskStatus, create_task, get_task
from qa_engine import notify_pm, parse_qa_result, run_qa_check
import state
import phase_engine


@pytest.fixture(autouse=True)
def mock_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_state = tmp_path / "project_state.json"
    monkeypatch.setattr(state, "STATE_PATH", fake_state)
    return fake_state


def test_parse_qa_result_pass():
    task = Task(task_id="t1", phase="1", role="backend", title="Auth", acceptance_criteria="OK")
    passed, bug = parse_qa_result("PASS: All unit tests and acceptance criteria met.", task)
    assert passed is True
    assert bug is None


def test_parse_qa_result_fail():
    task = Task(task_id="t1", phase="1", role="backend", title="Auth", acceptance_criteria="OK")
    raw = (
        "FAIL\n"
        "Bug ID: BUG-AUTH-001\n"
        "Severity: critical\n"
        "Steps to Reproduce: POST /login with null password\n"
        "Expected: 400 Bad Request\n"
        "Actual: 500 Internal Server Error\n"
    )
    passed, bug = parse_qa_result(raw, task)
    assert passed is False
    assert bug is not None
    assert bug["bug_id"] == "BUG-AUTH-001"
    assert bug["severity"] == "critical"
    assert "POST /login" in bug["steps_to_reproduce"]
    assert "400 Bad Request" in bug["expected"]
    assert "500 Internal Server Error" in bug["actual"]
    assert bug["reported_by"] == "QA Tester"
    assert bug["status"] == BugStatus.OPEN.value


def test_notify_pm_telegram_integration(monkeypatch: pytest.MonkeyPatch):
    called = {}

    def fake_send_telegram(msg: str, parse_mode=None):
        called["msg"] = msg

    monkeypatch.setattr("notify.send_telegram", fake_send_telegram)
    notify_pm("task-99", "done")
    assert "task-99" in called["msg"]
    assert "done" in called["msg"]


def test_run_qa_check_pass_path():
    task = create_task("t-pass", "1", "backend", "Endpoint", acceptance_criteria="Return 200")

    def mock_qa(t: Task) -> str:
        return "PASS: verified endpoint returns 200"

    result = run_qa_check("t-pass", qa_runner=mock_qa)
    assert result is True
    updated = get_task("t-pass")
    assert updated is not None
    assert updated.status == TaskStatus.DONE.value


def test_run_qa_check_fail_then_pass_loop():
    task = create_task("t-loop", "1", "backend", "Validation", acceptance_criteria="Validate email")
    attempts = {"count": 0}

    def mock_qa(t: Task) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return (
                "FAIL\n"
                "Bug ID: BUG-VAL-1\n"
                "Severity: major\n"
                "Steps to Reproduce: Enter test@ invalid email\n"
                "Expected: Validation error\n"
                "Actual: Accepted\n"
            )
        return "PASS: email validation working now"

    def mock_fix(t: Task, bug: dict) -> str:
        return f"Fixed regex for bug {bug['bug_id']}"

    result = run_qa_check("t-loop", qa_runner=mock_qa, fix_runner=mock_fix)
    assert result is True
    assert attempts["count"] == 2

    updated = get_task("t-loop")
    assert updated is not None
    assert updated.status == TaskStatus.DONE.value
    assert len(updated.bug_reports) == 1
    assert updated.bug_reports[0]["status"] == BugStatus.FIXED.value
    assert "Fixed regex" in updated.output


def test_run_qa_check_exceeds_max_retries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(phase_engine, "MAX_TEST_RETRIES", 2)
    task = create_task("t-fail", "1", "frontend", "Button", acceptance_criteria="Clickable")

    def always_fail_qa(t: Task) -> str:
        return "FAIL: Button not clickable"

    def mock_fix(t: Task, bug: dict) -> str:
        return "Attempted fix"

    result = run_qa_check("t-fail", qa_runner=always_fail_qa, fix_runner=mock_fix)
    assert result is False
    updated = get_task("t-fail")
    assert updated is not None
    assert updated.status == TaskStatus.BUG.value

