"""Legacy approval transport safety while migrating to official LangGraph.

These are intentionally **not** a homegrown replacement for LangGraph HITL.
They test the small, still-required idempotency guard around existing
CONFIRM/CANCEL pending records before an external GitHub or mail write.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.capabilities.connectors import store
from app.plugins.email import email_assistant
from app.plugins.github import chat_tools
from app.ai.execution.tools import ToolResult


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("INDOONE_DATABASE_URL", raising=False)
    monkeypatch.setenv("INDOONE_CAPABILITY_DB", str(tmp_path / "pending.sqlite3"))
    monkeypatch.setenv("INDOONE_OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("INDOONE_APPROVAL_SECRET", "test-secret-with-enough-length-for-hmac-approvals")
    store.initialize()


def _expiration(seconds: float = 300.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _save(user="alice", conversation="thread-1", provider="zoho_mail", action="send_email", payload=b"encrypted-a"):
    store.save_pending_email_action(user, conversation, provider, action, payload, _expiration())
    pending = store.get_pending_email_action(user, conversation)
    assert pending is not None
    return pending


def test_one_atomic_claim_for_eight_simultaneous_confirms():
    pending = _save()
    start = threading.Barrier(8)

    def attempt():
        start.wait(timeout=5)
        return store.consume_pending_email_action(
            "alice", "thread-1", pending, integration_id="zoho_mail"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(lambda _: attempt(), range(8)))
    assert sum(x is not None for x in attempts) == 1
    assert store.get_pending_email_action("alice", "thread-1") is None


def test_owner_and_conversation_isolation():
    pending = _save()
    assert store.consume_pending_email_action("bob", "thread-1", pending) is None
    assert store.consume_pending_email_action("alice", "another-thread", pending) is None
    assert store.consume_pending_email_action(
        "alice", "thread-1", pending, integration_id="github"
    ) is None
    assert store.consume_pending_github_action("alice", "thread-1", pending) is None
    assert store.get_pending_email_action("alice", "thread-1") is not None
    assert store.consume_pending_email_action(
        "alice", "thread-1", pending, integration_id="zoho_mail"
    ) is not None


def test_old_proposal_cannot_consume_replacement():
    old = _save(payload=b"encrypted-before")
    replacement = _save(payload=b"encrypted-after")
    assert store.consume_pending_email_action("alice", "thread-1", old) is None
    assert store.get_pending_email_action("alice", "thread-1")["encrypted_payload"] == b"encrypted-after"
    assert store.consume_pending_email_action("alice", "thread-1", replacement) is not None


def test_expired_after_preview_never_claimable():
    store.save_pending_email_action(
        "alice", "thread-1", "zoho_mail", "send_email", b"encrypted",
        _expiration(0.035),
    )
    pending = store.get_pending_email_action("alice", "thread-1")
    assert pending is not None
    time.sleep(0.08)
    assert store.consume_pending_email_action("alice", "thread-1", pending) is None
    assert store.get_pending_email_action("alice", "thread-1") is None


def test_github_claim_checks_integration_and_exact_action():
    pending = _save(provider="github", action="github_write", payload=b"encrypted-github")
    assert store.consume_pending_github_action("alice", "thread-1", pending) is not None
    assert store.consume_pending_github_action("alice", "thread-1", pending) is None


def test_github_two_confirmations_only_one_dispatches(monkeypatch):
    async def prepare():
        result = await chat_tools._execute_github_tool(
            "alice", "github_delete_branch",
            {"repository": "o/r", "branch": "old", "conversation_id": "thread-1"},
        )
        assert result["code"] == "APPROVAL_REQUIRED"

    asyncio.run(prepare())
    pending = store.get_pending_github_action("alice", "thread-1")
    assert pending is not None
    ready = 0
    gate = asyncio.Event()
    dispatched = []

    async def simultaneous_confirmation(text):
        nonlocal ready
        ready += 1
        if ready == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=5)
        return "confirm"

    async def fake_execute(user, tool, arguments, **kwargs):
        dispatched.append((user, tool, arguments))
        await asyncio.sleep(0.02)
        return {"ok": True, "result": {"branch": "old"}}

    monkeypatch.setattr(chat_tools, "classify_reply_async", simultaneous_confirmation)
    monkeypatch.setattr(chat_tools, "_execute_github_tool", fake_execute)

    async def race():
        return await asyncio.gather(*[
            chat_tools.handle_github_chat_with_tools(
                "alice", "CONFIRM", conversation_id="thread-1"
            ) for _ in range(2)
        ])

    outputs = asyncio.run(race())
    assert len(dispatched) == 1
    assert dispatched[0][0:2] == ("alice", "github_delete_branch")
    assert "approval_token" in dispatched[0][2]
    assert any("already" in str(value).lower() for value in outputs)
    assert store.get_pending_github_action("alice", "thread-1") is None


def test_email_two_confirms_only_one_dispatches(monkeypatch):
    email_assistant._save_pending(
        "alice", "thread-1", "zoho_mail", "send_email",
        {"to": "recipient@example.org", "subject": "Test", "body": "Hello"},
    )
    ready = 0
    gate = asyncio.Event()
    dispatched = []

    async def simultaneous_confirmation(text):
        nonlocal ready
        ready += 1
        if ready == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=5)
        return "confirm"

    async def fake_execute(user, provider, kind, payload):
        dispatched.append((user, provider, kind, payload["to"]))
        await asyncio.sleep(0.02)
        return "Email sent."

    monkeypatch.setattr(email_assistant, "classify_reply_async", simultaneous_confirmation)
    monkeypatch.setattr(email_assistant, "_execute_pending", fake_execute)

    async def race():
        return await asyncio.gather(*[
            email_assistant.handle_email_chat("alice", "thread-1", "CONFIRM")
            for _ in range(2)
        ])

    outputs = asyncio.run(race())
    assert dispatched == [("alice", "zoho_mail", "send_email", "recipient@example.org")]
    assert any("already" in str(value).lower() for value in outputs)
    assert store.get_pending_email_action("alice", "thread-1") is None


def test_email_cancel_and_confirm_cannot_both_win(monkeypatch):
    email_assistant._save_pending(
        "alice", "thread-1", "zoho_mail", "send_email",
        {"to": "recipient@example.org", "subject": "Test", "body": "Hello"},
    )
    ready = 0
    gate = asyncio.Event()
    dispatched = []

    async def simultaneous_decision(text):
        nonlocal ready
        ready += 1
        if ready == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=5)
        return "confirm" if text == "CONFIRM" else "cancel"

    async def fake_execute(*args):
        dispatched.append(args)
        return "Email sent."

    monkeypatch.setattr(email_assistant, "classify_reply_async", simultaneous_decision)
    monkeypatch.setattr(email_assistant, "_execute_pending", fake_execute)

    async def race():
        return await asyncio.gather(
            email_assistant.handle_email_chat("alice", "thread-1", "CONFIRM"),
            email_assistant.handle_email_chat("alice", "thread-1", "CANCEL"),
        )

    outputs = asyncio.run(race())
    assert sum("Email sent." == output for output in outputs) + sum(
        str(output).startswith("Cancelled.") for output in outputs
    ) == 1
    assert len(dispatched) <= 1
    assert store.get_pending_email_action("alice", "thread-1") is None


def test_email_handler_never_claims_github_pending(monkeypatch):
    pending = _save(provider="github", action="github_write", payload=b"encrypted-github")
    assert asyncio.run(email_assistant.handle_email_chat("alice", "thread-1", "CONFIRM")) is None
    assert store.get_pending_github_action("alice", "thread-1") is not None


def test_stale_github_cancel_cannot_delete_newer_email_proposal(monkeypatch):
    """Old get()+unqualified delete() could erase a new provider's action."""
    old_github = _save(
        provider="github", action="github_write", payload=b"old-github-proposal"
    )
    new_email = _save(
        provider="zoho_mail", action="send_email", payload=b"new-email-proposal"
    )
    # Simulate a GET that returned a Github snapshot before another request
    # replaced that same user's conversation slot with a mail proposal.
    monkeypatch.setattr(
        store, "get_pending_github_action",
        lambda user, conversation: old_github,
    )
    assert store.delete_pending_github_action("alice", "thread-1") is False
    assert store.get_pending_email_action("alice", "thread-1") == new_email


def test_same_user_older_snapshot_invalid_even_with_same_ciphertext():
    old = _save(payload=b"same-ciphertext")
    # A fresh proposal with the same payload still represents a new user
    # intent; the created_at/expiry must also match the reviewed record.
    store.save_pending_email_action(
        "alice", "thread-1", "zoho_mail", "send_email",
        b"same-ciphertext", _expiration(120),
    )
    new = store.get_pending_email_action("alice", "thread-1")
    assert new is not None and new["expires_at"] != old["expires_at"]
    assert store.consume_pending_email_action("alice", "thread-1", old) is None
    assert store.consume_pending_email_action("alice", "thread-1", new) is not None
