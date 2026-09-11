"""LLM provider auto-failover engine for upotto-foreman.

Automatically retries across available LLM providers (anthropic, gemini,
openrouter, agentrouter) when encountering retryable API errors (rate limits,
quota exhaustion, auth errors, timeouts).
"""

from __future__ import annotations

import logging
import os
import types
from typing import Any

from crewai import LLM
import litellm.exceptions as le

from agents import build_crew_llm, has_valid_key

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_PRIORITY: list[str] = [
    "anthropic",
    "gemini",
    "openrouter",
    "agentrouter",
]


class AllProvidersFailedError(RuntimeError):
    """Raised when all candidate LLM providers fail."""


RETRYABLE_TYPES = (
    le.RateLimitError,
    le.BudgetExceededError,
    le.AuthenticationError,
    le.APIConnectionError,
    le.ServiceUnavailableError,
    le.Timeout,
    TimeoutError,
    ConnectionError,
)

NON_RETRYABLE_TYPES = (
    le.BadRequestError,
    le.ContentPolicyViolationError,
    le.ContextWindowExceededError,
    le.NotFoundError,
    le.JSONSchemaValidationError,
    le.UnprocessableEntityError,
    le.UnsupportedParamsError,
    ValueError,
    TypeError,
    KeyError,
    AssertionError,
)


def get_provider_priority() -> list[str]:
    """Return ordered list of providers from env or default."""
    raw = os.getenv("LLM_PROVIDER_PRIORITY", "").strip()
    if not raw:
        return list(DEFAULT_PROVIDER_PRIORITY)
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items if items else list(DEFAULT_PROVIDER_PRIORITY)


def get_available_providers() -> list[str]:
    """Return providers from priority list that have valid API keys in environment."""
    priority = get_provider_priority()
    return [p for p in priority if has_valid_key(p)]


def is_provider_error_retryable(exception: BaseException) -> bool:
    """Classify errors: rate limit (429), quota, auth (401/403), timeout -> retryable.

    Non-retryable errors (bad request, content policy, validation errors) return False.
    """
    if isinstance(exception, NON_RETRYABLE_TYPES):
        return False

    if isinstance(exception, RETRYABLE_TYPES):
        return True

    status_code = getattr(exception, "status_code", None)
    if status_code is not None:
        if status_code in (400, 404, 422):
            return False
        if status_code in (401, 403, 408, 429, 500, 502, 503, 504):
            return True

    msg = str(exception).lower()
    non_retryable_keywords = (
        "bad request",
        "content policy",
        "context length",
        "schema validation",
    )
    if any(kw in msg for kw in non_retryable_keywords):
        return False

    retryable_keywords = (
        "rate limit",
        "rate_limit",
        "quota",
        "budget exceeded",
        "invalid api key",
        "invalid_api_key",
        "authentication",
        "unauthorized",
        "expired key",
        "timed out",
        "timeout",
        "service unavailable",
        "overloaded",
        "connection error",
        "connection reset",
    )
    if any(kw in msg for kw in retryable_keywords):
        return True

    return False


def _resolve_candidate_providers() -> list[str]:
    """Determine ordered list of providers to attempt."""
    available = get_available_providers()
    current = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    if current in available:
        candidates = [current] + [p for p in available if p != current]
    else:
        candidates = list(available)

    return candidates


def _format_attempts(attempts: list[tuple[str, BaseException]]) -> str:
    return "; ".join(f"{p}: {type(e).__name__}({e})" for p, e in attempts)


def build_crew_llm_with_failover(role: str | None = None, **kwargs: Any) -> LLM:
    """Construct an LLM instance with automatic failover across available providers.

    1. Resolves candidate providers in priority order (current LLM_PROVIDER first).
    2. Falls through construction on retryable failure.
    3. Wraps llm.call() so call-time retryable errors seamlessly fall over to the
       next available provider.
    4. Raises AllProvidersFailedError if all candidate providers fail.
    """
    candidates = _resolve_candidate_providers()
    if not candidates:
        raise AllProvidersFailedError(
            "No LLM providers available with valid API keys in environment."
        )

    construction_attempts: list[tuple[str, BaseException]] = []
    constructed_llm: LLM | None = None
    start_index = 0

    for idx, provider_name in enumerate(candidates):
        try:
            constructed_llm = build_crew_llm(
                provider=provider_name,
                disable_failover=True,
                **kwargs,
            )
            start_index = idx
            break
        except Exception as exc:
            if not is_provider_error_retryable(exc):
                logger.error(
                    "Non-retryable LLM construction error for provider '%s': %s",
                    provider_name,
                    exc,
                )
                raise
            construction_attempts.append((provider_name, exc))
            next_p = candidates[idx + 1] if idx + 1 < len(candidates) else "none"
            logger.warning(
                "LLM construction failed for provider '%s' (%s). Failing over to '%s'...",
                provider_name,
                exc,
                next_p,
            )

    if constructed_llm is None:
        summary = _format_attempts(construction_attempts)
        raise AllProvidersFailedError(
            f"All available LLM providers failed at construction: {summary}"
        )

    # Attach call-time failover wrapper
    orig_call = getattr(constructed_llm, "call")
    active_state = {
        "current_llm": constructed_llm,
        "current_idx": start_index,
    }

    def wrapped_call(self: LLM, *call_args: Any, **call_kwargs: Any) -> Any:
        attempts: list[tuple[str, BaseException]] = []

        while active_state["current_idx"] < len(candidates):
            current_provider = candidates[active_state["current_idx"]]
            target_llm = active_state["current_llm"]

            if target_llm is None:
                try:
                    logger.info("Instantiating failover LLM provider '%s'...", current_provider)
                    target_llm = build_crew_llm(
                        provider=current_provider,
                        disable_failover=True,
                        **kwargs,
                    )
                    active_state["current_llm"] = target_llm
                except Exception as exc:
                    if not is_provider_error_retryable(exc):
                        raise
                    attempts.append((current_provider, exc))
                    active_state["current_idx"] += 1
                    active_state["current_llm"] = None
                    continue

            try:
                # Call underlying native call method
                if target_llm is constructed_llm:
                    return orig_call(*call_args, **call_kwargs)
                return target_llm.call(*call_args, **call_kwargs)
            except Exception as exc:
                if not is_provider_error_retryable(exc):
                    logger.error(
                        "Non-retryable LLM call error on provider '%s': %s",
                        current_provider,
                        exc,
                    )
                    raise

                attempts.append((current_provider, exc))
                active_state["current_idx"] += 1
                active_state["current_llm"] = None

                if active_state["current_idx"] < len(candidates):
                    next_provider = candidates[active_state["current_idx"]]
                    logger.warning(
                        "LLM call failed on provider '%s' (%s). Failing over to next provider '%s'...",
                        current_provider,
                        exc,
                        next_provider,
                    )
                else:
                    logger.error(
                        "LLM call failed on provider '%s' (%s). All %d candidate providers exhausted.",
                        current_provider,
                        exc,
                        len(candidates),
                    )

        summary = _format_attempts(attempts)
        raise AllProvidersFailedError(f"All candidate LLM providers failed: {summary}")

    constructed_llm.call = types.MethodType(wrapped_call, constructed_llm)
    return constructed_llm
