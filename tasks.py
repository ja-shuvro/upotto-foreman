"""CrewAI task definitions: plan → develop → summarize."""

from __future__ import annotations

import os
from pathlib import Path

from crewai import Agent, Task

PROJECT_DIR = os.getenv(
    "PROJECT_DIR",
    str((Path(__file__).resolve().parent / "target_project")),
)


def create_plan_task(architect: Agent) -> Task:
    return Task(
        description=(
            f"You are planning today's engineering work for the project at: {PROJECT_DIR}\n\n"
            "Steps:\n"
            "1. Use DirectoryReadTool / FileReadTool to scan the project structure and key files.\n"
            "2. Use SerperDevTool to research current industry-standard approaches relevant "
            "to the next likely improvements.\n"
            "3. Decide the single best next task for today (scoped to one focused change).\n"
            "4. Produce a clear implementation plan: goals, files to touch, tests to add, "
            "acceptance criteria, and risks.\n"
            "5. If the change is large / architectural / breaking, include exactly:\n"
            "   NEEDS_APPROVAL: <reason>\n"
            "   Otherwise state: APPROVED_FOR_DEV (no human approval gate).\n\n"
            "Output a structured daily plan the Developer can execute."
        ),
        expected_output=(
            "A structured daily plan with: objective, rationale, file list, steps, "
            "tests, risks, and either NEEDS_APPROVAL: <reason> or APPROVED_FOR_DEV."
        ),
        agent=architect,
        human_input=True,
    )


def create_dev_task(developer: Agent, plan_task: Task | None = None) -> Task:
    return Task(
        description=(
            f"Implement today's plan for the project at: {PROJECT_DIR}\n\n"
            "Rules:\n"
            "- Follow the Architect plan exactly (from context).\n"
            "- If the plan contains NEEDS_APPROVAL and human approval was not given, "
            "do NOT implement; report BLOCKED_PENDING_APPROVAL.\n"
            "- Write / update code with FileWriterTool and FileReadTool.\n"
            "- Add or update tests.\n"
            "- Use the code_execution tool to run shell commands INSIDE the project dir, "
            "e.g. command='pytest -q', command='npm test', command='git add -A && git commit -m \"...\"'. "
            "Only python/pytest/npm/npx/pnpm/yarn/git/node/pip/ls/cat commands are allowed — "
            "do not attempt anything else, it will be blocked.\n"
            "- Create a git commit with a concise message (stage only relevant files; "
            "never commit secrets / .env).\n"
            "- Summarize what you changed, including the exact commands you ran and their output."
        ),
        expected_output=(
            "Implementation report: files changed, exact test command + result, git commit "
            "hash or message, or BLOCKED_PENDING_APPROVAL with reason."
        ),
        agent=developer,
        context=[plan_task] if plan_task is not None else [],
    )


def create_summary_task(architect: Agent, plan_task: Task | None, dev_task: Task) -> Task:
    return Task(
        description=(
            "Write a concise 5–10 line daily summary covering:\n"
            "- What was planned\n"
            "- What was implemented (or blocked)\n"
            "- Tests / commit status\n"
            "- Any remaining risks or next steps\n"
            "- Whether NEEDS_APPROVAL is still open\n\n"
            "Keep it suitable for WhatsApp / email notification."
        ),
        expected_output="A 5–10 line plain-text daily summary.",
        agent=architect,
        context=[t for t in (plan_task, dev_task) if t is not None],
    )
