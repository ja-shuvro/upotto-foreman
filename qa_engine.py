"""QA verification engine with automated engineer bug-fix loop."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from tasks import (
    BugReport,
    BugSeverity,
    BugStatus,
    Task,
    TaskStatus,
    add_bug_report,
    get_all_tasks,
    get_task,
    update_task_status,
)

logger = logging.getLogger(__name__)


def notify_pm(task_id: str, status: str = "done") -> None:
    """PM notification hook, wired to Telegram if configured."""
    msg = f"Task {task_id} QA check completed: status={status}"
    logger.info(msg)
    try:
        from notify import send_telegram

        send_telegram(msg, parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("notify_pm Telegram notification skipped: %s", exc)


def parse_qa_result(output: str, task: Task) -> tuple[bool, dict[str, Any] | None]:
    """Parse QA output into PASS or structured BugReport dict."""
    is_fail = bool(
        re.search(r"\bFAIL\b", output, re.IGNORECASE)
        or re.search(r"\bBug\s*ID\b", output, re.IGNORECASE)
        or re.search(r"\bSeverity\b", output, re.IGNORECASE)
    )

    if not is_fail and "pass" in output.lower():
        return True, None
    if not is_fail:
        return True, None

    bug_id_m = re.search(r"Bug\s*ID\s*[:\-]?\s*([^\n]+)", output, re.IGNORECASE)
    sev_m = re.search(r"Severity\s*[:\-]?\s*(critical|major|minor)", output, re.IGNORECASE)
    steps_m = re.search(
        r"Steps\s*to\s*Reproduce\s*[:\-]?\s*([^\n]+(?:\n(?!(?:Expected|Actual|Severity|Bug\s*ID)\b)[^\n]+)*)",
        output,
        re.IGNORECASE,
    )
    exp_m = re.search(
        r"Expected\s*[:\-]?\s*([^\n]+(?:\n(?!(?:Steps|Actual|Severity|Bug\s*ID)\b)[^\n]+)*)",
        output,
        re.IGNORECASE,
    )
    act_m = re.search(
        r"Actual\s*[:\-]?\s*([^\n]+(?:\n(?!(?:Steps|Expected|Severity|Bug\s*ID)\b)[^\n]+)*)",
        output,
        re.IGNORECASE,
    )

    bug_id = bug_id_m.group(1).strip() if bug_id_m else f"BUG-{task.task_id}-{uuid.uuid4().hex[:6]}"
    severity = sev_m.group(1).lower() if sev_m else BugSeverity.MAJOR.value
    steps = (
        steps_m.group(1).strip()
        if steps_m
        else "Run verification tests against acceptance criteria"
    )
    expected = exp_m.group(1).strip() if exp_m else (task.acceptance_criteria or "Verification pass")
    actual = act_m.group(1).strip() if act_m else output[:500]

    bug_report = {
        "bug_id": bug_id,
        "severity": severity,
        "steps_to_reproduce": steps,
        "expected": expected,
        "actual": actual,
        "reported_by": "QA Tester",
        "status": BugStatus.OPEN.value,
    }
    return False, bug_report


def run_qa_check(
    task_id: str,
    *,
    project_dir: Path | None = None,
    retry_count: int = 0,
    qa_runner: Callable[[Task], str] | None = None,
    fix_runner: Callable[[Task, dict[str, Any]], str] | None = None,
) -> bool:
    """Run QA Tester agent against task's acceptance criteria with bug-fix loop."""
    from phase_engine import MAX_TEST_RETRIES

    task = get_task(task_id)
    if not task:
        logger.error("Task %s not found for QA check", task_id)
        return False

    # Check retry limit
    if retry_count >= MAX_TEST_RETRIES:
        logger.warning(
            "Task %s reached MAX_TEST_RETRIES (%d) — QA check aborted",
            task_id,
            MAX_TEST_RETRIES,
        )
        update_task_status(task_id, TaskStatus.BUG)
        notify_pm(task_id, f"bug (max retries {MAX_TEST_RETRIES} exceeded)")
        return False

    logger.info("Running QA check for task %s (attempt %d/%d)", task_id, retry_count + 1, MAX_TEST_RETRIES)

    # 1. Execute QA check
    if qa_runner is not None:
        raw_output = qa_runner(task)
    else:
        from crewai import Crew, Process, Task as CrewTask

        from agents import build_crew_llm, create_qa

        llm = build_crew_llm()
        qa_agent = create_qa(llm)
        crew_task = CrewTask(
            description=(
                f"Verify implementation of Task {task.task_id}: '{task.title}'\n"
                f"Role: {task.role}\n"
                f"Acceptance Criteria:\n{task.acceptance_criteria or task.title}\n\n"
                f"Implementation Output:\n{task.output or 'Implementation complete'}\n\n"
                "Instructions:\n"
                "1. Inspect changed code and execute verification tests via run_code_execution.\n"
                "2. If all acceptance criteria and tests pass, return 'PASS'.\n"
                "3. If any test or criteria fails, return 'FAIL' followed by:\n"
                "   Bug ID: <id>\n"
                "   Severity: <critical|major|minor>\n"
                "   Steps to Reproduce: <steps>\n"
                "   Expected: <expected behavior>\n"
                "   Actual: <actual behavior>\n"
            ),
            expected_output="PASS or FAIL with structured bug report.",
            agent=qa_agent,
        )
        crew = Crew(agents=[qa_agent], tasks=[crew_task], process=Process.sequential, verbose=True)
        kickoff_res = crew.kickoff()
        raw_output = str(getattr(getattr(crew_task, "output", None), "raw", None) or kickoff_res)

    passed, bug_report = parse_qa_result(raw_output, task)

    # 2. Handle PASS
    if passed:
        update_task_status(task_id, TaskStatus.DONE)
        notify_pm(task_id, "done")
        return True

    # 3. Handle FAIL
    assert bug_report is not None
    add_bug_report(task_id, bug_report)
    update_task_status(task_id, TaskStatus.BUG)

    logger.warning("Task %s failed QA check: %s — routing to engineer for fix", task_id, bug_report.get("bug_id"))

    # Route back to originating engineer
    is_frontend = "frontend" in (task.role or "").lower()

    if fix_runner is not None:
        fix_output = fix_runner(task, bug_report)
    else:
        from crewai import Crew, Process, Task as CrewTask

        from agents import build_crew_llm, create_backend, create_frontend

        llm = build_crew_llm()
        engineer = create_frontend(llm) if is_frontend else create_backend(llm)
        fix_task = CrewTask(
            description=(
                f"QA found a bug in Task {task.task_id}: '{task.title}'.\n\n"
                f"Bug Report:\n"
                f"- Bug ID: {bug_report.get('bug_id')}\n"
                f"- Severity: {bug_report.get('severity')}\n"
                f"- Steps to Reproduce: {bug_report.get('steps_to_reproduce')}\n"
                f"- Expected: {bug_report.get('expected')}\n"
                f"- Actual: {bug_report.get('actual')}\n\n"
                "Fix the bug, ensure all tests pass, and report the changes."
            ),
            expected_output="Fix report confirming resolution and test execution.",
            agent=engineer,
        )
        fix_crew = Crew(agents=[engineer], tasks=[fix_task], process=Process.sequential, verbose=True)
        fix_kickoff = fix_crew.kickoff()
        fix_output = str(getattr(getattr(fix_task, "output", None), "raw", None) or fix_kickoff)

    # Update task state with fix output and mark bug report as fixed
    updated_task = get_task(task_id)
    if updated_task:
        updated_task.output = fix_output
        if updated_task.bug_reports:
            updated_task.bug_reports[-1]["status"] = BugStatus.FIXED.value
        all_tasks = get_all_tasks()
        for idx, t in enumerate(all_tasks):
            if t.task_id == task_id:
                all_tasks[idx] = updated_task
                break
        from tasks import _save_tasks

        _save_tasks(all_tasks)

    update_task_status(task_id, TaskStatus.QA)

    # Re-invoke QA check
    return run_qa_check(
        task_id,
        project_dir=project_dir,
        retry_count=retry_count + 1,
        qa_runner=qa_runner,
        fix_runner=fix_runner,
    )

