"""Transient Gemini outages should fall back to Groq, not invent tool results."""
from __future__ import annotations

import httpx
import pytest

from app.ai.gateway import gemini_service


@pytest.mark.asyncio
async def test_gemini_503_exhausted_retry_is_a_fallback_signal(monkeypatch):
    monkeypatch.setattr(gemini_service, "_MAX_ATTEMPTS", 1)

    def unavailable(request):
        return httpx.Response(503, json={"error": "upstream unavailable"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(gemini_service.ModelBusyError):
            await gemini_service._post_generate_content(
                client, "https://example.invalid", {}, {"contents": []},
            )


@pytest.mark.asyncio
async def test_gemini_connect_timeout_is_a_fallback_signal(monkeypatch):
    monkeypatch.setattr(gemini_service, "_MAX_ATTEMPTS", 1)

    def unreachable(request):
        raise httpx.ConnectTimeout("provider network timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as client:
        with pytest.raises(gemini_service.ModelBusyError):
            await gemini_service._post_generate_content(
                client, "https://example.invalid", {}, {"contents": []},
            )


@pytest.mark.asyncio
async def test_authentication_error_does_not_trigger_groq_fallback(monkeypatch):
    monkeypatch.setattr(gemini_service, "_MAX_ATTEMPTS", 1)

    def unauthorized(request):
        return httpx.Response(403, json={"error": "forbidden"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)) as client:
        with pytest.raises(RuntimeError, match="authentication failed") as exc:
            await gemini_service._post_generate_content(
                client, "https://example.invalid", {}, {"contents": []},
            )
    assert not isinstance(exc.value, gemini_service.ModelBusyError)
