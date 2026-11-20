"""Real upstream AsyncPostgresSaver interrupt/resume on CI's service database."""
from __future__ import annotations

import asyncio
import os

import pytest

from app.ai.execution import langgraph_approval
from app.capabilities.connectors import store
from app.storage.migrations import reset_for_tests


def test_official_postgres_checkpoint_human_deny(monkeypatch):
    url = os.getenv("INDOONE_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("requires the isolated CI PostgreSQL test service")
    monkeypatch.setenv("INDOONE_DATABASE_URL", url)
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "true")
    reset_for_tests()
    step = {
        "index": 1,
        "tool": "github_delete_branch",
        "payload": '{"repository":"example/repo","branch":"obsolete"}',
        "requires_approval": True,
    }
    run_id = store.create_agent_run("pg-checkpoint-user", "delete obsolete branch")["id"]
    store.update_agent_run(
        "pg-checkpoint-user", run_id, status="blocked", steps=[], results=[],
        blocked_steps=[step], retry_counts=[],
    )
    assert asyncio.run(langgraph_approval.stage("pg-checkpoint-user", run_id, step))
    assert asyncio.run(langgraph_approval._checkpoint_pending(
        "pg-checkpoint-user", run_id, langgraph_approval._step_hash(step)
    ))
    assert asyncio.run(langgraph_approval.decide(
        "pg-checkpoint-user", run_id, approve=False
    )) == step
    assert store.get_agent_run("pg-checkpoint-user", run_id)["status"] == "denied"
    with pytest.raises(langgraph_approval.ApprovalConflict):
        asyncio.run(langgraph_approval.decide("pg-checkpoint-user", run_id, approve=True))
