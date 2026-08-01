"""Phase-driven execution engine — analyze → approve → branch → implement → test → merge."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from config import get_project_dir, validate_project_path
from phase_docs import (
    PhaseAnalysis,
    PhaseInfo,
    append_phase_completion_memory,
    apply_completion_to_phases,
    parse_memory_status,
    parse_phases_md,
    project_display_name,
    read_doc,
    update_memory_phase_status,
    working_tree_fingerprint,
)
from state import (
    append_log,
    clear_pending_approval,
    is_approval_granted,
    load_pending_approval,
    load_state,
    save_state,
    set_pending_approval,
)

logger = logging.getLogger(__name__)

MAX_TEST_RETRIES = int(os.getenv("PHASE_TEST_MAX_RETRIES", "5"))


def _emit(event_type: str, message: str, **extra: Any) -> None:
    try:
        from live_events import emit_phase, push_event

        if event_type == "phase_ui":
            emit_phase(extra.get("phase") or message, message)
        push_event({"type": event_type, "message": message, **extra})
    except Exception:  # noqa: BLE001
        pass


def detect_test_command(project_dir: Path) -> str:
    if (project_dir / "package.json").is_file():
        return "npm test -- --watchAll=false"
    if (project_dir / "pytest.ini").is_file() or (project_dir / "pyproject.toml").is_file():
        return "pytest -q"
    if (project_dir / "tests").is_dir() or (project_dir / "test").is_dir():
        return "pytest -q"
    # soft default
    if any(project_dir.glob("**/test_*.py")) or any(project_dir.glob("**/*_test.py")):
        return "pytest -q"
    return "pytest -q"


def run_tests(project_dir: Path, command: str | None = None) -> dict[str, Any]:
    from code_tool import LocalCodeExecutionTool

    cmd = command or detect_test_command(project_dir)
    _emit(
        "test_started",
        f"Running tests: {cmd}",
        tool="tests",
        meta={"command": cmd},
    )
    prev = os.environ.get("PROJECT_DIR")
    os.environ["PROJECT_DIR"] = str(project_dir)
    try:
        tool = LocalCodeExecutionTool()
        output = tool._run(command=cmd, timeout_seconds=300)
    finally:
        if prev is None:
            os.environ.pop("PROJECT_DIR", None)
        else:
            os.environ["PROJECT_DIR"] = prev

    # Parse exit_code from tool output
    m = re.search(r"exit_code=(\d+)", output)
    code = int(m.group(1)) if m else 1
    passed = code == 0
    _emit(
        "test_finished",
        "Tests passed" if passed else "Tests failed",
        tool="tests",
        meta={"exit_code": code, "passed": passed},
        error=None if passed else output[-800:],
    )
    # Stream lines for UI terminal panel
    for line in (output or "").splitlines()[-80:]:
        _emit(
            "test_line",
            line,
            meta={"passed": passed, "level": "ok" if passed else "err"},
        )
    return {"ok": passed, "exit_code": code, "output": output, "command": cmd}


def _sync_phase_state(state: dict[str, Any], analysis: PhaseAnalysis) -> None:
    state["phase_analysis"] = analysis.to_dict()
    state["phases_completed"] = [p.number for p in analysis.done]
    if analysis.next_phase:
        state["current_phase"] = analysis.next_phase.number
    state["last_analyzed_commit"] = analysis.last_analyzed_commit
    state["phase_total"] = len(analysis.phases)
    save_state(state)


def analyze_project(project_dir: Path | None = None, *, force: bool = False) -> PhaseAnalysis:
    """Case A/B analysis with Memory cache + commit/fingerprint skip."""
    import git_safe

    root = Path(validate_project_path(project_dir or get_project_dir()))
    name = project_display_name(root)
    phases_text = read_doc("Phases.md", project_dir=root)
    memory_text = read_doc("Memory.md", project_dir=root)
    phases = parse_phases_md(phases_text)
    mem = parse_memory_status(memory_text)
    completed = list(mem.get("phases_completed") or [])
    state = load_state()
    completed = list(state.get("phases_completed") or completed)

    head = git_safe.head_commit(root) if git_safe.is_git_repo(root) else ""
    fingerprint = working_tree_fingerprint(root)
    cache_token = head or fingerprint
    last = (state.get("last_analyzed_commit") or mem.get("last_analyzed_commit") or "").strip()

    emptyish = not any(
        p.is_file()
        for p in root.rglob("*")
        if p.is_file()
        and "docs" not in p.relative_to(root).parts
        and ".git" not in p.relative_to(root).parts
        and p.name != ".DS_Store"
    )

    if not force and last and last == cache_token and state.get("phase_analysis"):
        cached = state["phase_analysis"]
        analysis = PhaseAnalysis(
            project_name=name,
            phases=[PhaseInfo(**p) for p in cached.get("phases") or []],
            done=[PhaseInfo(**p) for p in cached.get("done") or []],
            remaining=[PhaseInfo(**p) for p in cached.get("remaining") or []],
            next_phase=PhaseInfo(**cached["next_phase"]) if cached.get("next_phase") else None,
            last_analyzed_commit=cache_token,
            cached=True,
            case=cached.get("case") or ("empty" if emptyish else "existing"),
            summary=cached.get("summary") or "Loaded cached phase analysis",
        )
        return analysis

    if not phases:
        # synthesize a single phase so engine can still run
        phases = [
            PhaseInfo(
                number=1,
                title="Foundation",
                body="Implement the first milestone described in PRD.md / Architecture.md.",
            )
        ]

    phases = apply_completion_to_phases(
        phases,
        completed,
        current=(completed[-1] + 1) if completed else phases[0].number,
    )
    done = [p for p in phases if p.status == "done"]
    remaining = [p for p in phases if p.status != "done"]
    nxt = remaining[0] if remaining else None
    if nxt:
        for p in phases:
            if p.number == nxt.number:
                p.status = "current"

    analysis = PhaseAnalysis(
        project_name=name,
        phases=phases,
        done=done,
        remaining=remaining,
        next_phase=nxt,
        last_analyzed_commit=cache_token,
        cached=False,
        case="empty" if emptyish else "existing",
        summary=(
            f"Analyzed {name}: {len(done)} done, {len(remaining)} remaining. "
            f"Next: Phase {nxt.number}: {nxt.title}" if nxt else f"{name}: all phases done."
        ),
    )

    update_memory_phase_status(
        project_dir=root,
        last_analyzed_commit=cache_token,
        phases_completed=[p.number for p in done],
        current_phase=nxt.number if nxt else None,
        note=f"Phase analysis refresh (cached={analysis.cached}).\n{analysis.summary}",
    )
    _sync_phase_state(load_state(), analysis)
    return analysis


def format_phase_report(analysis: PhaseAnalysis) -> str:
    done_s = ", ".join(f"Phase {p.number}: {p.title}" for p in analysis.done) or "none"
    rem_s = ", ".join(f"Phase {p.number}: {p.title}" for p in analysis.remaining) or "none"
    nxt = analysis.next_phase
    lines = [
        f"Project: {analysis.project_name}",
        f"Phases done: {len(analysis.done)} — [{done_s}]",
        f"Phases remaining: {len(analysis.remaining)} — [{rem_s}]",
    ]
    if analysis.cached:
        lines.append("(using cached analysis — no code change detected)")
    if nxt:
        lines.append(f"Proceed with Phase {nxt.number}: {nxt.title}?")
        lines.append("Reply YES to approve, NO to reject, or PAUSE to wait.")
    else:
        lines.append("All phases complete. Nothing to run.")
    return "\n".join(lines)


def request_phase_approval(
    analysis: PhaseAnalysis,
    *,
    kind: str = "phase_start",
    extra_plan: str = "",
) -> dict[str, Any]:
    state = load_state()
    nxt = analysis.next_phase
    if not nxt:
        state["phase_status"] = "done"
        save_state(state)
        return {"status": "done", "summary": "All phases complete"}

    reason = f"PHASE:{nxt.number}:{kind}:{nxt.title}"
    plan = format_phase_report(analysis)
    if extra_plan:
        plan = plan + "\n\n" + extra_plan
    set_pending_approval(state, reason, plan=plan)
    pending = load_pending_approval() or {}
    pending["kind"] = kind
    pending["phase"] = nxt.number
    pending["phase_title"] = nxt.title
    pending["approval_options"] = ["YES", "NO", "PAUSE"]
    from state import save_pending_approval

    save_pending_approval(pending)
    state["pending_approval"] = pending
    state["phase_status"] = "awaiting_approval"
    state["current_phase"] = nxt.number
    state["phase_branch"] = None
    save_state(state)

    try:
        from notify import notify_phase_approval

        notify_phase_approval(plan, phase=nxt.number, title=nxt.title, kind=kind)
    except Exception:  # noqa: BLE001
        from notify import send_telegram

        send_telegram(plan, parse_mode=None)

    _emit("phase_ui", f"Awaiting approval for Phase {nxt.number}", phase="Awaiting Approval")
    _emit(
        "phase_progress",
        f"Phase {nxt.number} awaiting approval",
        meta={
            "current": nxt.number,
            "total": len(analysis.phases),
            "phases": [p.__dict__ for p in analysis.phases],
            "status": "awaiting_approval",
        },
    )
    return {
        "status": "pending_approval",
        "reason": reason,
        "phase": nxt.number,
        "summary": plan,
        "analysis": analysis.to_dict(),
    }


def _extract_bullets(text: str, limit: int = 8) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(("-", "*", "•")):
            items.append(s.lstrip("-*• ").strip())
        if len(items) >= limit:
            break
    if not items and text:
        items = [text.strip()[:200]]
    return items


def implement_phase(phase: PhaseInfo, *, project_dir: Path) -> dict[str, Any]:
    """Run Developer crew focused on a single phase."""
    from crewai import Crew, Process

    from agents import build_crew_llm, create_developer
    from config import load_project_brief
    from crewai import Task

    llm = build_crew_llm()
    developer = create_developer(llm)
    brief = load_project_brief(max_chars_per_file=6000, project_dir=project_dir)

    directive = ""
    state = load_state()
    if (state.get("user_directive") or "").strip():
        directive = f"USER DIRECTIVE (priority): {state['user_directive']}\n\n"

    task = Task(
        description=(
            f"{directive}"
            f"Implement PHASE {phase.number}: {phase.title} for project at {project_dir}.\n\n"
            f"Phase requirements:\n{phase.body or phase.title}\n\n"
            f"{brief}\n\n"
            "Rules:\n"
            "- Follow docs/Rules.md and Architecture.md.\n"
            "- Write code + tests for this phase only.\n"
            "- Use code_execution for tests (pytest/npm). Do NOT merge to main.\n"
            "- Do NOT checkout main or push to main.\n"
            "- Stay on the current phase branch.\n"
            "- Summarize: implemented bullets, deferred bullets, tests run.\n"
        ),
        expected_output=(
            "Implementation report with Implemented:/Deferred:/Tests: sections and file list."
        ),
        agent=developer,
    )
    _emit("phase_ui", f"Developing Phase {phase.number}", phase="Developing")
    crew = Crew(agents=[developer], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()
    raw = str(getattr(getattr(task, "output", None), "raw", None) or result)
    return {
        "report": raw,
        "implemented": _extract_bullets(raw),
        "deferred": [],
    }


def execute_approved_phase(analysis: PhaseAnalysis | None = None) -> dict[str, Any]:
    """Branch → implement → test loop → commit → merge to dev → memory → next approval."""
    import git_safe
    from path_guard import assert_project_dir_consistent

    root = assert_project_dir_consistent()
    root = Path(validate_project_path(root))
    _emit("info", f"Executing phase inside PROJECT_DIR={root}")
    state = load_state()
    analysis = analysis or analyze_project(root, force=False)
    phase = analysis.next_phase
    if not phase:
        state["phase_status"] = "done"
        save_state(state)
        return {"status": "done", "summary": "All phases complete"}

    # Clear the start approval so we don't loop
    clear_pending_approval(state)
    state = load_state()
    state["phase_status"] = "in_progress"
    state["current_phase"] = phase.number
    state["test_retry_count"] = 0
    save_state(state)

    git_safe.ensure_main_and_dev(root)
    branch = git_safe.create_phase_branch(phase.number, phase.slug(), project_dir=root)
    state["phase_branch"] = branch
    save_state(state)
    _emit(
        "branch_event",
        f"Created/checked out {branch}",
        meta={"branch": branch, "action": "create", "from": "dev"},
    )

    try:
        impl = implement_phase(phase, project_dir=root)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Phase implementation failed")
        state = load_state()
        state["phase_status"] = "blocked"
        state["last_error"] = str(exc)
        append_log(state, f"Phase {phase.number} blocked: {exc}", level="ERROR")
        save_state(state)
        try:
            from notify import send_telegram

            send_telegram(f"Phase {phase.number} BLOCKED during implement: {exc}", parse_mode=None)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "blocked", "error": str(exc), "phase": phase.number}

    # Test loop
    test_cmd = detect_test_command(root)
    last_test: dict[str, Any] = {}
    for attempt in range(1, MAX_TEST_RETRIES + 1):
        state = load_state()
        state["test_retry_count"] = attempt
        save_state(state)
        last_test = run_tests(root, test_cmd)
        if last_test["ok"]:
            break
        if attempt >= MAX_TEST_RETRIES:
            state = load_state()
            state["phase_status"] = "blocked"
            append_log(
                state,
                f"Phase {phase.number} BLOCKED — tests failed after {attempt} attempts",
                level="ERROR",
            )
            save_state(state)
            try:
                from notify import send_telegram

                send_telegram(
                    f"Phase {phase.number} BLOCKED — tests failed after {attempt} attempts.\n"
                    f"Command: {test_cmd}\n\n{(last_test.get('output') or '')[-1500:]}",
                    parse_mode=None,
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "status": "blocked",
                "phase": phase.number,
                "tests": last_test,
                "summary": f"Phase {phase.number} blocked on tests",
            }
        # Fix attempt via short developer pass
        _emit("phase_ui", f"Fixing failing tests (attempt {attempt})", phase="Developing")
        try:
            from crewai import Crew, Process, Task

            from agents import build_crew_llm, create_developer

            developer = create_developer(build_crew_llm())
            fix = Task(
                description=(
                    f"Tests failed for Phase {phase.number}. Fix the code so tests pass.\n"
                    f"Test command: {test_cmd}\n"
                    f"Failure output:\n{(last_test.get('output') or '')[-4000:]}\n"
                    "Do not touch main. Stay on current branch. Re-run tests via code_execution."
                ),
                expected_output="Fix report and confirmation tests were re-run.",
                agent=developer,
            )
            Crew(agents=[developer], tasks=[fix], process=Process.sequential, verbose=True).kickoff()
        except Exception:  # noqa: BLE001
            logger.exception("Test-fix pass failed")

    commit = git_safe.commit_all(
        f"feat(phase-{phase.number}): {phase.title}",
        project_dir=root,
    )
    _emit("branch_event", f"Committed on {branch}", meta={"branch": branch, "action": "commit", "commit": commit})

    try:
        git_safe.push_branch(branch, project_dir=root)
    except Exception:  # noqa: BLE001
        logger.warning("Push phase branch failed (continuing)")

    merge = git_safe.merge_phase_into_dev(branch, project_dir=root)
    _emit(
        "branch_event",
        f"Merge {branch} → dev ({'ok' if merge.get('ok') else 'FAILED'})",
        meta={"branch": branch, "action": "merge", "ok": merge.get("ok"), "into": "dev"},
    )
    if not merge.get("ok"):
        state = load_state()
        state["phase_status"] = "blocked"
        append_log(state, f"Merge into dev failed for {branch}", level="ERROR")
        save_state(state)
        return {"status": "blocked", "phase": phase.number, "merge": merge}

    # Re-test on dev
    git_safe.run_git("checkout", "dev", project_dir=root)
    post = run_tests(root, test_cmd)
    if not post["ok"]:
        # one fix pass on dev
        try:
            from crewai import Crew, Process, Task

            from agents import build_crew_llm, create_developer

            developer = create_developer(build_crew_llm())
            fix = Task(
                description=(
                    f"After merging phase {phase.number} into dev, tests failed. "
                    f"Fix on dev (NOT main).\n{(post.get('output') or '')[-4000:]}"
                ),
                expected_output="Fix report.",
                agent=developer,
            )
            Crew(agents=[developer], tasks=[fix], process=Process.sequential, verbose=True).kickoff()
            git_safe.commit_all(f"fix(dev): after phase-{phase.number} merge", project_dir=root)
            post = run_tests(root, test_cmd)
        except Exception:  # noqa: BLE001
            logger.exception("Post-merge fix failed")

    if not post["ok"]:
        state = load_state()
        state["phase_status"] = "blocked"
        save_state(state)
        try:
            from notify import send_telegram

            send_telegram(
                f"Phase {phase.number} merged but DEV tests still failing — marked BLOCKED.",
                parse_mode=None,
            )
        except Exception:  # noqa: BLE001
            pass
        return {"status": "blocked", "phase": phase.number, "tests": post}

    implemented = impl.get("implemented") or []
    deferred = impl.get("deferred") or []
    tests_line = f"passed ({post.get('command')})"
    append_phase_completion_memory(
        phase,
        project_dir=root,
        branch=branch,
        commit=commit,
        implemented=implemented,
        deferred=deferred,
        tests=tests_line,
        merged=True,
    )

    completed = list(load_state().get("phases_completed") or [])
    if phase.number not in completed:
        completed.append(phase.number)
    completed = sorted(set(completed))
    state = load_state()
    state["phases_completed"] = completed
    state["phase_status"] = "awaiting_approval"
    state["phase_branch"] = branch
    state["last_analyzed_commit"] = git_safe.head_commit(root) or working_tree_fingerprint(root)
    # clear directive after successful phase
    if state.get("user_directive"):
        state["user_directive"] = None
    save_state(state)

    # Refresh analysis for next phase
    next_analysis = analyze_project(root, force=True)
    complete_msg = (
        f"✅ Phase {phase.number}: {phase.title} — COMPLETE\n"
        f"Branch: {branch} → merged into dev\n"
        f"Implemented:\n" + "\n".join(f"- {x}" for x in implemented[:8]) + "\n"
        f"Not implemented / deferred:\n"
        + ("\n".join(f"- {x}" for x in deferred[:8]) if deferred else "- none")
        + f"\nTests: {tests_line}\n"
    )
    if next_analysis.next_phase:
        complete_msg += (
            f"Next: Phase {next_analysis.next_phase.number}: {next_analysis.next_phase.title}\n"
            "Approve to continue? Reply YES / NO / PAUSE"
        )
        return request_phase_approval(
            next_analysis,
            kind="phase_next",
            extra_plan=complete_msg,
        )

    complete_msg += "All phases complete. main was not touched."
    try:
        from notify import send_telegram

        send_telegram(complete_msg, parse_mode=None)
    except Exception:  # noqa: BLE001
        pass
    state = load_state()
    state["phase_status"] = "done"
    clear_pending_approval(state)
    save_state(state)
    _emit("phase_ui", "All phases done", phase="Idle")
    return {"status": "done", "summary": complete_msg, "phase": phase.number}


def run_phase_zero_setup(project_dir: Path) -> dict[str, Any]:
    """Phase 0: git main/dev + baseline scaffold inside the ACTIVE project only."""
    import git_safe
    from path_guard import assert_project_dir_consistent, guard_write_target

    root = assert_project_dir_consistent()
    if root.resolve() != Path(project_dir).resolve():
        raise RuntimeError(f"Phase 0 path mismatch {root} vs {project_dir}")

    _emit("phase_ui", "Phase 0: Project Setup", phase="Planning")
    info = git_safe.ensure_main_and_dev(root)
    branch = git_safe.create_phase_branch(0, "project-setup", project_dir=root)

    # Minimal baseline files (never write outside root)
    gitignore = (
        ".venv/\nvenv/\n__pycache__/\nnode_modules/\n.env\n"
        "dist/\nbuild/\n*.log\n.DS_Store\n"
    )
    readme = (
        f"# {root.name}\n\n"
        "Initialized by Upotto Foreman — Phase 0: Project Setup.\n"
    )
    files = {
        ".gitignore": gitignore,
        "README.md": readme,
    }
    # Prefer Python package stub if PRD mentions python; otherwise keep docs-only + readme
    prd = (root / "docs" / "PRD.md").read_text(encoding="utf-8", errors="replace") if (root / "docs" / "PRD.md").is_file() else ""
    low = prd.lower()
    if "node" in low or "typescript" in low or "react" in low:
        files["package.json"] = (
            '{\n  "name": "'
            + root.name
            + '",\n  "private": true,\n  "version": "0.0.1",\n  "scripts": { "test": "echo \\"no tests yet\\" && exit 0" }\n}\n'
        )
    else:
        pkg = root.name.replace("-", "_")
        files[f"{pkg}/__init__.py"] = '"""Package root."""\n__version__ = "0.0.1"\n'
        files["tests/test_smoke.py"] = (
            f"from {pkg} import __version__\n\n\ndef test_version():\n    assert __version__\n"
        )

    for rel, content in files.items():
        path = root / rel
        guard_write_target(path, project_dir=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    commit = git_safe.commit_all("Phase 0: initial project setup", project_dir=root)
    merge = git_safe.merge_phase_into_dev(branch, project_dir=root)
    git_safe.run_git("checkout", "dev", project_dir=root)

    from phase_docs import PhaseInfo, append_phase_completion_memory

    phase0 = PhaseInfo(number=0, title="Project Setup", status="done")
    append_phase_completion_memory(
        phase0,
        project_dir=root,
        branch=branch,
        commit=commit,
        implemented=list(files.keys()) + ["git main+dev"],
        deferred=[],
        tests="n/a (scaffold)",
        merged=bool(merge.get("ok")),
    )
    state = load_state()
    completed = list(state.get("phases_completed") or [])
    if 0 not in completed:
        completed.append(0)
    state["phases_completed"] = sorted(set(completed))
    state["phase_branch"] = branch
    save_state(state)
    return {"ok": True, "branch": branch, "commit": commit, "created_repo": info.get("created_repo")}


def run_phase_engine(*, skip_human_input: bool = True) -> dict[str, Any]:
    """Entry from daily_loop — Case A/B + approval gates + execute."""
    import git_safe
    from bootstrap import BootstrapMode, is_project_empty, resolve_bootstrap_mode
    from path_guard import assert_project_dir_consistent

    root = assert_project_dir_consistent()
    root = Path(validate_project_path(root))
    state = load_state()
    append_log(state, f"Phase engine started — PROJECT_DIR={root}")
    save_state(state)
    _emit("phase_ui", "Planning phases", phase="Planning")
    _emit("info", f"Active project path: {root}")

    mode = resolve_bootstrap_mode(root)

    if mode == BootstrapMode.DOCS_MISSING:
        return {"status": "needs_docs", "summary": "Docs missing — run bootstrap first"}
    if mode == BootstrapMode.INVALID:
        return {"status": "error", "summary": "Invalid project path"}

    needs_phase0 = (mode == BootstrapMode.EMPTY or is_project_empty(root)) and not git_safe.is_git_repo(root)
    # Also Phase 0 if empty-ish code but no git
    if (mode == BootstrapMode.EMPTY or is_project_empty(root)) and 0 not in (
        state.get("phases_completed") or []
    ):
        needs_phase0 = True

    if needs_phase0:
        analysis = analyze_project(root, force=True)
        # Inject Phase 0 as next if not completed
        phase0 = PhaseInfo(
            number=0,
            title="Project Setup",
            body=(
                "Initialize git (main + dev), create baseline README/.gitignore "
                "and minimal package/test scaffold from PRD hints."
            ),
            status="current",
        )
        analysis.phases = [phase0] + [p for p in analysis.phases if p.number != 0]
        analysis.next_phase = phase0
        analysis.case = "empty"
        analysis.remaining = [phase0] + analysis.remaining
        pending = load_pending_approval()
        if is_approval_granted() and pending and str(pending.get("kind", "")).startswith("phase"):
            # Execute Phase 0 specially then continue
            clear_pending_approval(load_state())
            try:
                result0 = run_phase_zero_setup(root)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Phase 0 failed")
                return {"status": "blocked", "error": str(exc), "phase": 0}
            # After Phase 0, request approval for Phase 1
            next_analysis = analyze_project(root, force=True)
            msg = (
                f"✅ Phase 0: Project Setup — COMPLETE\n"
                f"Repo ready at {root}\n"
                f"Branch merged into dev. Commit: {result0.get('commit')}\n"
            )
            if next_analysis.next_phase and next_analysis.next_phase.number != 0:
                msg += (
                    f"Next: Phase {next_analysis.next_phase.number}: "
                    f"{next_analysis.next_phase.title}\nReply YES / NO / PAUSE"
                )
                return request_phase_approval(next_analysis, kind="phase_next", extra_plan=msg)
            return {"status": "done", "summary": msg, "phase": 0}

        msg = (
            f"New project: {analysis.project_name}\n"
            f"Path: {root}\n"
            "Starting Phase 0: Project Setup (git + baseline scaffold). Proceed?\n"
            "Reply YES / NO / PAUSE"
        )
        return request_phase_approval(analysis, kind="phase_start", extra_plan=msg)

    # Case B with git already: normal phase 1+
    if mode == BootstrapMode.EMPTY or is_project_empty(root):
        git_safe.ensure_main_and_dev(root)
        analysis = analyze_project(root, force=True)
        analysis.case = "empty"
        pending = load_pending_approval()
        if is_approval_granted() and pending and str(pending.get("kind", "")).startswith("phase"):
            return execute_approved_phase(analysis)
        nxt = analysis.next_phase
        msg = (
            f"New project: {analysis.project_name}\n"
            f"Path: {root}\n"
            f"Starting Phase {nxt.number if nxt else 1}: {nxt.title if nxt else 'Foundation'}. Proceed?\n"
            "Reply YES / NO / PAUSE"
        )
        return request_phase_approval(analysis, kind="phase_start", extra_plan=msg)

    # Case A — existing project
    analysis = analyze_project(root, force=False)
    _emit(
        "phase_progress",
        analysis.summary,
        meta={
            "current": analysis.next_phase.number if analysis.next_phase else 0,
            "total": len(analysis.phases),
            "phases": [p.__dict__ for p in analysis.phases],
            "status": state.get("phase_status") or "idle",
        },
    )

    pending = load_pending_approval()
    kind = (pending or {}).get("kind") or ""

    if is_approval_granted() and kind in {"phase_start", "phase_next", "phase"}:
        append_log(load_state(), f"Phase approval granted ({kind}) — executing")
        # If approving phase 0 specifically
        if (pending or {}).get("phase") == 0:
            clear_pending_approval(load_state())
            result0 = run_phase_zero_setup(root)
            next_analysis = analyze_project(root, force=True)
            msg = f"✅ Phase 0 complete at {root}\nCommit: {result0.get('commit')}\n"
            if next_analysis.next_phase:
                return request_phase_approval(next_analysis, kind="phase_next", extra_plan=msg)
            return {"status": "done", "summary": msg, "phase": 0}
        return execute_approved_phase(analysis)

    return request_phase_approval(analysis, kind="phase_start")
