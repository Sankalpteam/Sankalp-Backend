"""Atomic one-time legacy consent claim on CI's real PostgreSQL service."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.capabilities.connectors import store
from app.storage.migrations import reset_for_tests


def test_postgres_owner_scoped_claim_serializes_parallel_confirms(monkeypatch):
    url = os.getenv("INDOONE_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("isolated PostgreSQL CI service required")
    monkeypatch.setenv("INDOONE_DATABASE_URL", url)
    reset_for_tests()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    store.save_pending_email_action(
        "pg-legacy-approval-owner", "pg-conversation-1", "zoho_mail",
        "send_email", b"encrypted-test-fixture", expires,
    )
    snapshot = store.get_pending_email_action(
        "pg-legacy-approval-owner", "pg-conversation-1"
    )
    assert snapshot is not None
    gate = threading.Barrier(4)

    def consume(index):
        gate.wait(timeout=10)
        user = "another-user" if index == 0 else "pg-legacy-approval-owner"
        return store.consume_pending_email_action(
            user, "pg-conversation-1", snapshot, integration_id="zoho_mail"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(consume, range(4)))
    assert results[0] is None  # Cross-account cannot steal a proposal.
    assert sum(item is not None for item in results) == 1
    assert store.get_pending_email_action(
        "pg-legacy-approval-owner", "pg-conversation-1"
    ) is None
