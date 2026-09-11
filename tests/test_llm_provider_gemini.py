from __future__ import annotations

import os
import pytest

from agents import build_crew_llm


def test_build_crew_llm_gemini_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-123")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    llm = build_crew_llm(temperature=0.3)
    assert llm.model == "gemini/gemini-2.0-flash"
    assert llm.api_key == "test-gemini-key-123"
    assert llm.temperature == 0.3


def test_build_crew_llm_gemini_google_api_key_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key-456")

    llm = build_crew_llm()
    assert llm.model == "gemini/gemini-2.0-flash"
    assert llm.api_key == "test-google-key-456"


def test_build_crew_llm_gemini_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(EnvironmentError, match="GEMINI_API_KEY"):
        build_crew_llm()


def test_build_crew_llm_gemini_custom_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-pro")

    llm = build_crew_llm()
    assert llm.model == "gemini/gemini-1.5-pro"


def test_build_crew_llm_gemini_model_prefix_stripped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini/gemini-2.0-flash")

    llm = build_crew_llm()
    # Verify no double prefix "gemini/gemini/..."
    assert llm.model == "gemini/gemini-2.0-flash"

