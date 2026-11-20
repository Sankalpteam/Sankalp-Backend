"""No mocked token counts may be silently accepted as billable metadata."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.developer_provider_metering import (
    UsageNotVerified, extract_final_provider_usage,
)


def gemini():
    return {
        "modelVersion": "gemini-test-version",
        "responseId": "provider-response-one",
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Hello!"}]}}],
        "usageMetadata": {
            "promptTokenCount": 120, "candidatesTokenCount": 30,
            "totalTokenCount": 150,
        },
    }


def groq():
    return {
        "model": "groq-test-version",
        "id": "chatcmpl-abcd",
        "choices": [{"finish_reason": "stop", "message": {"content": "Hello!"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


def test_gemini_valid_verified_usage():
    evidence = extract_final_provider_usage(
        provider="gemini", upstream_model="gemini-test-version", response=gemini(),
    )
    assert (evidence.provider, evidence.upstream_model, evidence.upstream_response_id) == (
        "gemini", "gemini-test-version", "provider-response-one")
    assert (evidence.input_tokens, evidence.output_tokens) == (120, 30)


def test_groq_valid_verified_usage():
    evidence = extract_final_provider_usage(
        provider="groq", upstream_model="groq-test-version", response=groq(),
    )
    assert (evidence.input_tokens, evidence.output_tokens) == (120, 30)


@pytest.mark.parametrize("corruption", [
    lambda r: r.pop("usageMetadata"),
    lambda r: r["usageMetadata"].pop("promptTokenCount"),
    lambda r: r["usageMetadata"].update({"promptTokenCount": True}),
    lambda r: r["usageMetadata"].update({"promptTokenCount": 120.5}),
    lambda r: r["usageMetadata"].update({"candidatesTokenCount": -1}),
    lambda r: r["usageMetadata"].update({"totalTokenCount": 149}),
    lambda r: r["usageMetadata"].update({"thoughtsTokenCount": 3}),
    lambda r: r["usageMetadata"].update({"cachedContentTokenCount": 2}),
    lambda r: r["usageMetadata"].update({"toolUsePromptTokenCount": 1}),
    lambda r: r.pop("responseId"),
    lambda r: r.update({"modelVersion": "fallback-model"}),
    lambda r: r["candidates"][0].update({"finishReason": "SAFETY"}),
    lambda r: r["candidates"].append({"finishReason": "STOP"}),
])
def test_gemini_rejects_ambiguous_or_unpriced_counts(corruption):
    data = deepcopy(gemini())
    corruption(data)
    with pytest.raises(UsageNotVerified):
        extract_final_provider_usage(
            provider="gemini", upstream_model="gemini-test-version", response=data,
        )


@pytest.mark.parametrize("corruption", [
    lambda r: r.pop("usage"),
    lambda r: r["usage"].pop("completion_tokens"),
    lambda r: r["usage"].update({"prompt_tokens": True}),
    lambda r: r["usage"].update({"completion_tokens": 4.3}),
    lambda r: r["usage"].update({"total_tokens": 12}),
    lambda r: r.update({"model": "fallback-model"}),
    lambda r: r.pop("id"),
    lambda r: r["choices"][0].update({"finish_reason": "tool_calls"}),
    lambda r: r["choices"].append({"finish_reason": "stop"}),
])
def test_groq_rejects_missing_wrong_or_ambiguous_counts(corruption):
    data = deepcopy(groq())
    corruption(data)
    with pytest.raises(UsageNotVerified):
        extract_final_provider_usage(
            provider="groq", upstream_model="groq-test-version", response=data,
        )


def test_wrong_provider_rejected_even_with_similar_response():
    with pytest.raises(UsageNotVerified):
        extract_final_provider_usage(
            provider="groq", upstream_model="gemini-test-version", response=gemini(),
        )
