"""HTTP contract for opt-in one-time approval and authenticated resume."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app.ai.agents.agent import AgentStep
from app.ai.execution.approval import ApprovalDecision, validate_approval_token
from app.ai.execution.tools import ToolResult
from app.api.chat import agent_async as agent_route
from app.main import app
from app.storage.migrations import reset_for_tests


client = TestClient(app)
WRITE = AgentStep(
    1, "github_delete_branch",
    '{"repository":"org/repo","branch":"old-feature"}',
    True,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _auth(user: str) -> dict[str, str]:
    subject = _b64(user.encode())
    timestamp = _b64(str(int(time.time())).encode())
    message = f"{subject}.{timestamp}".encode()
    signature = _b64(hmac.new(b"x" * 32, message, hashlib.sha256).digest())
    return {"Authorization": f"Bearer {subject}.{timestamp}.{signature}"}


@pytest.fixture
def approval_api(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'approval-api.sqlite3'}")
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_APPROVAL_SECRET", "y" * 32)
    reset_for_tests()

    monkeypatch.setattr("app.ai.agents.agent_async.build_agent_steps", lambda *args, **kwargs: (WRITE,))
    monkeypatch.setattr("app.ai.agents.agent_async._load_memory_context", lambda *args: ())
    monkeypatch.setattr(
        "app.ai.agents.agent_async.decide_tool",
        lambda *args, **kwargs: ApprovalDecision(
            False, True, "valid server-issued approval is required"
        ),
    )

    async def blocked(*args, **kwargs):
        return {
            "steps": (), "blocked_steps": (WRITE,),
            "results": (), "retry_counts": (),
        }

    monkeypatch.setattr("app.ai.agents.agent_async.run_graph_execution", blocked)
    started = client.post("/api/agent", json={"message": "delete old branch"}, headers=_auth("alice"))
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    assert run_id
    return run_id


def test_only_owner_can_review_and_resume_exact_action(approval_api, monkeypatch):
    path = f"/api/agent/runs/{approval_api}/approval"
    assert client.get(path, headers=_auth("bob")).status_code == 404
    assert client.post(path, json={"decision": "approve"}, headers=_auth("bob")).status_code == 404

    review = client.get(path, headers=_auth("alice"))
    assert review.status_code == 200
    assert review.json()["tool"] == WRITE.tool
    assert review.json()["payload"] == WRITE.payload
    assert review.json()["status"] == "pending"
    called = []

    async def verified_graph(planned, *, user_id, approved_tools, approval_tokens, **kwargs):
        assert planned == (WRITE,)
        assert user_id == "alice"
        assert validate_approval_token(approval_tokens[0], "alice", WRITE.tool)
        called.append(planned[0])
        return {
            "steps": planned,
            "blocked_steps": (),
            "results": (ToolResult(WRITE.tool, "remote action verified", safe=True),),
            "retry_counts": (0,),
        }

    monkeypatch.setattr(agent_route, "run_graph_execution", verified_graph)
    accepted = client.post(path, json={"decision": "approve"}, headers=_auth("alice"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "completed"
    assert accepted.json()["requires_manual_review"] is False
    assert len(called) == 1

    repeat = client.post(path, json={"decision": "approve"}, headers=_auth("alice"))
    assert repeat.status_code == 409
    assert len(called) == 1
    assert client.get(path, headers=_auth("alice")).json()["status"] == "completed"


def test_deny_never_dispatches_a_provider_write(approval_api, monkeypatch):
    path = f"/api/agent/runs/{approval_api}/approval"

    async def forbidden(*args, **kwargs):
        raise AssertionError("deny must never execute the tool")

    monkeypatch.setattr(agent_route, "run_graph_execution", forbidden)
    denied = client.post(path, json={"decision": "deny"}, headers=_auth("alice"))
    assert denied.status_code == 200
    assert denied.json() == {"run_id": approval_api, "status": "denied", "executed": False}
    assert client.post(path, json={"decision": "approve"}, headers=_auth("alice")).status_code == 409


def test_provider_timeout_is_not_auto_retried(approval_api, monkeypatch):
    path = f"/api/agent/runs/{approval_api}/approval"
    calls = []

    async def uncertain(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("provider may have received write")

    monkeypatch.setattr(agent_route, "run_graph_execution", uncertain)
    response = client.post(path, json={"decision": "approve"}, headers=_auth("alice"))
    assert response.status_code == 200
    assert response.json()["status"] == "needs_review"
    assert response.json()["requires_manual_review"] is True
    assert len(calls) == 1
    assert client.post(path, json={"decision": "approve"}, headers=_auth("alice")).status_code == 409
    assert len(calls) == 1


def test_disabled_feature_does_not_stage_new_actions(approval_api, monkeypatch):
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "false")
    path = f"/api/agent/runs/{approval_api}/approval"
    assert client.get(path, headers=_auth("alice")).status_code == 404
    assert client.post(path, json={"decision": "approve"}, headers=_auth("alice")).status_code == 404
