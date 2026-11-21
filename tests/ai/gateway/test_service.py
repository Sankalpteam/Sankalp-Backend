"""Provider routing without live API calls or local model checkpoint."""
from __future__ import annotations

import pytest

from app.ai.gateway import gemini_service, groq_service, service


@pytest.mark.asyncio
async def test_default_gemini_generates_text_and_preserves_context(monkeypatch):
    monkeypatch.delenv("INDOONE_MODEL_BACKEND", raising=False)
    monkeypatch.setattr(groq_service, "groq_available", lambda: False)
    captured = {}

    async def fake_gemini(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return "Gemini verified answer"

    monkeypatch.setattr(gemini_service, "generate_gemini_reply", fake_gemini)
    reply = await service.generate_reply(
        "Explain this text",
        history=[("user", "prior message")],
        document_context="user uploaded content",
    )
    assert reply == "Gemini verified answer"
    assert captured["message"] == "Explain this text"
    assert captured["document_context"] == "user uploaded content"


@pytest.mark.asyncio
async def test_groq_fallback_only_when_gemini_is_busy(monkeypatch):
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "gemini")
    monkeypatch.setattr(groq_service, "groq_available", lambda: True)
    calls = []

    async def unavailable(*_args, **_kwargs):
        calls.append("gemini")
        raise gemini_service.ModelBusyError("busy")

    async def groq_answer(*_args, **_kwargs):
        calls.append("groq")
        return "Groq response"

    monkeypatch.setattr(gemini_service, "generate_gemini_reply", unavailable)
    monkeypatch.setattr(groq_service, "generate_groq_reply", groq_answer)
    assert await service.generate_reply("hello") == "Groq response"
    assert calls == ["gemini", "groq"]


@pytest.mark.asyncio
async def test_multimodal_does_not_discard_attachments_to_fallback(monkeypatch):
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "gemini")
    monkeypatch.setattr(groq_service, "groq_available", lambda: True)

    async def unavailable(*_args, **_kwargs):
        raise gemini_service.ModelBusyError("busy")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("text-only Groq cannot accept multimodal attachment")

    monkeypatch.setattr(gemini_service, "generate_gemini_reply", unavailable)
    monkeypatch.setattr(groq_service, "generate_groq_reply", forbidden)
    with pytest.raises(gemini_service.ModelBusyError):
        await service.generate_reply(
            "What is in this image?",
            media_attachment={"mime_type": "image/png", "content": b"fake"},
        )


@pytest.mark.asyncio
async def test_legacy_local_backend_is_not_a_provider(monkeypatch):
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "local")
    with pytest.raises(RuntimeError, match="Unsupported provider"):
        await service.generate_reply("hello")


def test_provider_status_is_gemini_without_exposing_key(monkeypatch):
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-test-value")
    status = service.ai_provider_status()
    assert status["provider"] == "gemini"
    assert status["configuration_ready"] is True
    assert "secret-test-value" not in repr(status)
