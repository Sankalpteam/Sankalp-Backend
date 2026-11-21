"""Agent API must not accept a user_id that disagrees with authenticated identity.

The adapter keeps the old JSON response contract for existing clients while
its storage and tools are always scoped to the trusted principal.
"""
import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from app.main import app


def _authorization(user_id: str) -> dict[str, str]:
    sub = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    ts = base64.urlsafe_b64encode(str(int(time.time())).encode()).decode().rstrip("=")
    data = f"{sub}.{ts}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(b"x" * 32, data, hashlib.sha256).digest()
    ).decode().rstrip("=")
    return {"Authorization": f"Bearer {sub}.{ts}.{signature}"}


def test_an_authenticated_user_cannot_run_an_agent_as_someone_else(monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    observed = []

    async def forbidden(*args, **kwargs):
        observed.append((args, kwargs))
        raise AssertionError("untrusted user_id reached the agent executor")

    monkeypatch.setattr("app.api.chat.agent_async.execute_agent_async", forbidden)
    with TestClient(app) as client:
        response = client.post(
            "/api/agent",
            headers=_authorization("user-one"),
            json={"message": "show my memories", "user_id": "another-user"},
        )

    assert response.status_code == 403
    assert observed == []


def test_missing_body_user_id_uses_authenticated_principal(monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    seen = []

    async def fake_execute(*args, **kwargs):
        from app.ai.agents.agent import AgentExecution
        seen.append(kwargs["user_id"])
        return AgentExecution(message=args[0], steps=(), results=())

    monkeypatch.setattr("app.api.chat.agent_async.execute_agent_async", fake_execute)
    with TestClient(app) as client:
        response = client.post(
            "/api/agent",
            headers=_authorization("user-one"),
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert seen == ["user-one"]
    assert response.json()["intent"] == "general"


def test_matching_body_user_id_preserves_legacy_api_contract(monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    seen = []

    async def fake_execute(*args, **kwargs):
        from app.ai.agents.agent import AgentExecution
        seen.append(kwargs["user_id"])
        return AgentExecution(message=args[0], steps=(), results=())

    monkeypatch.setattr("app.api.chat.agent_async.execute_agent_async", fake_execute)
    with TestClient(app) as client:
        response = client.post(
            "/api/agent",
            headers=_authorization("user-one"),
            json={"message": "hello", "user_id": "user-one"},
        )

    assert response.status_code == 200
    assert seen == ["user-one"]
    assert {"steps", "results", "run_id", "blocked_steps", "max_steps"} <= response.json().keys()
