"""Durable approval claims: owner isolation, one-time dispatch, fail-closed replay."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.ai.execution import durable_approval
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
    run = store.create_agent_run("alice", "delete the stale feature branch")
    run_id = run["id"]
    store.update_agent_run(
        "alice", run_id, status="blocked", steps=[], results=[],
        blocked_steps=[dict(WRITE)], retry_counts=[],
    )
    return run_id


def test_stage_and_deny_are_owned_and_one_time(owned_run):
    assert durable_approval.stage("alice", owned_run, dict(WRITE)) is True
    assert durable_approval.stage("alice", owned_run, dict(WRITE)) is False
    assert durable_approval.get("bob", owned_run) is None
    pending = durable_approval.get("alice", owned_run)
    assert pending is not None
    assert pending["status"] == "pending"
    assert pending["tool"] == "github_delete_branch"
    with pytest.raises(LookupError):
        durable_approval.decide("bob", owned_run, approve=True)
    assert durable_approval.decide("alice", owned_run, approve=False) == WRITE
    assert store.get_agent_run("alice", owned_run)["status"] == "denied"
    with pytest.raises(durable_approval.ApprovalConflict):
        durable_approval.decide("alice", owned_run, approve=True)


def test_claim_is_persisted_before_execution_and_cannot_be_replayed(owned_run):
    assert durable_approval.stage("alice", owned_run, dict(WRITE))
    step = durable_approval.decide("alice", owned_run, approve=True)
    assert step == WRITE
    assert store.get_agent_run("alice", owned_run)["status"] == "approval_claimed"
    # Simulate a process dying after the provider receives a request.
    with pytest.raises(durable_approval.ApprovalConflict):
        durable_approval.decide("alice", owned_run, approve=True)
    outcome = {
        "name": "github_delete_branch",
        "output": "verified",
        "safe": True,
        "retryable": False,
        "truncated": False,
    }
    assert durable_approval.finish(
        "alice", owned_run, steps=[step], results=[outcome],
        blocked_steps=[], retry_counts=[0],
    ) == "completed"
    assert store.get_agent_run("alice", owned_run)["results"] == [outcome]
    with pytest.raises(durable_approval.ApprovalConflict):
        durable_approval.finish(
            "alice", owned_run, steps=[step], results=[outcome],
            blocked_steps=[], retry_counts=[0],
        )


def test_unverified_provider_outcome_needs_manual_review(owned_run):
    durable_approval.stage("alice", owned_run, dict(WRITE))
    step = durable_approval.decide("alice", owned_run, approve=True)
    assert durable_approval.finish(
        "alice", owned_run, steps=[step],
        results=[{"name": step["tool"], "safe": False, "output": "timeout"}],
        blocked_steps=[], retry_counts=[0],
    ) == "needs_review"
    assert store.get_agent_run("alice", owned_run)["status"] == "needs_review"
    with pytest.raises(durable_approval.ApprovalConflict):
        durable_approval.decide("alice", owned_run, approve=True)


def test_expired_approval_never_claims_write(owned_run, monkeypatch):
    durable_approval.stage("alice", owned_run, dict(WRITE))
    advanced = durable_approval._utcnow() + timedelta(seconds=301)
    monkeypatch.setattr(durable_approval, "_utcnow", lambda: advanced)
    assert durable_approval.get("alice", owned_run)["status"] == "expired"
    with pytest.raises(durable_approval.ApprovalExpired):
        durable_approval.decide("alice", owned_run, approve=True)
    assert store.get_agent_run("alice", owned_run)["status"] == "blocked"


def test_mutated_saved_action_fails_closed(owned_run):
    durable_approval.stage("alice", owned_run, dict(WRITE))
    modified = {**WRITE, "payload": '{"repository":"org/repo","branch":"main"}'}
    store.update_agent_run(
        "alice", owned_run, status="awaiting_approval", steps=[], results=[],
        blocked_steps=[modified], retry_counts=[],
    )
    assert durable_approval.get("alice", owned_run) is None
    with pytest.raises(durable_approval.ApprovalConflict):
        durable_approval.decide("alice", owned_run, approve=True)
    assert store.get_agent_run("alice", owned_run)["status"] == "needs_review"


def test_multi_step_and_read_only_actions_are_not_staged(owned_run):
    run = store.get_agent_run("alice", owned_run)
    assert run is not None
    other = {**WRITE, "index": 2}
    store.update_agent_run(
        "alice", owned_run, status="blocked", steps=[], results=[],
        blocked_steps=[WRITE, other], retry_counts=[],
    )
    assert durable_approval.stage("alice", owned_run, dict(WRITE)) is False
    assert durable_approval.get("alice", owned_run) is None
