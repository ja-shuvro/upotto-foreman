from __future__ import annotations

import os
from pathlib import Path
import pytest

import config
from phase_docs import PhaseInfo
from phase_engine import implement_phase
from tasks import Task, TaskStatus, create_task, get_tasks_by_phase
import state


@pytest.fixture(autouse=True)
def mock_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_state = tmp_path / "project_state.json"
    monkeypatch.setattr(state, "STATE_PATH", fake_state)
    monkeypatch.setattr(config, "validate_project_path", lambda p: str(p))
    monkeypatch.setenv("PROJECT_DIR", str(tmp_path))
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return fake_state


def test_implement_phase_auto_creates_task_and_runs_qa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    phase = PhaseInfo(number=1, title="Foundation Module", body="Build core setup")

    # Mock run_qa_check to automatically pass
    qa_checked_tasks = []

    def fake_run_qa_check(task_id: str, *, project_dir=None, retry_count=0):
        qa_checked_tasks.append(task_id)
        from tasks import update_task_status
        update_task_status(task_id, TaskStatus.DONE)
        return True

    monkeypatch.setattr("qa_engine.run_qa_check", fake_run_qa_check)

    class DummyCrew:
        def __init__(self, *args, **kwargs):
            pass

        def kickoff(self):
            return "Implementation report: - Created core module\n- Added basic tests"

    monkeypatch.setattr("crewai.Crew.kickoff", DummyCrew.kickoff)

    res = implement_phase(phase, project_dir=tmp_path)
    assert res is not None
    assert "implemented" in res

    # Verify task was created and QA was called
    tasks = get_tasks_by_phase("1")
    assert len(tasks) >= 1
    assert tasks[0].status == TaskStatus.DONE.value
    assert tasks[0].task_id in qa_checked_tasks


def test_implement_phase_fails_if_qa_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    phase = PhaseInfo(number=2, title="Failing Phase", body="Buggy setup")

    def failing_run_qa_check(task_id: str, *, project_dir=None, retry_count=0):
        from tasks import update_task_status
        update_task_status(task_id, TaskStatus.BUG)
        return False

    monkeypatch.setattr("qa_engine.run_qa_check", failing_run_qa_check)

    class DummyCrew:
        def __init__(self, *args, **kwargs):
            pass

        def kickoff(self):
            return "Implementation attempted"

    monkeypatch.setattr("crewai.Crew.kickoff", DummyCrew.kickoff)

    with pytest.raises(RuntimeError, match="failed QA check"):
        implement_phase(phase, project_dir=tmp_path)

