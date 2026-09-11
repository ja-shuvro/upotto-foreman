from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest

import litellm.exceptions as le
from agents import build_crew_llm, has_valid_key
from llm_failover import (
    AllProvidersFailedError,
    build_crew_llm_with_failover,
    get_available_providers,
    get_provider_priority,
    is_provider_error_retryable,
)


def test_has_valid_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    assert not has_valid_key("anthropic")
    assert not has_valid_key("gemini")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert has_valid_key("anthropic")

    monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
    assert has_valid_key("gemini")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert has_valid_key("openrouter")

    monkeypatch.setenv("AGENTROUTER_API_KEY", "sk-ar-test")
    assert has_valid_key("agentrouter")


def test_get_available_providers_filtering(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_PRIORITY", raising=False)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")

    available = get_available_providers()
    assert available == ["anthropic", "gemini"]

    # Test custom priority override
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "gemini,agentrouter,anthropic")
    assert get_provider_priority() == ["gemini", "agentrouter", "anthropic"]
    assert get_available_providers() == ["gemini", "anthropic"]


def test_is_provider_error_retryable():
    # Real LiteLLM retryable exceptions
    rate_limit = le.RateLimitError(
        message="Rate limit reached",
        llm_provider="anthropic",
        model="claude-3-5-sonnet",
    )
    auth_err = le.AuthenticationError(
        message="Invalid API Key",
        llm_provider="anthropic",
        model="claude-3-5-sonnet",
    )
    budget_err = le.BudgetExceededError(current_cost=10, max_budget=5)
    timeout_err = le.Timeout(
        message="Request timed out",
        model="claude-3-5-sonnet",
        llm_provider="anthropic",
    )

    assert is_provider_error_retryable(rate_limit)
    assert is_provider_error_retryable(auth_err)
    assert is_provider_error_retryable(budget_err)
    assert is_provider_error_retryable(timeout_err)
    assert is_provider_error_retryable(TimeoutError("Socket timeout"))
    assert is_provider_error_retryable(ConnectionError("Connection aborted"))

    # Real LiteLLM non-retryable exceptions
    bad_req = le.BadRequestError(
        message="Invalid schema",
        llm_provider="anthropic",
        model="claude-3-5-sonnet",
    )
    content_err = le.ContentPolicyViolationError(
        message="Flagged content",
        llm_provider="anthropic",
        model="claude-3-5-sonnet",
    )

    assert not is_provider_error_retryable(bad_req)
    assert not is_provider_error_retryable(content_err)
    assert not is_provider_error_retryable(ValueError("Bad parameter"))
    assert not is_provider_error_retryable(TypeError("Wrong type"))

    # Status code checks
    class CustomStatusError(Exception):
        def __init__(self, code: int):
            self.status_code = code

    assert is_provider_error_retryable(CustomStatusError(429))
    assert is_provider_error_retryable(CustomStatusError(503))
    assert not is_provider_error_retryable(CustomStatusError(400))
    assert not is_provider_error_retryable(CustomStatusError(404))


def test_build_crew_llm_with_failover_construction_fallthrough(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "anthropic,gemini")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    mock_gemini_llm = MagicMock()
    mock_gemini_llm.model = "gemini/gemini-2.0-flash"

    def fake_build(provider=None, **kwargs):
        if provider == "anthropic":
            raise le.RateLimitError(
                message="Rate limited on anthropic",
                llm_provider="anthropic",
                model="claude-3-5-sonnet",
            )
        if provider == "gemini":
            return mock_gemini_llm
        raise ValueError(f"Unexpected provider {provider}")

    with patch("llm_failover.build_crew_llm", side_effect=fake_build):
        llm = build_crew_llm_with_failover()
        assert llm == mock_gemini_llm


def test_build_crew_llm_with_failover_construction_non_retryable_stops(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    def fake_build(provider=None, **kwargs):
        raise le.BadRequestError(
            message="Malformed request payload",
            llm_provider="anthropic",
            model="claude-3-5-sonnet",
        )

    with patch("llm_failover.build_crew_llm", side_effect=fake_build):
        with pytest.raises(le.BadRequestError, match="Malformed request"):
            build_crew_llm_with_failover()


def test_build_crew_llm_with_failover_all_exhausted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "anthropic,gemini")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    def fake_build(provider=None, **kwargs):
        raise le.RateLimitError(
            message=f"Rate limited on {provider}",
            llm_provider=provider or "unknown",
            model="test-model",
        )

    with patch("llm_failover.build_crew_llm", side_effect=fake_build):
        with pytest.raises(AllProvidersFailedError, match="All available LLM providers failed at construction"):
            build_crew_llm_with_failover()


def test_call_time_failover_retries_next_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "anthropic,gemini")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    anthropic_llm = MagicMock()
    anthropic_llm.model = "anthropic/claude-sonnet-5"
    orig_anthropic_call = MagicMock(side_effect=le.RateLimitError(
        message="429 Rate limit on Anthropic",
        llm_provider="anthropic",
        model="claude-sonnet-5",
    ))
    anthropic_llm.call = orig_anthropic_call

    gemini_llm = MagicMock()
    gemini_llm.model = "gemini/gemini-2.0-flash"
    gemini_llm.call.return_value = "Gemini response after failover"

    def fake_build(provider=None, **kwargs):
        if provider == "anthropic":
            return anthropic_llm
        if provider == "gemini":
            return gemini_llm
        raise ValueError(f"Unexpected provider {provider}")

    with patch("llm_failover.build_crew_llm", side_effect=fake_build):
        llm = build_crew_llm_with_failover()
        # Call invocation triggers failover from anthropic -> gemini
        res = llm.call("Hello world")
        assert res == "Gemini response after failover"
        assert orig_anthropic_call.called
        assert gemini_llm.call.called


def test_call_time_failover_stops_on_non_retryable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "anthropic,gemini")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    anthropic_llm = MagicMock()
    anthropic_llm.call.side_effect = le.BadRequestError(
        message="Bad prompt format",
        llm_provider="anthropic",
        model="claude-sonnet-5",
    )

    gemini_llm = MagicMock()

    def fake_build(provider=None, **kwargs):
        if provider == "anthropic":
            return anthropic_llm
        return gemini_llm

    with patch("llm_failover.build_crew_llm", side_effect=fake_build):
        llm = build_crew_llm_with_failover()
        with pytest.raises(le.BadRequestError, match="Bad prompt format"):
            llm.call("Hello world")

        assert not gemini_llm.call.called


def test_build_crew_llm_feature_flag_integration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("LLM_FAILOVER_ENABLED", raising=False)

    # When disabled (default): returns standard LLM without failover delegation
    llm_std = build_crew_llm()
    assert llm_std.model in ("anthropic/claude-sonnet-5", "claude-sonnet-5")

    # When enabled: delegates to build_crew_llm_with_failover
    monkeypatch.setenv("LLM_FAILOVER_ENABLED", "true")
    mock_failover_llm = MagicMock()
    with patch("llm_failover.build_crew_llm_with_failover", return_value=mock_failover_llm) as mock_fn:
        llm_f = build_crew_llm(temperature=0.4)
        assert llm_f == mock_failover_llm
        mock_fn.assert_called_once_with(temperature=0.4)
