"""Opt-in /api/chat -> official LangGraph handoff, both transports.

These tests intentionally do not make provider calls. The existing agent
executor and the HTTP security/claim contracts are exercised; enabling a
real connector still requires separate approved E2E.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.agents.agent import AgentExecution, AgentStep
from app.ai.execution.tools import ToolResult
from app.ai.memory.conversation_store import ConversationStore
from app.main import app
from app.storage.migrations import reset_for_tests

client = TestClient(app)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _auth(user: str = "alice") -> dict[str, str]:
    subject = _b64(user.encode())
    stamp = _b64(str(int(time.time())).encode())
    signed = f"{subject}.{stamp}".encode()
    signature = _b64(hmac.new(b"x" * 32, signed, hashlib.sha256).digest())
    return {"Authorization": f"Bearer {subject}.{stamp}.{signature}"}


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "true")
    monkeypatch.setenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "false")
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'bridge.sqlite3'}")
    reset_for_tests()
    monkeypatch.setattr("app.api.chat.chat._admit", lambda *args, **kwargs: None)
    history = ConversationStore(tmp_path / "history.sqlite3")
    monkeypatch.setattr("app.api.chat.chat._store", history)
    monkeypatch.setattr("app.api.chat.platform._stream_store", history)
    return history


def _turn() -> str:
    return "turn_" + uuid4().hex


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _read_execution(message: str = "count words: hello world", user: str = "alice") -> AgentExecution:
    return AgentExecution(
        message=message,
        steps=(AgentStep(1, "text_stats", "hello world"),),
        results=(ToolResult("text_stats", "two words", safe=True, retryable=False),),
        run_id=str(uuid4()),
    )


def test_default_chat_unchanged_when_flag_off(monkeypatch, bridge):
    monkeypatch.setenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "false")

    called = 0

    async def must_not_execute(*args, **kwargs):
        nonlocal called
        called += 1
        raise AssertionError("LangGraph must be OFF")

    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", must_not_execute)
    opted = client.post(
        "/api/chat", json={"message": "count words: hi", "execution_mode": "langgraph",
                           "client_turn_id": _turn()}, headers=_auth(),
    )
    assert opted.status_code == 409
    assert called == 0

    async def normal_reply(message, **kwargs):
        return "normal-chat"

    monkeypatch.setattr("app.api.chat.chat.festival_answer", lambda *a: None)
    monkeypatch.setattr("app.api.chat.chat.generate_reply", normal_reply)
    monkeypatch.setattr("app.api.chat.chat.handle_github_chat_with_tools", _none)
    monkeypatch.setattr("app.api.chat.chat.email_read_fast_path", _none)
    monkeypatch.setattr("app.api.chat.chat.web_research_answer", _none)
    normal = client.post("/api/chat", json={"message": "hi"}, headers=_auth())
    assert normal.status_code == 200
    assert called == 0
    assert normal.json()["reply"]


async def _none(*args, **kwargs):
    return None


@pytest.mark.parametrize("payload,code", [
    ({"message": "count words: hi", "execution_mode": "langgraph"}, 422),
    ({"message": "yes", "execution_mode": "langgraph", "client_turn_id": _turn()}, 409),
    ({"message": "CANCEL", "execution_mode": "langgraph", "client_turn_id": _turn()}, 409),
    ({"message": "count words: hi", "execution_mode": "langgraph",
      "approval_tokens": ["untrusted-token"], "client_turn_id": _turn()}, 422),
    ({"message": "count words: hi", "execution_mode": "langgraph",
      "file_id": "file-no-match", "client_turn_id": _turn()}, 422),
])
def test_reject_invalid_handoffs_before_dispatch(bridge, payload, code, monkeypatch):
    async def must_not_execute(*args, **kwargs):
        raise AssertionError("rejected before any agent run")
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", must_not_execute)
    response = client.post("/api/chat", json=payload, headers=_auth())
    assert response.status_code == code


def test_chat_runs_once_under_auth_and_persists_agent_run_link(bridge, monkeypatch):
    calls = []

    async def execute(message, **kwargs):
        calls.append((message, kwargs))
        return _read_execution(message)

    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", execute)
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.langgraph_approval.get", lambda *a: None)
    turn = _turn()
    body = {"message": "count words: hello world", "client_turn_id": turn,
            "execution_mode": "langgraph"}
    response = client.post("/api/chat", json=body, headers=_auth())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["agent_status"] == "completed"
    assert data["agent_run_id"]
    assert "two words" in data["reply"]
    assert data["action_chips"] == []
    assert len(calls) == 1
    assert calls[0][1]["user_id"] == "alice"
    assert calls[0][1]["chat_handoff"] is True
    assert calls[0][1]["approved_tools"] == frozenset()
    assert calls[0][1]["approval_tokens"] == ()
    # Same turn cannot execute even when a client falls back to another transport.
    duplicate = client.post("/api/chat/stream", json=body, headers=_auth())
    assert duplicate.status_code == 409
    assert len(calls) == 1
    assert bridge.recent(data["conversation_id"], user_id="alice")


def test_stream_mode_emits_one_meta_delta_done_and_does_not_run_old_tools(bridge, monkeypatch):
    calls = 0
    async def fake_exec(message, **kwargs):
        nonlocal calls
        calls += 1
        return _read_execution(message)

    async def legacy_must_not_run(*args, **kwargs):
        raise AssertionError("legacy router/tool loop must not receive opt-in request")

    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", fake_exec)
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.langgraph_approval.get", lambda *a: None)
    monkeypatch.setattr("app.api.chat.platform.handle_github_chat_with_tools", legacy_must_not_run)
    monkeypatch.setattr("app.api.chat.platform.run_tool_loop", legacy_must_not_run)

    response = client.post(
        "/api/chat/stream",
        json={"message": "count words: hi there", "execution_mode": "langgraph",
              "client_turn_id": _turn()}, headers=_auth(),
    )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert [e["type"] for e in events] == ["meta", "delta", "done"]
    assert events[-1]["agent_status"] == "completed"
    assert events[-1]["agent_run_id"]
    assert events[-1]["reply"] == events[1]["text"]
    assert events[-1]["action_chips"] == []
    assert calls == 1


def test_paused_write_requires_real_saved_checkpoint_before_chat_announces_approval(
    bridge, monkeypatch
):
    run_id = str(uuid4())
    blocked = AgentStep(1, "github_delete_branch",
                        '{"repository":"owner/repo","branch":"old"}', True)

    async def blocked_exec(message, **kwargs):
        return AgentExecution(message=message, steps=(), results=(),
                              blocked_steps=(blocked,), run_id=run_id)

    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", blocked_exec)
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.langgraph_approval.get",
                        lambda user, rid: {"status": "pending"} if user == "alice" and rid == run_id else None)
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.get_agent_run",
                        lambda user, rid: {"status": "awaiting_approval"} if user == "alice" and rid == run_id else None)

    body = {"message": "delete an old branch", "execution_mode": "langgraph",
            "client_turn_id": _turn()}
    response = client.post("/api/chat", json=body, headers=_auth())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["agent_status"] == "awaiting_approval"
    assert data["agent_run_id"] == run_id
    assert data["action_chips"] == []  # Never convert approval into CONFIRM/CANCEL.
    assert "No change was sent" in data["reply"]

    # If checkpoint does not match, report blocked rather than fake approval.
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.langgraph_approval.get", lambda *a: None)
    other = client.post("/api/chat", json={**body, "client_turn_id": _turn()}, headers=_auth())
    assert other.status_code == 200
    assert other.json()["agent_status"] == "blocked"
    assert "not execute" in other.json()["reply"].lower()


def test_unknown_outcome_is_masked_not_called_success(bridge, monkeypatch):
    async def unverified(message, **kwargs):
        return AgentExecution(
            message=message, steps=(AgentStep(1, "text_stats", "x"),),
            results=(ToolResult("text_stats", "provider-secret-error", safe=False),),
            run_id=str(uuid4()),
        )
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.execute_agent_async", unverified)
    monkeypatch.setattr("app.ai.execution.chat_agent_bridge.langgraph_approval.get", lambda *a: None)
    response = client.post("/api/chat", json={
        "message": "count words: x", "execution_mode": "langgraph", "client_turn_id": _turn()
    }, headers=_auth())
    assert response.status_code == 200
    assert response.json()["agent_status"] == "needs_review"
    assert "provider-secret" not in response.json()["reply"]


def test_without_identity_bridge_fails_closed(bridge):
    response = client.post("/api/chat", json={
        "message": "count words: hi", "execution_mode": "langgraph",
        "client_turn_id": _turn(),
    })
    assert response.status_code in (401, 403)


def test_opt_in_planner_writes_without_approval_are_dropped(monkeypatch, bridge):
    from app.ai.agents import agent_async
    from app.ai.agents.agent import AgentStep

    monkeypatch.setattr(agent_async, "build_agent_steps", lambda *a, **k: (
        AgentStep(1, "github_update_repository_file", '{"repository":"org/repo"}', True),
    ))
    monkeypatch.setattr(agent_async, "_load_memory_context", lambda *a: ())
    async def no_execution(planned, **kwargs):
        assert not planned  # External writes must never reach dispatch.
        return {"steps": (), "blocked_steps": (), "results": (), "retry_counts": ()}
    monkeypatch.setattr(agent_async, "run_graph_execution", no_execution)
    result = client.post("/api/chat", json={
        "message": "update a repository file", "execution_mode": "langgraph",
        "client_turn_id": _turn(),
    }, headers=_auth())
    assert result.status_code == 200, result.text
    assert result.json()["agent_status"] == "no_action"


def test_agent_mode_capability_is_authenticated_and_default_off(bridge, monkeypatch):
    monkeypatch.setenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "false")
    off = client.get("/api/chat/agent-mode", headers=_auth())
    assert off.status_code == 200
    assert off.json() == {"enabled": False}

    monkeypatch.setenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "true")
    on = client.get("/api/chat/agent-mode", headers=_auth())
    assert on.status_code == 200
    assert on.json() == {"enabled": True}
    unauthenticated = client.get("/api/chat/agent-mode")
    assert unauthenticated.status_code in (401, 403)
