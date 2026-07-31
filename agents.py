"""Architect and Developer CrewAI agent definitions (Claude via crewai.LLM)."""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable

from crewai import Agent, LLM
from crewai.tools import BaseTool as CrewBaseTool
from crewai_tools import (
    FileReadTool,
    FileWriterTool,
)
from langchain_community.tools import DuckDuckGoSearchRun  # free, no API key needed
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from code_tool import build_code_execution_tool
from config import IGNORED_DIRS, get_project_dir
from cost_tracker import CostTracker

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")


def _project_dir_str() -> str:
    return str(get_project_dir())


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _bare_model_name(raw: str) -> str:
    """Strip any provider prefix the user may have typed (anthropic/, openrouter/)."""
    for prefix in ("openrouter/anthropic/", "openrouter/", "anthropic/"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def build_crew_llm(*, temperature: float = 0.2) -> LLM:
    """crewai.LLM (litellm-backed) — THIS is what Agent(llm=...) must receive.

    Supports either direct Anthropic API or OpenRouter, based on which key is set.
    Set LLM_PROVIDER=openrouter in .env to route through OpenRouter instead of
    api.anthropic.com directly.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    bare_model = _bare_model_name(os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip())

    if provider == "agentrouter":
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

    if provider == "openrouter":
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
    file_read_tool = FileReadTool()
    search_tool = DuckDuckGoSearchTool()  # free — no API key, no cost

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
            "NEEDS_APPROVAL: <reason>."
        ),
        tools=[directory_tool, file_read_tool, search_tool],
        llm=llm or build_crew_llm(temperature=0.2),
        verbose=True,
        allow_delegation=False,
        max_iter=15,
    )


def create_developer(llm: Any | None = None) -> Agent:
    file_read_tool = FileReadTool()
    file_writer_tool = FileWriterTool()
    code_tool = build_code_execution_tool()

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
            "you stop and report that implementation is blocked."
        ),
        tools=[file_read_tool, file_writer_tool, code_tool],
        llm=llm or build_crew_llm(temperature=0.1),
        verbose=True,
        allow_delegation=False,
        max_iter=25,
    )
