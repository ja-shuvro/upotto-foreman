"""CrewAI task definitions: plan → develop → summarize."""

from __future__ import annotations

from crewai import Agent, Task

from config import REQUIRED_DOCS, get_docs_dir, get_project_dir, load_project_brief


def create_plan_task(architect: Agent) -> Task:
    project_dir = get_project_dir()
    docs_dir = get_docs_dir()
    docs_list = ", ".join(REQUIRED_DOCS)
    # Same loader as bootstrap — inject brief so Architect sees identical docs
    brief = load_project_brief(max_chars_per_file=8000)

    directive_block = ""
    try:
        from state import load_state

        directive = (load_state().get("user_directive") or "").strip()
        if directive:
            directive_block = (
                f"USER DIRECTIVE (priority): {directive}\n"
                "Incorporate this into today's plan as the highest priority. "
                "Scan + auto-discover only fills gaps around it.\n\n"
            )
    except Exception:  # noqa: BLE001
        pass

    return Task(
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


def create_dev_task(developer: Agent, plan_task: Task | None = None) -> Task:
    project_dir = get_project_dir()
    return Task(
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


def create_summary_task(architect: Agent, plan_task: Task | None, dev_task: Task) -> Task:
    return Task(
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
