"""A stalled or rejected primary hands the answer to the secondary.

The production symptom was "Sankalp is taking too long to respond": Gemini accepted the
connection, then either stalled or answered 429/5xx, and nothing took over. The user waited
out their own client timeout.

These tests hold the rule: a failure *before* the first chunk fails over; a failure *after*
it does not, because half an answer that silently restarts under a second provider is worse
than an honest error.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ai.gateway import service


def _collect(agen) -> list[str]:
    async def run():
        return [chunk async for chunk in agen]

    return asyncio.run(run())


def _use(monkeypatch, providers, blocking="the blocking answer") -> None:
    monkeypatch.setenv("INDOONE_STREAM_PROVIDERS", "true")
    monkeypatch.setattr(service, "_provider_streams", lambda: providers)

    async def blocking_reply(*_args, **_kwargs):
        return blocking

    monkeypatch.setattr(service, "generate_reply", blocking_reply)


def test_a_primary_timeout_fails_over_to_the_secondary(monkeypatch, caplog) -> None:
    async def gemini(*_args, **_kwargs):
        raise TimeoutError("gemini read timeout")
        yield ""  # pragma: no cover - makes this an async generator

    async def groq(*_args, **_kwargs):
        for chunk in ("Hello", " from", " Azure"):
            yield chunk

    _use(monkeypatch, [("Gemini", gemini), ("Groq", groq)])

    with caplog.at_level("WARNING"):
        assert _collect(service.stream_generate("hi")) == ["Hello", " from", " Azure"]

    assert any("failing over to Groq" in record.message for record in caplog.records)


def test_a_primary_rate_limit_fails_over(monkeypatch) -> None:
    async def gemini(*_args, **_kwargs):
        raise RuntimeError("Gemini API returned HTTP 429")
        yield ""  # pragma: no cover

    async def groq(*_args, **_kwargs):
        yield "groq answered"

    _use(monkeypatch, [("Gemini", gemini), ("Groq", groq)])

    assert _collect(service.stream_generate("hi")) == ["groq answered"]


def test_a_primary_that_stalls_before_the_first_chunk_fails_over(monkeypatch) -> None:
    async def slow_gemini(*_args, **_kwargs):
        await asyncio.sleep(5)
        yield "too late"

    async def groq(*_args, **_kwargs):
        yield "groq answered"

    _use(monkeypatch, [("Gemini", slow_gemini), ("Groq", groq)])
    monkeypatch.setattr(service, "_STREAM_FIRST_CHUNK_SECONDS", 0.05)

    assert _collect(service.stream_generate("hi")) == ["groq answered"]


def test_a_failure_after_the_first_chunk_is_not_hidden(monkeypatch) -> None:
    async def half_broken(*_args, **_kwargs):
        yield "partial"
        raise RuntimeError("connection dropped")

    async def groq(*_args, **_kwargs):
        yield "groq answered"

    _use(monkeypatch, [("Gemini", half_broken), ("Groq", groq)])

    with pytest.raises(RuntimeError):
        _collect(service.stream_generate("hi"))


def test_when_every_provider_fails_the_blocking_path_answers(monkeypatch) -> None:
    async def gemini(*_args, **_kwargs):
        raise TimeoutError("no")
        yield ""  # pragma: no cover

    async def groq(*_args, **_kwargs):
        raise RuntimeError("also no")
        yield ""  # pragma: no cover

    _use(monkeypatch, [("Gemini", gemini), ("Groq", groq)], blocking="the blocking answer")

    assert _collect(service.stream_generate("hi")) == ["the blocking answer"]


def test_groq_is_a_blocking_fallback_not_a_fake_stream(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    assert [label for label, _stream in service._provider_streams()] == ["Gemini"]


def test_no_secondary_is_offered_without_its_credentials(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_MODEL_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert [label for label, _stream in service._provider_streams()] == ["Gemini"]
