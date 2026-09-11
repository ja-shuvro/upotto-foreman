"""Architect and Developer CrewAI agent definitions (Claude via crewai.LLM)."""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable

from crewai import Agent, LLM
from crewai.tools import BaseTool as CrewBaseTool
from langchain_community.tools import DuckDuckGoSearchRun  # free, no API key needed
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from code_tool import build_code_execution_tools
from config import IGNORED_DIRS, get_project_dir
from cost_tracker import CostTracker
from project_tools import ProjectFileReadTool, ProjectFileWriterTool

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")


def _project_dir_str() -> str:
    """Fresh PROJECT_DIR every call — never a cached module constant."""
    return str(get_project_dir())


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _bare_model_name(raw: str) -> str:
    """Strip any provider prefix the user may have typed (anthropic/, openrouter/, gemini/)."""
    for prefix in ("openrouter/anthropic/", "openrouter/", "anthropic/", "gemini/"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def has_valid_key(provider: str) -> bool:
    """Return True if the required API key for the given provider is present in environment."""
    p = provider.strip().lower()
    if p == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    if p == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    if p == "agentrouter":
        return bool(os.getenv("AGENTROUTER_API_KEY", "").strip())
    if p == "gemini":
        return bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip())
    return False


def build_crew_llm(
    provider: str | None = None,
    *,
    temperature: float = 0.2,
    disable_failover: bool = False,
) -> LLM:
    """crewai.LLM (litellm-backed) — THIS is what Agent(llm=...) must receive.

    Supports direct Anthropic API, OpenRouter, AgentRouter, or Gemini.
    Set LLM_PROVIDER=gemini in .env to route through Gemini.
    If LLM_FAILOVER_ENABLED=true in .env, delegates to build_crew_llm_with_failover().
    """
    if provider is None and not disable_failover:
        if os.getenv("LLM_FAILOVER_ENABLED", "false").strip().lower() in ("true", "1", "yes"):
            from llm_failover import build_crew_llm_with_failover
            return build_crew_llm_with_failover(temperature=temperature)

    active_provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).strip().lower()
    bare_model = _bare_model_name(os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip())

    if active_provider == "agentrouter":
        # AgentRouter is an Anthropic-Messages-API-compatible relay — same wire
        # format as api.anthropic.com, just a different base_url + key.
        api_key = _require_env("AGENTROUTER_API_KEY")
        return LLM(
            model=f"anthropic/{bare_model}",
            api_key=api_key,
            base_url=os.getenv("AGENTROUTER_BASE_URL", "https://agentrouter.org"),
            temperature=temperature,
            max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
        )

    if active_provider == "openrouter":
        api_key = _require_env("OPENROUTER_API_KEY")
        # litellm reads this env var itself for the native "openrouter/" provider path —
        # setting it explicitly avoids edge cases where only api_key= isn't picked up.
        os.environ.setdefault("OPENROUTER_API_KEY", api_key)
        return LLM(
            model=f"openrouter/{bare_model}",
            api_key=api_key,
            temperature=temperature,
            max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
        )

    if active_provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            api_key = _require_env("GEMINI_API_KEY")
        os.environ.setdefault("GEMINI_API_KEY", api_key)
        os.environ.setdefault("GOOGLE_API_KEY", api_key)
        model_name = os.getenv("GEMINI_MODEL") or (bare_model if "gemini" in bare_model else "gemini-2.0-flash")
        model_name = _bare_model_name(model_name)
        return LLM(
            model=f"gemini/{model_name}",
            provider="litellm",
            api_key=api_key,
            temperature=temperature,
            max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
        )

    return LLM(
        model=f"anthropic/{bare_model}",
        api_key=_require_env("ANTHROPIC_API_KEY"),
        temperature=temperature,
        max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
    )


def build_chat_anthropic(*, temperature: float = 0.2) -> ChatAnthropic:
    """Standalone LangChain client — NOT for Agent(llm=...). Use only if you need
    a direct LangChain call somewhere (e.g. a side script), with CostTracker via
    invoke_with_tracking(). CrewAI agents must use build_crew_llm() instead."""
    return ChatAnthropic(
        model=DEFAULT_MODEL,
        api_key=_require_env("ANTHROPIC_API_KEY"),
        temperature=temperature,
        max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "8192")),
    )


def with_llm_retry(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Retry wrapper for LLM / crew kicks on transient failures."""

    @wraps(fn)
    @retry(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda rs: logger.warning(
            "LLM call failed (attempt %s): %s — retrying…",
            rs.attempt_number,
            rs.outcome.exception() if rs.outcome else "unknown",
        ),
    )
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


@with_llm_retry
def invoke_with_tracking(
    llm: ChatAnthropic,
    messages: list[Any],
    *,
    tracker: CostTracker,
    agent_name: str,
) -> Any:
    """Invoke LangChain ChatAnthropic directly and log token usage into CostTracker.
    Only relevant if you call ChatAnthropic outside of CrewAI's own execution."""
    response = llm.invoke(messages)
    tracker.extract_and_log(response, agent=agent_name, model=DEFAULT_MODEL)
    return response


_IGNORED_DIRS = IGNORED_DIRS


class ProjectDirectoryScanTool(CrewBaseTool):
    """List project files, skipping .git/__pycache__/venv noise that burns context
    and starves the LLM's output token budget (was causing empty LLM responses)."""

    name: str = "list_project_files"
    description: str = (
        "List all real project files under the project directory, excluding "
        ".git, __pycache__, node_modules, and venv folders. Input: ignored, "
        "always scans the configured project directory."
    )

    def _run(self, *_args: Any, **_kwargs: Any) -> str:
        project_dir = _project_dir_str()
        paths: list[str] = []
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
            for f in files:
                paths.append(os.path.join(root, f))
        if not paths:
            return f"No files found under {project_dir}."
        return "File paths:\n" + "\n".join(f"- {p}" for p in paths)


class DuckDuckGoSearchTool(CrewBaseTool):
    """crewai-native wrapper around langchain's free DuckDuckGoSearchRun."""

    name: str = "web_search"
    description: str = (
        "Search the web (DuckDuckGo, free, no API key) for current industry-standard "
        "practices, docs, or news relevant to the project. Input: a search query string."
    )

    def _run(self, query: str) -> str:
        return DuckDuckGoSearchRun().run(query)


def create_architect(llm: Any | None = None) -> Agent:
    directory_tool = ProjectDirectoryScanTool()
    file_read_tool = ProjectFileReadTool()
    search_tool = DuckDuckGoSearchTool()  # free — no API key, no cost

    project = _project_dir_str()
    return Agent(
        role="Senior Software Architect",
        goal=(
            "Scan the project, research industry standards, decide the next highest-value "
            "task, and flag large architectural changes with NEEDS_APPROVAL."
        ),
        backstory=(
            "You are a Senior Software Architect with 15+ years of experience designing "
            "production systems. You favor incremental, industry-standard changes, clear "
            "rationale, and risk-aware planning. When a change is large, irreversible, or "
            "cross-cutting, you MUST include a line exactly like: "
            "NEEDS_APPROVAL: <reason>. "
            f"The ONLY project directory you may inspect is: {project}"
        ),
        tools=[directory_tool, file_read_tool, search_tool],
        llm=llm or build_crew_llm(temperature=0.2),
        verbose=True,
        allow_delegation=False,
        max_iter=15,
    )


class ScopedFileWriterTool(ProjectFileWriterTool):
    """Same as ProjectFileWriterTool but rejects writes outside `allowed_prefixes`."""

    allowed_prefixes: tuple[str, ...] = ()

    def _run(self, filename: str, content: str, directory: str | None = "./", overwrite: str | bool = True) -> str:
        rel = os.path.normpath(os.path.join(directory or "./", filename)).replace("\\", "/")
        if self.allowed_prefixes and not any(rel.startswith(p) for p in self.allowed_prefixes):
            return f"ERROR: path '{rel}' outside allowed scope {self.allowed_prefixes}"
        return super()._run(filename=filename, content=content, directory=directory, overwrite=overwrite)


def create_pm(llm: Any | None = None) -> Agent:
    directory_tool = ProjectDirectoryScanTool()
    file_read_tool = ProjectFileReadTool()
    project = _project_dir_str()
    return Agent(
        role="Project Manager",
        goal=(
            "Take the Architect's approved architecture and break it into ordered phases, "
            "then phases into small backend/frontend tasks with clear dependencies."
        ),
        backstory=(
            "You are a Senior Technical Project Manager. You never write code. You output "
            "task lists as: task_id, role (backend|frontend), title, depends_on, acceptance "
            "criteria. You resolve task ordering so no engineer is ever blocked on an "
            "unfinished dependency. "
            f"The ONLY project directory you may inspect is: {project}"
        ),
        tools=[directory_tool, file_read_tool],
        llm=llm or build_crew_llm(temperature=0.2),
        verbose=True,
        allow_delegation=False,
        max_iter=15,
    )


def create_backend(llm: Any | None = None) -> Agent:
    file_read_tool = ProjectFileReadTool()
    file_writer_tool = ScopedFileWriterTool(allowed_prefixes=("backend/", "server/", "api/"))
    code_tools = build_code_execution_tools()
    project = _project_dir_str()
    return Agent(
        role="Senior Backend Engineer",
        goal="Complete the assigned backend task from the PM, then hand off to QA.",
        backstory=(
            "You implement one backend task at a time, write tests, and stop. You never "
            "touch frontend files. If QA sends a bug report, you fix only that bug and "
            "return it to QA — you do not start the next task until QA passes this one. "
            f"CRITICAL: Write ALL files only under {project}, backend/server/api paths only."
        ),
        tools=[file_read_tool, file_writer_tool, *code_tools],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=25,
    )


def create_frontend(llm: Any | None = None) -> Agent:
    file_read_tool = ProjectFileReadTool()
    file_writer_tool = ScopedFileWriterTool(allowed_prefixes=("frontend/", "client/", "web/", "src/"))
    code_tools = build_code_execution_tools()
    project = _project_dir_str()
    return Agent(
        role="Senior Frontend Engineer",
        goal="Complete the assigned frontend task from the PM, then hand off to QA.",
        backstory=(
            "You implement one frontend task at a time, write tests, and stop. You never "
            "touch backend files. If QA sends a bug report, you fix only that bug and "
            "return it to QA — you do not start the next task until QA passes this one. "
            f"CRITICAL: Write ALL files only under {project}, frontend/client/web/src paths only."
        ),
        tools=[file_read_tool, file_writer_tool, *code_tools],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=25,
    )


def create_qa(llm: Any | None = None) -> Agent:
    file_read_tool = ProjectFileReadTool()
    code_tools = build_code_execution_tools()
    project = _project_dir_str()
    return Agent(
        role="QA Tester",
        goal=(
            "Verify the implemented task against its acceptance criteria — functional, "
            "edge cases, error handling — and return PASS or a structured bug report."
        ),
        backstory=(
            "You are a meticulous QA Tester. You run tests via run_code_execution, read "
            "the changed files, and check them against the task's acceptance criteria. "
            "On failure you output exactly: Bug ID / Task ID / Severity / Steps to "
            "Reproduce / Expected / Actual. You never write or fix code yourself. "
            f"The ONLY project directory you may inspect is: {project}"
        ),
        tools=[file_read_tool, *code_tools],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=15,
    )


def create_devops(llm: Any | None = None) -> Agent:
    file_read_tool = ProjectFileReadTool()
    code_tools = build_code_execution_tools()
    project = _project_dir_str()
    return Agent(
        role="Senior DevOps Engineer",
        goal=(
            "Decide production readiness after QA has passed all phases — never deploy "
            "on a hunch."
        ),
        backstory=(
            "You check: all QA passed, architecture checks satisfied, rollback plan "
            "exists, secrets are not hardcoded, CI/tests green. You output READY or "
            "NOT_READY with a checklist. You never merge to main/deploy without an "
            "explicit human YES via the existing Telegram approval flow. "
            f"The ONLY project directory you may inspect is: {project}"
        ),
        tools=[file_read_tool, *code_tools],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=15,
    )


def create_developer(llm: Any | None = None) -> Agent:
    file_read_tool = ProjectFileReadTool()
    file_writer_tool = ProjectFileWriterTool()
    code_tools = build_code_execution_tools()
    project = _project_dir_str()

    return Agent(
        role="Senior Full-Stack Developer",
        goal=(
            "Implement the Architect's approved plan: write production-quality code, "
            "add tests, and create a git commit."
        ),
        backstory=(
            "You are a Senior Full-Stack Developer who ships clean, tested code. "
            "You follow the Architect's plan precisely, write focused tests, and commit "
            "with a clear message. You never invent scope beyond the approved plan. "
            "If the plan contains NEEDS_APPROVAL and approval has not been granted, "
            "you stop and report that implementation is blocked. "
            "Git rules: work only on phase/* or dev branches — NEVER checkout, merge into, "
            "rebase, or push main/master; never force-push; never delete main or dev. "
            "To run shell commands use the tool named run_code_execution "
            "(also registered as code_execution). "
            f"CRITICAL: Write ALL files only under {project}. Never write into any other folder."
        ),
        tools=[file_read_tool, file_writer_tool, *code_tools],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=25,
    )
