"""Feature-gated developer API contract, against the real FastAPI middleware.

Never touches real provider APIs or merchant systems. Only internal SQL fixtures
supply temporary test credit; no production funding endpoint exists.
"""
from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import database
from app.storage.migrations import ensure_schema, reset_for_tests

RAW_KEY = "sk_sankalp_live_test_" + "a" * 32
CATALOG = (
    '{"sankalp-standard": {"provider":"gemini", '
    '"upstream_model":"gemini-test-version","input_rate_paise":4000,'
    '"output_rate_paise":8000}}'
)
BODY = {
    "model": "sankalp-standard",
    "messages": [{"role": "user", "content": "Reply with hello."}],
    "max_tokens": 64, "temperature": 0.3, "stream": False,
}
HEADERS = {
    "Authorization": f"Bearer {RAW_KEY}",
    "Idempotency-Key": "request-unique-0001",
}


@pytest.fixture
def isolated_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'gateway.sqlite3'}")
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("INDOONE_APPROVAL_SECRET", "b" * 32)
    monkeypatch.setenv("INDOONE_DEVELOPER_KEY_HASH_SECRET", "k" * 32)
    monkeypatch.setenv("INDOONE_DEVELOPER_KEYS_ENABLED", "true")
    monkeypatch.setenv("INDOONE_B2B_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("INDOONE_B2B_RESALE_APPROVED", "false")
    monkeypatch.setenv("INDOONE_B2B_BILLING_APPROVED", "false")
    monkeypatch.setenv("INDOONE_B2B_APPROVED_MODEL_CATALOG_JSON", CATALOG)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-provider-key")
    reset_for_tests()
    ensure_schema()
    digest = hmac.new(b"k" * 32, RAW_KEY.encode(), hashlib.sha256).hexdigest()
    with database.connect() as db:
        db.execute(
            "INSERT INTO developer_api_keys "
            "(key_id, owner_id, name, environment, key_hash, key_prefix, key_last4, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("key-one", "owner-one", "Test live key", "live", digest, "sk_sankalp_live_", "aaaa", "active", "now"),
        )
        db.execute(
            "INSERT INTO developer_prepaid_accounts "
            "(owner_id, available_paise, held_paise, updated_at) VALUES (?, ?, ?, ?)",
            ("owner-one", 1000, 0, "now"),
        )
    yield
    reset_for_tests()


def _enable(monkeypatch):
    monkeypatch.setenv("INDOONE_B2B_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("INDOONE_B2B_RESALE_APPROVED", "true")
    monkeypatch.setenv("INDOONE_B2B_BILLING_APPROVED", "true")


def _provider_response(response_id="gemini-upstream-001"):
    return {
        "modelVersion": "gemini-test-version", "responseId": response_id,
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Hello!"}]}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20,
                          "totalTokenCount": 120},
    }


def test_gateway_is_off_by_default_and_never_reuses_user_credentials(isolated_gateway):
    with TestClient(app) as client:
        blocked = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert blocked.status_code == 503
        assert "not enabled" in blocked.text


def test_gateway_owner_key_and_prompt_scope_enforced(isolated_gateway, monkeypatch):
    _enable(monkeypatch)
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json=BODY).status_code == 401
        assert client.post("/v1/chat/completions", json=BODY,
            headers={"Authorization": "Bearer not-an-api-key", "Idempotency-Key": "request-unique-0002"}).status_code == 401
        assert client.post("/v1/chat/completions", json=BODY,
            headers={"Authorization": f"Bearer {RAW_KEY}"}).status_code == 422
        assert client.get("/api/developers/status", headers={"Authorization": f"Bearer {RAW_KEY}"}).status_code == 401
        assert client.post("/v1/chat/completions", json={**BODY, "tools": [{"type":"function"}]},
            headers=HEADERS).status_code == 422
        assert client.post("/v1/chat/completions", json={**BODY, "stream": True},
            headers=HEADERS).status_code == 422


def test_one_provider_dispatch_and_one_verified_charge_on_client_retry(
    isolated_gateway, monkeypatch,
):
    _enable(monkeypatch)
    from app.api import developer_inference
    upstream = AsyncMock(return_value=_provider_response())
    monkeypatch.setattr(developer_inference, "_request_provider", upstream)
    with TestClient(app) as client:
        first = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["model"] == "sankalp-standard"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["usage"] == {
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        }
        assert result["charge_paise"] == 1
        assert result["currency"] == "INR"
        second = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert second.status_code == 409
        reused_payload = client.post("/v1/chat/completions",
            json={**BODY, "messages": [{"role": "user", "content": "Different prompt"}]},
            headers=HEADERS)
        assert reused_payload.status_code == 409
    assert upstream.await_count == 1
    with database.connect() as db:
        account = db.execute("SELECT available_paise, held_paise FROM developer_prepaid_accounts "
            "WHERE owner_id = 'owner-one'").fetchone()
        assert (account["available_paise"], account["held_paise"]) == (999, 0)
        events = db.execute("SELECT COUNT(*) AS n FROM developer_verified_usage").fetchone()
        assert events["n"] == 1


def test_unverified_model_response_never_charges_and_hold_is_not_released(
    isolated_gateway, monkeypatch,
):
    _enable(monkeypatch)
    from app.api import developer_inference
    response = _provider_response()
    response["modelVersion"] = "unapproved-fallback"
    upstream = AsyncMock(return_value=response)
    monkeypatch.setattr(developer_inference, "_request_provider", upstream)
    with TestClient(app) as client:
        result = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert result.status_code == 502, result.text
        again = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert again.status_code == 409
    assert upstream.await_count == 1
    with database.connect() as db:
        account = db.execute("SELECT available_paise, held_paise FROM developer_prepaid_accounts "
            "WHERE owner_id = 'owner-one'").fetchone()
        assert account["held_paise"] > 0
        assert db.execute("SELECT COUNT(*) AS n FROM developer_verified_usage").fetchone()["n"] == 0


def test_no_dispatch_when_verified_funds_missing(isolated_gateway, monkeypatch):
    _enable(monkeypatch)
    from app.api import developer_inference
    upstream = AsyncMock(return_value=_provider_response())
    monkeypatch.setattr(developer_inference, "_request_provider", upstream)
    with database.connect() as db:
        db.execute("UPDATE developer_prepaid_accounts SET available_paise = 0 WHERE owner_id = 'owner-one'")
    with TestClient(app) as client:
        result = client.post("/v1/chat/completions", json=BODY, headers=HEADERS)
        assert result.status_code == 402
    assert upstream.await_count == 0
