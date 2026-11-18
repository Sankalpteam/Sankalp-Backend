"""Every entrypoint must share the exact Gemini backend-selection policy."""
from __future__ import annotations

import pytest

from app.ai.gateway import provider_policy, service
from app.ai.execution import tool_loop
from app.capabilities.platform import channel_common


@pytest.mark.parametrize(
    ("configured", "enabled"),
    [
        (None, True),
        ("gemini", True),
        ("  GEMINI  ", True),
        ("", False),
        ("groq", False),
        ("azure", False),
        ("local", False),
        ("not-a-provider", False),
    ],
)
def test_provider_policy_is_consistent_across_endpoints(monkeypatch, configured, enabled):
    if configured is None:
        monkeypatch.delenv("INDOONE_MODEL_BACKEND", raising=False)
    else:
        monkeypatch.setenv("INDOONE_MODEL_BACKEND", configured)

    assert provider_policy.is_primary_backend_enabled() is enabled
    assert service._gemini_backend_enabled() is enabled
    assert tool_loop._gemini_backend_enabled() is enabled
    assert channel_common._tool_loop_available() is enabled


def test_groq_is_secondary_only_and_not_a_primary_mode(monkeypatch):
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-only")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-only")
    assert provider_policy.FALLBACK_PROVIDER == "groq"
    assert provider_policy.is_primary_backend_enabled() is False
    assert service.ai_provider_status()["configuration_ready"] is False
