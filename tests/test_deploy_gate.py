from __future__ import annotations

import os
from pathlib import Path
import pytest

from deploy_gate import (
    check_production_readiness,
    deploy_to_production,
    request_production_approval,
)
from tasks import TaskStatus, create_task
import state


@pytest.fixture(autouse=True)
def mock_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_state = tmp_path / "project_state.json"
    monkeypatch.setattr(state, "STATE_PATH", fake_state)
    fake_approval = tmp_path / "pending_approval.json"
    monkeypatch.setattr(state, "PENDING_APPROVAL_PATH", fake_approval)

    # Setup basic gitignore with .env
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".env\n.venv/\n", encoding="utf-8")

    # Mock git_safe head_commit
    monkeypatch.setattr("git_safe.head_commit", lambda p: "abc1234")

    # Mock test command and test run
    monkeypatch.setattr("phase_engine.detect_test_command", lambda p: "pytest")
    monkeypatch.setattr("phase_engine.run_tests", lambda p, cmd: {"ok": True, "command": cmd})

    return tmp_path


def test_check_production_readiness_pass(tmp_path: Path):
    create_task("task-1", "1", "backend", "Setup API", status=TaskStatus.DONE)
    create_task("task-2", "1", "frontend", "Setup UI", status=TaskStatus.DONE)

    def mock_devops(p: Path) -> str:
        return "READY: Architecture, secrets, and tests verified."

    report = check_production_readiness(tmp_path, devops_runner=mock_devops)
    assert report["ready"] is True
    assert report["checklist"]["all_tasks_done"] is True
    assert report["checklist"]["env_gitignored"] is True
    assert report["checklist"]["no_exposed_secrets"] is True
    assert report["checklist"]["rollback_path_exists"] is True
    assert report["checklist"]["tests_pass"] is True
    assert report["checklist"]["devops_review"] is True
    assert len(report["blocking_issues"]) == 0


def test_check_production_readiness_fails_on_unfinished_task(tmp_path: Path):
    create_task("task-1", "1", "backend", "Setup API", status=TaskStatus.DONE)
    create_task("task-2", "1", "frontend", "Setup UI", status=TaskStatus.IN_PROGRESS)

    def mock_devops(p: Path) -> str:
        return "READY"

    report = check_production_readiness(tmp_path, devops_runner=mock_devops)
    assert report["ready"] is False
    assert report["checklist"]["all_tasks_done"] is False
    assert any("task-2" in issue for issue in report["blocking_issues"])


def test_check_production_readiness_fails_without_env_gitignored(tmp_path: Path):
    create_task("task-1", "1", "backend", "Setup API", status=TaskStatus.DONE)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")

    def mock_devops(p: Path) -> str:
        return "READY"

    report = check_production_readiness(tmp_path, devops_runner=mock_devops)
    assert report["ready"] is False
    assert report["checklist"]["env_gitignored"] is False
    assert any(".env" in issue for issue in report["blocking_issues"])


def test_check_production_readiness_fails_on_exposed_secret(tmp_path: Path):
    create_task("task-1", "1", "backend", "Setup API", status=TaskStatus.DONE)
    leak = tmp_path / "secret.py"
    leak.write_text('OPENAI_KEY = "sk-abcdef1234567890abcdef1234567890"', encoding="utf-8")

    def mock_devops(p: Path) -> str:
        return "READY"

    report = check_production_readiness(tmp_path, devops_runner=mock_devops)
    assert report["ready"] is False
    assert report["checklist"]["no_exposed_secrets"] is False
    assert any("secret" in issue.lower() for issue in report["blocking_issues"])


def test_check_production_readiness_fails_on_devops_not_ready(tmp_path: Path):
    create_task("task-1", "1", "backend", "Setup API", status=TaskStatus.DONE)

    def mock_devops(p: Path) -> str:
        return "NOT_READY: Missing rollback strategy for database migration."

    report = check_production_readiness(tmp_path, devops_runner=mock_devops)
    assert report["ready"] is False
    assert report["checklist"]["devops_review"] is False
    assert any("NOT_READY" in issue for issue in report["blocking_issues"])


def test_request_production_approval_blocked():
    readiness = {
        "ready": False,
        "checklist": {"all_tasks_done": False},
        "blocking_issues": ["Task 1 unfinished"],
        "devops_report": "NOT_READY",
    }
    res = request_production_approval(readiness)
    assert res["status"] == "blocked"
    assert state.load_pending_approval() is None


def test_request_production_approval_granted_path():
    readiness = {
        "ready": True,
        "checklist": {
            "all_tasks_done": True,
            "env_gitignored": True,
            "no_exposed_secrets": True,
            "rollback_path_exists": True,
            "tests_pass": True,
            "devops_review": True,
        },
        "blocking_issues": [],
        "devops_report": "READY",
    }
    res = request_production_approval(readiness)
    assert res["status"] == "pending_approval"
    assert res["kind"] == "production_deploy"

    pending = state.load_pending_approval()
    assert pending is not None
    assert pending["kind"] == "production_deploy"
    assert state.load_state()["phase_status"] == "awaiting_production_approval"


def test_deploy_to_production_stub_when_no_mechanism(tmp_path: Path):
    # Set pending approval first
    state.set_pending_approval(state.load_state(), "deploy", kind="production_deploy")
    res = deploy_to_production(tmp_path, approved_by="Alice")
    assert res is True

    curr_state = state.load_state()
    assert "production_deploy" in curr_state
    assert curr_state["production_deploy"]["status"] == "manual_required"
    assert curr_state["production_deploy"]["approved_by"] == "Alice"
    assert curr_state["pending_approval"] is None


def test_deploy_to_production_with_deploy_script(tmp_path: Path):
    deploy_sh = tmp_path / "deploy.sh"
    deploy_sh.write_text("#!/bin/bash\necho deploying", encoding="utf-8")

    res = deploy_to_production(tmp_path, approved_by="Bob")
    assert res is True

    curr_state = state.load_state()
    assert curr_state["production_deploy"]["status"] == "success"
    assert curr_state["production_deploy"]["approved_by"] == "Bob"

