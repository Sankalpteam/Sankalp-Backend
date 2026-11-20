"""B2B prepaid preparation tests: no upstream calls, payment, or live inference."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.developer_prepaid import (
    ServerPrice,
    capture_verified,
    metered_charge_paise,
    mark_provider_dispatched,
    release_before_dispatch,
    reserve_prepaid,
    wallet_snapshot,
)
from app.main import app
from app.storage import database
from app.storage.migrations import ensure_schema, reset_for_tests

_PRICE = ServerPrice("sankalp-standard", "gemini", "gemini-test", 4000, 8000)


@pytest.fixture
def account(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'prepaid.sqlite3'}")
    monkeypatch.setenv("INDOONE_ENV", "development")
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INDOONE_DEVELOPER_KEYS_ENABLED", "true")
    reset_for_tests()
    ensure_schema()
    with database.connect() as db:
        db.execute(
            "INSERT INTO developer_api_keys "
            "(key_id, owner_id, name, environment, key_hash, key_prefix, key_last4, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("key-a", "alice", "Production", "live", "hash-a", "sk_sankalp_live_", "xxxx", "active", "now"),
        )
        db.execute(
            "INSERT INTO developer_api_keys "
            "(key_id, owner_id, name, environment, key_hash, key_prefix, key_last4, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("key-b", "bob", "Production", "live", "hash-b", "sk_sankalp_live_", "yyyy", "active", "now"),
        )
        db.execute(
            "INSERT INTO developer_prepaid_accounts "
            "(owner_id, available_paise, held_paise, updated_at) VALUES (?, ?, ?, ?)",
            ("alice", 1000, 0, "now"),
        )
    yield
    reset_for_tests()


def _reserve(request="unique-request", amount=100, key="key-a"):
    return reserve_prepaid(
        owner_id="alice", key_id=key, client_request_id=request,
        price=_PRICE, max_charge_paise=amount,
    )


def test_verified_capture_reconciles_integer_paise_exactly_once(account):
    reference, is_new = _reserve()
    assert is_new is True
    assert wallet_snapshot("alice")["available_paise"] == 900
    assert wallet_snapshot("alice")["held_paise"] == 100
    assert _reserve() == (reference, False)
    with pytest.raises(PermissionError, match="unverified"):
        capture_verified(
            reservation_id=reference, provider="gemini",
            upstream_model="gemini-test", input_tokens=125, output_tokens=250,
        )
    assert capture_verified(
        reservation_id=reference, provider="gemini",
        upstream_model="gemini-test", input_tokens=125, output_tokens=250,
        provider_reported=True,
    ) == (3, True)
    assert capture_verified(
        reservation_id=reference, provider="gemini",
        upstream_model="gemini-test", input_tokens=125, output_tokens=250,
        provider_reported=True,
    ) == (3, False)
    with pytest.raises(ValueError, match="conflicting"):
        capture_verified(
            reservation_id=reference, provider="gemini",
            upstream_model="gemini-test", input_tokens=125, output_tokens=251,
            provider_reported=True,
        )
    wallet = wallet_snapshot("alice")
    assert (wallet["available_paise"], wallet["held_paise"], wallet["pending_requests"]) == (997, 0, 0)
    assert wallet["payments_enabled"] is False
    assert wallet["paid_inference_enabled"] is False
    with database.connect() as db:
        evidence = db.execute(
            "SELECT COUNT(*) AS n FROM developer_verified_usage WHERE request_id = ?",
            (reference,),
        ).fetchone()
        assert evidence["n"] == 1


def test_replay_conflict_owner_scope_and_revoked_keys(account):
    reference, _ = _reserve()
    with pytest.raises(ValueError, match="idempotency key"):
        _reserve(amount=101)
    with pytest.raises(PermissionError, match="unavailable"):
        _reserve(request="other", key="key-b")
    with database.connect() as db:
        db.execute("UPDATE developer_api_keys SET status='revoked' WHERE key_id='key-a'")
    with pytest.raises(PermissionError, match="unavailable"):
        _reserve(request="after-revoke")
    assert wallet_snapshot("bob")["available_paise"] == 0
    assert wallet_snapshot("alice")["held_paise"] == 100


def test_timeout_remains_held_and_release_requires_no_dispatch(account):
    reference, _ = _reserve()
    with pytest.raises(PermissionError, match="cannot release"):
        release_before_dispatch(reservation_id=reference, provider_dispatch_started=True)
    with pytest.raises(ValueError, match="mismatch"):
        capture_verified(reservation_id=reference, provider="groq",
            upstream_model="gemini-test", input_tokens=1, output_tokens=1, provider_reported=True)
    with pytest.raises(PermissionError, match="cap"):
        capture_verified(reservation_id=reference, provider="gemini",
            upstream_model="gemini-test", input_tokens=0, output_tokens=2_000_000, provider_reported=True)
    assert wallet_snapshot("alice")["held_paise"] == 100
    assert release_before_dispatch(reservation_id=reference, provider_dispatch_started=False) is True
    assert release_before_dispatch(reservation_id=reference, provider_dispatch_started=False) is False
    assert wallet_snapshot("alice")["available_paise"] == 1000
    with pytest.raises(PermissionError, match="released"):
        capture_verified(reservation_id=reference, provider="gemini",
            upstream_model="gemini-test", input_tokens=1, output_tokens=1, provider_reported=True)


def test_reservation_cannot_overspend_under_concurrent_requests(account):
    def request(i):
        try:
            return _reserve(request=f"concurrent-{i}", amount=600)[1]
        except PermissionError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        admitted = list(pool.map(request, range(4)))
    assert admitted.count(True) == 1
    assert wallet_snapshot("alice")["available_paise"] == 400
    assert wallet_snapshot("alice")["held_paise"] == 600


def test_charge_validation_uses_nonnegative_integers_not_float(account):
    assert metered_charge_paise(125, 250, input_rate_paise=4000, output_rate_paise=8000) == 3
    for bad in (True, 1.2, -1):
        with pytest.raises(ValueError):
            metered_charge_paise(bad, 0, input_rate_paise=4000, output_rate_paise=8000)
    with pytest.raises(ValueError):
        ServerPrice("alias", "unknown", "model", 10, 10).validate()


def test_fingerprint_and_durable_dispatch_replay_protection(account):
    reference, created = reserve_prepaid(
        owner_id="alice", key_id="key-a", client_request_id="op-one",
        price=_PRICE, max_charge_paise=100, request_sha256="a" * 64,
    )
    assert created is True
    with pytest.raises(ValueError, match="idempotency"):
        reserve_prepaid(owner_id="alice", key_id="key-a", client_request_id="op-one",
            price=_PRICE, max_charge_paise=100, request_sha256="b" * 64)
    assert reserve_prepaid(owner_id="alice", key_id="key-a", client_request_id="op-one",
        price=_PRICE, max_charge_paise=100, request_sha256="a" * 64) == (reference, False)
    mark_provider_dispatched(reservation_id=reference, key_id="key-a")
    with pytest.raises(PermissionError, match="dispatch already"):
        mark_provider_dispatched(reservation_id=reference, key_id="key-a")
    with pytest.raises(PermissionError, match="persistent"):
        release_before_dispatch(reservation_id=reference, provider_dispatch_started=False)
    assert wallet_snapshot("alice")["held_paise"] == 100


def test_revocation_before_dispatch_blocks_provider_call(account):
    reference, _ = _reserve("will-revoke")
    with database.connect() as db:
        db.execute("UPDATE developer_api_keys SET status = 'revoked' WHERE key_id = 'key-a'")
    with pytest.raises(PermissionError, match="revoked"):
        mark_provider_dispatched(reservation_id=reference, key_id="key-a")
    assert release_before_dispatch(reservation_id=reference, provider_dispatch_started=False)
    assert wallet_snapshot("alice")["held_paise"] == 0
