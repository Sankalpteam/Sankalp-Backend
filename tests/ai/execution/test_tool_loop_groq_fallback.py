"""The tool ran, so the answer is written from what it returned.

When Gemini is at its limit in the middle of a tool loop, the tool has already run and the data
already exists. Before this, the loop raised and the user got nothing - the work was done and
thrown away. Now the fallback answers from the results.
"""

from __future__ import annotations

import pytest

from app.ai.execution import tool_loop
from app.ai.gateway.gemini_service import ModelBusyError


@pytest.mark.asyncio
async def test_a_busy_gemini_is_answered_from_the_tool_results(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def busy(*_args, **_kwargs):
        raise ModelBusyError("busy")

    async def fake_groq(message, history=None, document_context="", **_kwargs):
        seen["context"] = document_context
        return "Bengaluru is 24 degrees with cloud cover."

    monkeypatch.setattr(
        "app.ai.gateway.gemini_service.generate_gemini_function_call_reply", busy
    )
    monkeypatch.setattr("app.ai.gateway.groq_service.groq_available", lambda: True)
    monkeypatch.setattr("app.ai.gateway.groq_service.generate_groq_reply", fake_groq)

    answer = await tool_loop._answer_from_tool_results(
        "ivattina weather hegide",
        executed=(("search_web", '{"results": [{"title": "Bengaluru weather"}]}'),),
        history=[],
        extra_system_instruction="",
    )

    assert answer == "Bengaluru is 24 degrees with cloud cover."
    assert "search_web" in seen["context"]
    assert "Bengaluru weather" in seen["context"]


@pytest.mark.asyncio
async def test_without_groq_the_original_error_stands(monkeypatch) -> None:
    monkeypatch.setattr("app.ai.gateway.groq_service.groq_available", lambda: False)

    answer = await tool_loop._answer_from_tool_results(
        "anything",
        executed=(),
        history=[],
        extra_system_instruction="",
    )

    assert answer is None


@pytest.mark.asyncio
async def test_with_no_tool_results_the_answer_is_still_a_sentence(monkeypatch) -> None:
    async def fake_groq(message, history=None, document_context="", **_kwargs):
        return "I could not look that up."

    monkeypatch.setattr("app.ai.gateway.groq_service.groq_available", lambda: True)
    monkeypatch.setattr("app.ai.gateway.groq_service.generate_groq_reply", fake_groq)

    answer = await tool_loop._answer_from_tool_results(
        "anything",
        executed=(),
        history=[],
        extra_system_instruction="",
    )

    assert answer == "I could not look that up."
