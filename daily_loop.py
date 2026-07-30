"""Main CrewAI daily loop: Architect → Developer → Summary."""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from crewai import Crew, Process, Task
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents import build_crew_llm, create_architect, create_developer
from cost_tracker import CostTracker
from logger_util import log_agent_action
from notify import notify_approval_request, notify_daily_summary
from state import (
    append_log,
    clear_pending_approval,
    is_approval_granted,
    load_state,
    mark_completed,
    update_after_run,
)
from tasks import create_dev_task, create_plan_task, create_summary_task

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"agent_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"


def setup_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


logger = logging.getLogger("daily_loop")
NEEDS_APPROVAL_RE = re.compile(r"NEEDS_APPROVAL:\s*(.+)", re.IGNORECASE)


def extract_needs_approval(text: str) -> str | None:
    match = NEEDS_APPROVAL_RE.search(text or "")
    return match.group(1).strip() if match else None


def _task_raw(task: Task) -> str:
    output = getattr(task, "output", None)
    if output is None:
        return ""
    raw = getattr(output, "raw", None)
    if raw:
        return str(raw)
    return str(output)


def _record_crew_token_usage(tracker: CostTracker, result: object, agent: str) -> None:
    usage = getattr(result, "token_usage", None)
    if usage is None and isinstance(result, dict):
        usage = result.get("token_usage")
    if usage is None:
        return

    if isinstance(usage, dict):
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    else:
        inp = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0)
        out = int(
            getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0
        )

    if inp or out:
        tracker.log_usage(
            agent=agent,
            input_tokens=inp,
            output_tokens=out,
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        )


@retry(
    reraise=True,
    stop=stop_after_attempt(int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda rs: logger.warning(
        "Crew kickoff failed (attempt %s): %s — retrying…",
        rs.attempt_number,
        rs.outcome.exception() if rs.outcome else "unknown",
    ),
)
def _kickoff_with_retry(crew: Crew):
    return crew.kickoff()


def _env_skip_human() -> bool:
    return os.getenv("SKIP_HUMAN_INPUT", "").lower() in ("1", "true", "yes")


def run_daily_loop(*, skip_human_input: bool = False) -> dict:
    setup_logging()
    logger.info("=== Daily loop started ===")

    state = load_state()
    append_log(state, "Daily loop started")
    tracker = CostTracker()

    # NOTE: crewai.LLM (litellm-backed), NOT raw langchain ChatAnthropic.
    # Agent(llm=...) requires this type — passing ChatAnthropic here silently
    # breaks tool-calling / token usage reporting inside CrewAI.
    llm = build_crew_llm()
    architect = create_architect(llm)
    developer = create_developer(llm)

    # Resume path: previously paused for approval, now approved
    resume_plan = None
    if is_approval_granted() and state.get("current_plan"):
        resume_plan = state["current_plan"]
        append_log(state, "Resuming after approval with saved plan")
        log_agent_action("System", "resume_after_approval")

    if resume_plan:
        plan_text = resume_plan
    else:
        plan_task = create_plan_task(architect)
        if skip_human_input or _env_skip_human():
            plan_task.human_input = False
            logger.info("human_input disabled for plan_task")

        # --- 1) Architect: plan_task ---
        log_agent_action("Architect", "plan_task_start")
        plan_crew = Crew(
            agents=[architect],
            tasks=[plan_task],
            process=Process.sequential,
            verbose=True,
        )
        try:
            plan_result = _kickoff_with_retry(plan_crew)
        except Exception as exc:
            logger.exception("plan_task failed after retries")
            append_log(state, f"plan_task failed: {exc}", level="ERROR")
            update_after_run(state)
            raise

        plan_text = _task_raw(plan_task) or str(plan_result)
        _record_crew_token_usage(tracker, plan_result, "Architect")
        mark_completed(state, "plan_task", detail=plan_text[:500])
        append_log(state, "Architect completed plan_task")
        log_agent_action("Architect", "plan_task_done", detail=f"chars={len(plan_text)}")

        needs_approval = extract_needs_approval(plan_text)
        if needs_approval:
            logger.warning("NEEDS_APPROVAL: %s", needs_approval)
            update_after_run(state, plan=plan_text, needs_approval=needs_approval)
            notify_approval_request(needs_approval, plan_excerpt=plan_text)

            if not is_approval_granted():
                summary = (
                    "Daily run paused: architectural change awaits approval.\n"
                    f"Reason: {needs_approval}\n"
                    "Developer and summary tasks skipped until /approve."
                )
                cost_summary = tracker.summary()
                cost_line = (
                    f"Cost so far: ${cost_summary['total_cost_usd']:.6f} "
                    f"({cost_summary['total_input_tokens']} in / "
                    f"{cost_summary['total_output_tokens']} out)"
                )
                notify_daily_summary(summary, cost_line=cost_line)
                update_after_run(
                    state, plan=plan_text, summary=summary, cost_summary=cost_summary
                )
                logger.info("=== Daily loop paused for approval ===")
                return {
                    "status": "pending_approval",
                    "plan": plan_text,
                    "reason": needs_approval,
                    "cost": cost_summary,
                }

            append_log(state, "Pending approval granted — continuing to Developer")
            logger.info("Approval granted — continuing to Developer")

    try:
        from runner import stop_requested

        if stop_requested():
            append_log(state, "Stop requested after plan — aborting before developer")
            return {"status": "stopped", "plan": plan_text}
    except ImportError:
        pass

    # --- 2) Developer: dev_task (plan injected directly into description — see below) ---
    # --- 3) Architect: summary_task (context = dev_task only; plan injected via description) ---
    log_agent_action("Developer", "dev_task_start")

    # NOTE: plan_task itself is NOT re-run here, and its Task object's .output is not
    # populated in THIS Crew run — so we do not pass it as `context=[...]` (that was
    # fragile / version-dependent). Instead the plan text is injected directly into
    # dev_task's description below, which CrewAI always honors reliably.
    dev_task = create_dev_task(developer, None)
    dev_task.context = []  # plan is provided via description text only, not context
    dev_task.description = (
        f"{dev_task.description}\n\n--- Architect plan (approved for this run) ---\n{plan_text}"
    )

    summary_task = create_summary_task(architect, None, dev_task)
    summary_task.context = [dev_task]  # dev_task output IS real (runs in this same crew)
    summary_task.description = (
        f"{summary_task.description}\n\n--- Architect plan ---\n{plan_text}"
    )

    remaining = Crew(
        agents=[developer, architect],
        tasks=[dev_task, summary_task],
        process=Process.sequential,
        verbose=True,
    )

    try:
        final_result = _kickoff_with_retry(remaining)
    except Exception as exc:
        logger.exception("dev/summary tasks failed after retries")
        append_log(state, f"dev/summary failed: {exc}", level="ERROR")
        cost_summary = tracker.summary()
        update_after_run(state, plan=plan_text, cost_summary=cost_summary)
        raise

    _record_crew_token_usage(tracker, final_result, "Developer+Architect")

    dev_text = _task_raw(dev_task)
    summary_text = _task_raw(summary_task) or str(final_result)

    mark_completed(state, "dev_task", detail=(dev_text or "")[:500])
    mark_completed(state, "summary_task", detail=summary_text[:500])
    append_log(state, "Developer completed dev_task")
    append_log(state, "Architect completed summary_task")
    log_agent_action("Developer", "dev_task_done", detail=f"chars={len(dev_text)}")
    log_agent_action("Architect", "summary_task_done", detail=f"chars={len(summary_text)}")

    cost_summary = tracker.summary()
    cost_line = (
        f"Cost: ${cost_summary['total_cost_usd']:.6f} "
        f"({cost_summary['total_input_tokens']} in / "
        f"{cost_summary['total_output_tokens']} out tokens)"
    )
    notify_daily_summary(summary_text, cost_line=cost_line)
    clear_pending_approval(state)
    update_after_run(
        state,
        plan=plan_text,
        summary=summary_text,
        cost_summary=cost_summary,
    )

    logger.info("=== Daily loop completed === %s", cost_line)
    return {
        "status": "completed",
        "plan": plan_text,
        "dev": dev_text,
        "summary": summary_text,
        "cost": cost_summary,
    }


def main() -> None:
    skip = "--yes" in sys.argv or "-y" in sys.argv
    try:
        result = run_daily_loop(skip_human_input=skip)
        print("\n--- RESULT ---")
        print(f"status: {result.get('status')}")
        if result.get("summary"):
            print(result["summary"])
        print(f"cost_usd: {result.get('cost', {}).get('total_cost_usd')}")
    except Exception as exc:
        logger.exception("Daily loop aborted")
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
