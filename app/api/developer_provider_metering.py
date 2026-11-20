"""Strict provider usage extraction for future developer API metering.

The consumer chat gateway may stream or invoke multiple tools and providers;
its model usage estimates and aggregated counters are NOT chargeable evidence.
Only a single finalized upstream reply, with exact provider-specific usage
metadata and matching expected model, can become provider-verified usage.

This module is internal. It does not enable API resale, checkout or inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class UsageNotVerified(ValueError):
    """The provider response is insufficient to invoice a customer."""


@dataclass(frozen=True)
class VerifiedProviderTokens:
    provider: str
    upstream_model: str
    upstream_response_id: str
    input_tokens: int
    output_tokens: int


def _dict(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise UsageNotVerified(f"{context} missing or invalid")
    return value


def _str(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise UsageNotVerified(f"{context} missing or invalid")
    return value.strip()


def _tokens(value: object, context: str, *, zero_allowed: bool = True) -> int:
    # bool is a subclass of int, and an untrusted API response may contain one.
    if type(value) is not int or value < (0 if zero_allowed else 1) or value > 10_000_000:
        raise UsageNotVerified(f"{context} must be a verified integer token count")
    return value


def _finish(choices: object, context: str, allowed: frozenset[str], field: str) -> None:
    if not isinstance(choices, list) or len(choices) != 1:
        raise UsageNotVerified(f"{context} must contain exactly one finalized choice")
    first = _dict(choices[0], f"{context} choice")
    if first.get(field) not in allowed:
        raise UsageNotVerified(f"{context} did not finish in a supported state")


def extract_final_provider_usage(
    *, provider: str, upstream_model: str, response: Mapping[str, Any],
) -> VerifiedProviderTokens:
    """Fail closed on partial/ambiguous/missing usage, model changes and fallback.

    Call only after a completed, non-streamed, single provider invocation. A
    gateway must still persist request identity, reservation, usage and charge
    atomically; this function does NOT prove the authenticity of its caller.
    """
    wanted_model = _str(upstream_model, "configured upstream model")
    record = _dict(response, "provider response")
    actual_model = _str(record.get("model") if provider == "groq" else record.get("modelVersion"),
                        "provider-reported model")
    if actual_model != wanted_model:
        raise UsageNotVerified("provider returned a model other than the reserved model")

    if provider == "gemini":
        response_id = _str(record.get("responseId"), "Gemini response ID")
        _finish(record.get("candidates"), "Gemini response", frozenset({"STOP", "MAX_TOKENS"}), "finishReason")
        metadata = _dict(record.get("usageMetadata"), "Gemini usageMetadata")
        prompt = _tokens(metadata.get("promptTokenCount"), "Gemini promptTokenCount", zero_allowed=False)
        output = _tokens(metadata.get("candidatesTokenCount"), "Gemini candidatesTokenCount")
        total = _tokens(metadata.get("totalTokenCount"), "Gemini totalTokenCount", zero_allowed=False)
        # Thinking, cached-input and tool prompts have different pricing and
        # may not be visible in output token counts. No pricing is approved yet.
        # Forbid them until each category is priced and reserved explicitly.
        for extra in ("thoughtsTokenCount", "cachedContentTokenCount", "toolUsePromptTokenCount"):
            if extra in metadata and _tokens(metadata[extra], f"Gemini {extra}") != 0:
                raise UsageNotVerified(f"Gemini {extra} requires separate billing rules")
        if total != prompt + output:
            raise UsageNotVerified("Gemini token total is not explainable by plain prompt and output")
        return VerifiedProviderTokens("gemini", actual_model, response_id, prompt, output)

    if provider == "groq":
        response_id = _str(record.get("id"), "Groq response ID")
        _finish(record.get("choices"), "Groq response", frozenset({"stop", "length"}), "finish_reason")
        metadata = _dict(record.get("usage"), "Groq usage")
        prompt = _tokens(metadata.get("prompt_tokens"), "Groq prompt_tokens", zero_allowed=False)
        output = _tokens(metadata.get("completion_tokens"), "Groq completion_tokens")
        total = _tokens(metadata.get("total_tokens"), "Groq total_tokens", zero_allowed=False)
        if prompt + output != total:
            raise UsageNotVerified("Groq total tokens do not match input and output")
        return VerifiedProviderTokens("groq", actual_model, response_id, prompt, output)

    raise UsageNotVerified("provider is not approved for usage metering")
