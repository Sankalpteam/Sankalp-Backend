"""Real official LangGraph checkpoint interrupt/resume, not a simulated planner."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.ai.execution import langgraph_approval as approval
from app.capabilities.connectors import store
from app.storage.migrations import reset_for_tests

WRITE = {
    "index": 1,
    "tool": "github_delete_branch",
    "payload": '{"repository":"org/repo","branch":"stale-feature"}',
    "requires_approval": True,
}


@pytest.fixture
def owned_run(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'approval.sqlite3'}")
    reset_for_tests()
    run_id = store.create_agent_run("alice", "delete the stale branch")["id"]
    store.update_agent_run(
        "alice", run_id, status="blocked", steps=[], results=[],
        blocked_steps=[WRITE], retry_counts=[],
    )
    return run_id


def test_upstream_interrupt_survives_new_checkpoint_connection(owned_run):
    assert asyncio.run(approval.stage("alice", owned_run, WRITE))
    assert asyncio.run(approval._checkpoint_pending("alice", owned_run, approval._step_hash(WRITE)))
    assert not asyncio.run(approval.stage("alice", owned_run, WRITE))
    assert approval.get("bob", owned_run) is None
    assert approval.get("alice", owned_run)["status"] == "pending"
    with pytest.raises(LookupError):
        asyncio.run(approval.decide("bob", owned_run, approve=True))
    assert asyncio.run(approval.decide("alice", owned_run, approve=False)) == WRITE
    assert store.get_agent_run("alice", owned_run)["status"] == "denied"
    with pytest.raises(approval.ApprovalConflict):
        asyncio.run(approval.decide("alice", owned_run, approve=True))


def test_official_command_resume_and_claim_are_not_replayable(owned_run):
    asyncio.run(approval.stage("alice", owned_run, WRITE))
    step = asyncio.run(approval.decide("alice", owned_run, approve=True))
    assert step == WRITE
    assert store.get_agent_run("alice", owned_run)["status"] == "approval_claimed"
    assert not asyncio.run(approval._checkpoint_pending("alice", owned_run, approval._step_hash(WRITE)))
    # Simulate a crash after the claimed write may have reached the provider.
    with pytest.raises(approval.ApprovalConflict):
        asyncio.run(approval.decide("alice", owned_run, approve=True))
    result = {"name": WRITE["tool"], "output": "verified", "safe": True}
    assert approval.finish(
        "alice", owned_run, steps=[step], results=[result],
        blocked_steps=[], retry_counts=[0],
    ) == "completed"
    assert store.get_agent_run("alice", owned_run)["results"] == [result]
    with pytest.raises(approval.ApprovalConflict):
        approval.finish(
            "alice", owned_run, steps=[step], results=[result],
            blocked_steps=[], retry_counts=[0],
        )
    assert approval.get("alice", owned_run)["status"] == "completed"


def test_uncertain_outcome_is_not_a_retry_grant(owned_run):
    asyncio.run(approval.stage("alice", owned_run, WRITE))
    step = asyncio.run(approval.decide("alice", owned_run, approve=True))
    assert approval.finish(
        "alice", owned_run, steps=[step],
        results=[{"name": step["tool"], "output": "timeout", "safe": False}],
        blocked_steps=[], retry_counts=[0],
    ) == "needs_review"
    with pytest.raises(approval.ApprovalConflict):
        asyncio.run(approval.decide("alice", owned_run, approve=True))


def test_expiry_fails_closed_without_resuming_graph(owned_run, monkeypatch):
    asyncio.run(approval.stage("alice", owned_run, WRITE))
    monkeypatch.setattr(approval, "_utcnow", lambda: approval._expires(
        store.get_agent_run("alice", owned_run)
    ) + timedelta(seconds=1))
    assert approval.get("alice", owned_run)["status"] == "expired"
    with pytest.raises(approval.ApprovalExpired):
        asyncio.run(approval.decide("alice", owned_run, approve=True))
    assert store.get_agent_run("alice", owned_run)["status"] == "blocked"


def test_tampered_record_does_not_match_saved_langgraph_checkpoint(owned_run):
    asyncio.run(approval.stage("alice", owned_run, WRITE))
    altered = {**WRITE, "payload": '{"repository":"org/repo","branch":"main"}'}
    store.update_agent_run(
        "alice", owned_run, status="awaiting_approval", steps=[], results=[],
        blocked_steps=[altered], retry_counts=[],
    )
    with pytest.raises(approval.ApprovalConflict, match="checkpoint"):
        asyncio.run(approval.decide("alice", owned_run, approve=True))
    assert store.get_agent_run("alice", owned_run)["status"] == "awaiting_approval"


def test_multi_step_or_read_does_not_create_upstream_checkpoint(owned_run):
    store.update_agent_run(
        "alice", owned_run, status="blocked", steps=[], results=[],
        blocked_steps=[WRITE, {**WRITE, "index": 2}], retry_counts=[],
    )
    assert not asyncio.run(approval.stage("alice", owned_run, WRITE))
    assert not asyncio.run(approval._checkpoint_pending("alice", owned_run, approval._step_hash(WRITE)))


def test_flag_off_stages_nothing(owned_run, monkeypatch):
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "false")
    assert not asyncio.run(approval.stage("alice", owned_run, WRITE))
    assert approval.get("alice", owned_run) is None
