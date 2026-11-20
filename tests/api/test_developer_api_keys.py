"""Development-only B2B key management and verified token-usage regression tests.

Runs through the real FastAPI authentication middleware and SQLite migrations,
but NEVER calls an upstream LLM, external OAuth provider, or payment gateway.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app.api.developer_keys import record_verified_usage, resolve_developer_key
from app.main import app
from app.storage import database
from app.storage.migrations import reset_for_tests

_AUTH_SECRET = "a" * 32
_KEY_SECRET = "k" * 32


def _base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _auth(user: str) -> dict[str, str]:
    principal = _base64(user.encode("utf-8"))
    timestamp = _base64(str(int(time.time())).encode("ascii"))
    unsigned = f"{principal}.{timestamp}".encode("ascii")
    signature = _base64(hmac.new(_AUTH_SECRET.encode("ascii"), unsigned, hashlib.sha256).digest())
    return {"Authorization": f"Bearer {principal}.{timestamp}.{signature}"}


@pytest.fixture
def isolated_developer_db(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'developers.sqlite3'}")
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INDOONE_AUTH_SECRET", _AUTH_SECRET)
    monkeypatch.setenv("INDOONE_APPROVAL_SECRET", "b" * 32)
    monkeypatch.setenv("INDOONE_DEVELOPER_KEY_HASH_SECRET", _KEY_SECRET)
    monkeypatch.setenv("INDOONE_DEVELOPER_KEYS_ENABLED", "true")
    monkeypatch.setenv("INDOONE_ENV", "development")
    reset_for_tests()
    yield
    reset_for_tests()


def test_developer_keys_are_one_time_owner_scoped_and_revocable(isolated_developer_db):
    with TestClient(app) as client:
        status = client.get("/api/developers/status", headers=_auth("alice"))
        assert status.status_code == 200
        assert status.json()["key_management_enabled"] is True
        assert status.json()["paid_inference_enabled"] is False
        assert status.json()["billing_enabled"] is False

        created = client.post(
            "/api/developers/keys",
            headers=_auth("alice"),
            json={"name": "My test server", "environment": "test"},
        )
        assert created.status_code == 201, created.text
        one_time_key = created.json()["key"]
        key_id = created.json()["record"]["id"]
        assert one_time_key.startswith("sk_sankalp_test_")
        assert resolve_developer_key(one_time_key) == {
            "owner_id": "alice", "key_id": key_id, "environment": "test"
        }

        listing = client.get("/api/developers/keys", headers=_auth("alice"))
        assert listing.status_code == 200
        assert len(listing.json()["keys"]) == 1
        assert listing.json()["keys"][0]["id"] == key_id
        assert listing.json()["keys"][0]["display"].endswith(one_time_key[-4:])
        assert one_time_key not in listing.text

        with database.connect() as db:
            row = db.execute(
                "SELECT key_hash FROM developer_api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
            assert row["key_hash"] != one_time_key
            assert len(row["key_hash"]) == 64

        other_listing = client.get("/api/developers/keys", headers=_auth("bob"))
        assert other_listing.status_code == 200
        assert other_listing.json()["keys"] == []
        assert client.delete(
            f"/api/developers/keys/{key_id}", headers=_auth("bob")
        ).status_code == 404
        assert resolve_developer_key(one_time_key) is not None

        assert client.delete(
            f"/api/developers/keys/{key_id}", headers=_auth("alice")
        ).status_code == 200
        assert resolve_developer_key(one_time_key) is None
        assert client.get("/api/developers/keys", headers=_auth("alice")).json()["keys"][0]["status"] == "revoked"


def test_provider_only_usage_is_deduplicated_and_owner_scoped(isolated_developer_db):
    with TestClient(app) as client:
        created = client.post(
            "/api/developers/keys",
            headers=_auth("alice"),
            json={"name": "Server", "environment": "live"},
        )
        assert created.status_code == 201
        key_id = created.json()["record"]["id"]
        params = dict(
            key_id=key_id, request_id="provider-request-1",
            model="gemini-test", provider="gemini",
            input_tokens=123, output_tokens=45,
        )
        with pytest.raises(PermissionError, match="Unverified"):
            record_verified_usage(**params)
        with pytest.raises(ValueError, match="nonnegative"):
            record_verified_usage(**{**params, "input_tokens": -1}, provider_reported=True)
        with pytest.raises(ValueError, match="Unsupported"):
            record_verified_usage(**{**params, "provider": "unknown"}, provider_reported=True)

        assert record_verified_usage(**params, provider_reported=True) is True
        assert record_verified_usage(**params, provider_reported=True) is False
        usage = client.get("/api/developers/usage", headers=_auth("alice"))
        assert usage.status_code == 200
        assert usage.json() == {
            "requests": 1, "input_tokens": 123, "output_tokens": 45,
            "billing_enabled": False, "currency": None, "amount_due": None,
        }
        other = client.get("/api/developers/usage", headers=_auth("bob"))
        assert other.status_code == 200
        assert other.json()["requests"] == 0

        client.delete(f"/api/developers/keys/{key_id}", headers=_auth("alice"))
        with pytest.raises(PermissionError, match="revoked"):
            record_verified_usage(
                **{**params, "request_id": "provider-request-2"}, provider_reported=True
            )


def test_keys_are_fail_closed_without_flags_secrets_or_login(isolated_developer_db, monkeypatch):
    with TestClient(app) as client:
        anonymous = client.get("/api/developers/status")
        assert anonymous.status_code == 401

        monkeypatch.setenv("INDOONE_DEVELOPER_KEYS_ENABLED", "false")
        assert client.get("/api/developers/keys", headers=_auth("alice")).status_code == 503
        assert client.post(
            "/api/developers/keys", headers=_auth("alice"),
            json={"name": "Denied", "environment": "test"},
        ).status_code == 503

        monkeypatch.setenv("INDOONE_DEVELOPER_KEYS_ENABLED", "true")
        monkeypatch.delenv("INDOONE_DEVELOPER_KEY_HASH_SECRET", raising=False)
        rejected = client.post(
            "/api/developers/keys", headers=_auth("alice"),
            json={"name": "Missing server key", "environment": "test"},
        )
        assert rejected.status_code == 503
        assert client.get("/api/developers/keys", headers=_auth("alice")).json()["keys"] == []
        assert resolve_developer_key("sk_sankalp_test_invalid") is None
