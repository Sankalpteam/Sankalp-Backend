"""Disabled-by-default, isolated OpenAI-compatible B2B inference gateway.

This is NOT the consumer chat endpoint. It cannot call the user's plugins,
integrations, OAuth credentials or the consumer AI agent/tool pipeline.
All chargeable calls require reviewed upstream model prices, verified
merchant funding, a live scoped developer key and an immutable prepaid hold.
Unapproved/missing config returns 503, and no gateway is active by default.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.developer_keys import resolve_developer_key
from app.api.developer_prepaid import (
    ServerPrice, capture_verified, mark_provider_dispatched,
    metered_charge_paise, release_before_dispatch, reserve_prepaid,
)
from app.api.developer_provider_metering import extract_final_provider_usage

router = APIRouter(prefix="/v1", tags=["developer-inference"])
_MAX_PROMPT_BYTES = 24_000


class CompletionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=160)
    messages: list[CompletionMessage] = Field(min_length=1, max_length=16)
    max_tokens: int = Field(default=256, ge=1, le=1024)
    temperature: float = Field(default=0.3, ge=0, le=1)
    stream: Literal[False] = False


def _true(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("true", "1", "yes")


def _require_launch_approval() -> None:
    # Independent flags are mandatory; they do NOT automatically enable a
    # public gateway even if developer-key management is enabled.
    flags = (
        "INDOONE_DEVELOPER_KEYS_ENABLED",
        "INDOONE_B2B_GATEWAY_ENABLED",
        "INDOONE_B2B_RESALE_APPROVED",
        "INDOONE_B2B_BILLING_APPROVED",
    )
    if not all(_true(flag) for flag in flags):
        raise HTTPException(status_code=503, detail="Developer inference is not enabled")


def _approved_price(alias: str) -> ServerPrice:
    try:
        catalog = json.loads(os.getenv("INDOONE_B2B_APPROVED_MODEL_CATALOG_JSON", "{}"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Approved model catalog is not configured") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get(alias), dict):
        raise HTTPException(status_code=404, detail="Model is not available")
    entry = catalog[alias]
    try:
        if set(entry) != {"provider", "upstream_model", "input_rate_paise", "output_rate_paise"}:
            raise ValueError("invalid approved pricing attributes")
        price = ServerPrice(alias, entry["provider"], entry["upstream_model"],
                            entry["input_rate_paise"], entry["output_rate_paise"])
        price.validate()
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Approved model pricing is invalid") from exc
    return price


def _provider_key(price: ServerPrice) -> str:
    name = "GEMINI_API_KEY" if price.provider == "gemini" else "GROQ_API_KEY"
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail="Approved model provider is unavailable")
    return value


def _developer_identity(request: Request) -> dict[str, str]:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) > 256:
        raise HTTPException(status_code=401, detail="Developer bearer key required")
    key = auth.removeprefix("Bearer ").strip()
    record = resolve_developer_key(key)
    if record is None or record.get("environment") != "live":
        raise HTTPException(status_code=401, detail="Developer key invalid or revoked")
    return record


def _serialize_fingerprint(body: CompletionRequest) -> str:
    canonical = json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _max_reserve(body: CompletionRequest, price: ServerPrice) -> int:
    # Conserve a strict upper-bound budget, then settle from provider-reported
    # counts only. Unpredictable reasoning/tool/cache usage is unsupported.
    payload_bytes = len(json.dumps(body.model_dump(), ensure_ascii=False).encode("utf-8"))
    if payload_bytes > _MAX_PROMPT_BYTES:
        raise HTTPException(status_code=413, detail="Developer prompt exceeds byte budget")
    prompt_token_ceiling = payload_bytes + 1024
    amount = metered_charge_paise(
        prompt_token_ceiling, body.max_tokens,
        input_rate_paise=price.input_rate_paise,
        output_rate_paise=price.output_rate_paise,
    )
    if amount > 100_000:
        raise HTTPException(status_code=422, detail="Request exceeds single-call spending cap")
    return max(1, amount)


async def _request_provider(body: CompletionRequest, price: ServerPrice, secret: str) -> dict:
    if price.provider == "gemini":
        system = "\n".join(m.content for m in body.messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in body.messages if m.role != "system"
        ]
        if not contents:
            raise ValueError("user message required")
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": body.temperature, "maxOutputTokens": body.max_tokens,
                "candidateCount": 1,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        # Upstream model is restricted to vetted identifiers, never a user URL.
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,100}", price.upstream_model):
            raise ValueError("invalid upstream model identifier")
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + price.upstream_model + ":generateContent"
        headers = {"x-goog-api-key": secret, "Content-Type": "application/json"}
    else:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": price.upstream_model,
            "messages": [m.model_dump() for m in body.messages],
            "temperature": body.temperature,
            "max_tokens": body.max_tokens,
            "n": 1, "stream": False,
        }
        headers = {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=False) as client:
        answer = await client.post(url, headers=headers, json=payload)
    if answer.status_code != 200:
        raise RuntimeError("Upstream provider returned non-success status")
    parsed = answer.json()
    if not isinstance(parsed, dict):
        raise ValueError("Provider returned invalid response JSON")
    return parsed


def _answer_text(provider: str, data: dict) -> str:
    if provider == "gemini":
        parts = data["candidates"][0]["content"]["parts"]
        content = "".join(part["text"] for part in parts
                          if isinstance(part, dict) and part.get("thought") is not True
                          and isinstance(part.get("text"), str))
    else:
        content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Provider returned no final text")
    return content.strip()


@router.post("/chat/completions")
async def developer_chat_completions(
    request: Request, body: CompletionRequest, idempotency_key: str | None = Header(default=None),
) -> dict[str, object]:
    _require_launch_approval()
    identity = _developer_identity(request)
    if idempotency_key is None or not re.fullmatch(r"[A-Za-z0-9_-]{12,96}", idempotency_key):
        raise HTTPException(status_code=422, detail="A unique Idempotency-Key of 12–96 safe characters is required")
    if not any(message.role == "user" for message in body.messages):
        raise HTTPException(status_code=422, detail="At least one user message is required")

    price = _approved_price(body.model)
    secret = _provider_key(price)
    max_paise = _max_reserve(body, price)
    try:
        reservation, is_new = reserve_prepaid(
            owner_id=identity["owner_id"], key_id=identity["key_id"],
            client_request_id=idempotency_key, price=price,
            max_charge_paise=max_paise, request_sha256=_serialize_fingerprint(body),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    if not is_new:
        # Clients may retry a timed-out POST, but a retry NEVER pays for a
        # second generation. Returning 409 is safer than inventing an answer.
        raise HTTPException(status_code=409, detail="Request already reserved; do not re-dispatch")

    try:
        mark_provider_dispatched(reservation_id=reservation, key_id=identity["key_id"])
    except PermissionError as exc:
        # No request went upstream yet, so release is genuinely safe here.
        release_before_dispatch(reservation_id=reservation, provider_dispatch_started=False)
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        data = await _request_provider(body, price, secret)
        verified = extract_final_provider_usage(provider=price.provider,
            upstream_model=price.upstream_model, response=data)
        content = _answer_text(price.provider, data)
        charge_paise, created = capture_verified(
            reservation_id=reservation, provider=verified.provider,
            upstream_model=verified.upstream_model,
            upstream_response_id=verified.upstream_response_id,
            input_tokens=verified.input_tokens,
            output_tokens=verified.output_tokens, provider_reported=True,
        )
        if not created:
            raise RuntimeError("Duplicate provider evidence cannot generate another charge")
    except Exception as exc:
        # Never quietly release on timeout or ambiguous provider response.
        # Customer support/operator reconciliation is required; no phantom
        # success response or inferred token usage may be returned.
        raise HTTPException(
            status_code=502,
            detail="Provider response or token verification unavailable; no additional request was dispatched",
        ) from exc

    return {
        "id": reservation, "object": "chat.completion", "created": int(time.time()),
        "model": body.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": (
                         "length" if (data["candidates"][0]["finishReason"] == "MAX_TOKENS"
                                      if price.provider == "gemini"
                                      else data["choices"][0]["finish_reason"] == "length")
                         else "stop"
                     )}],
        "usage": {"prompt_tokens": verified.input_tokens,
                  "completion_tokens": verified.output_tokens,
                  "total_tokens": verified.input_tokens + verified.output_tokens},
        "charge_paise": charge_paise, "currency": "INR",
    }
