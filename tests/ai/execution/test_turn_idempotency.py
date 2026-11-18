"""Atomic and durable admission for one chat turn across transport endpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.ai.execution.turn_idempotency import claim_turn, fingerprint, mark_turn_replied, turn_status
from app.storage.migrations import reset_for_tests


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOONE_CAPABILITY_DB", str(tmp_path / "chat-turns.db"))
    monkeypatch.delenv("INDOONE_DATABASE_URL", raising=False)
    monkeypatch.delenv("INDOONE_MIGRATE_ON_START", raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


def _claim(user="u1", turn="turn-client-001", message="send email"):
    return claim_turn(user, turn, message=message, conversation_id="c1")


def test_identical_turn_cannot_be_submitted_twice():
    assert _claim() == "new"
    assert _claim() == "duplicate"


def test_different_message_cannot_reuse_an_id():
    assert _claim() == "new"
    assert _claim(message="delete all email") == "conflict"


def test_same_id_belongs_to_each_user_separately():
    assert _claim("user-a") == "new"
    assert _claim("user-b") == "new"
    assert _claim("user-a") == "duplicate"


def test_a_claim_survives_a_new_database_connection():
    assert _claim() == "new"
    assert turn_status("u1", "turn-client-001") == "received"
    assert _claim() == "duplicate"
    mark_turn_replied("u1", "turn-client-001")
    assert turn_status("u1", "turn-client-001") == "replied"
    assert _claim() == "duplicate"


def test_other_users_cannot_read_claim_status():
    assert _claim() == "new"
    assert turn_status("someone-else", "turn-client-001") is None


def test_concurrent_requests_have_only_one_winner():
    def attempt(_):
        return _claim(turn="shared-race-002")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("new") == 1
    assert results.count("duplicate") == 7


def test_turn_fingerprint_hides_plaintext_message():
    hashed = fingerprint("secret email message", "conversation", None)
    assert len(hashed) == 64
    assert "secret" not in hashed
