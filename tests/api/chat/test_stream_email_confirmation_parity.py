"""Streamed Zoho pending writes must match the safe blocking-chat approval path.

Provider actions are mocked: no actual email is ever sent or deleted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.memory.conversation_store import ConversationStore
from app.main import app
from app.storage.migrations import reset_for_tests


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _auth(user: str = "email-owner") -> dict[str, str]:
    uid = _b64(user.encode("utf-8"))
    timestamp = _b64(str(int(time.time())).encode("ascii"))
    message = f"{uid}.{timestamp}".encode("ascii")
    sig = _b64(hmac.new(b"e" * 32, message, hashlib.sha256).digest())
    return {"Authorization": f"Bearer {uid}.{timestamp}.{sig}"}


def _events(body: str):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def stream_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "e" * 32)
    monkeypatch.setenv("INDOONE_APPROVAL_SECRET", "q" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INDOONE_DATABASE_URL", f"sqlite:///{tmp_path / 'stream.sqlite3'}")
    monkeypatch.setenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "false")
    reset_for_tests()
    monkeypatch.setattr("app.api.chat.chat._admit", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.api.chat.platform._stream_store", ConversationStore(tmp_path / "chat.sqlite3"))
    monkeypatch.setattr(
        "app.api.chat.platform.assess_answer",
        lambda *_a, **_kw: SimpleNamespace(passed=True, reason=""),
    )

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("No second tool or model may execute while email is pending")

    monkeypatch.setattr("app.api.chat.platform.handle_github_chat_with_tools", must_not_run)
    monkeypatch.setattr("app.api.chat.platform.run_tool_loop", must_not_run)
    monkeypatch.setattr("app.api.chat.platform.stream_generate", must_not_run)

    monkeypatch.setattr(
        "app.api.chat.platform.get_pending_email_action",
        lambda user, conversation: {
            "user_id": user, "conversation_id": conversation,
            "integration_id": "zoho_mail", "action_type": "send",
        },
    )
    yield
    reset_for_tests()


@pytest.mark.parametrize("message,decision", [("CONFIRM", "confirm"), ("CANCEL", "cancel")])
def test_pending_email_confirmation_stays_on_email_executor(
    stream_guard, monkeypatch, message, decision
):
    calls = []

    async def classify(_message):
        assert _message == message
        return decision

    async def resolved(user, conversation, submitted):
        calls.append((user, conversation, submitted))
        return "Pending email decision handled safely."

    monkeypatch.setattr("app.api.chat.platform.classify_reply_async", classify)
    monkeypatch.setattr("app.api.chat.platform.handle_email_chat", resolved)
    conversation_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": message, "conversation_id": conversation_id},
            headers=_auth(),
        )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert [event["type"] for event in events] == ["meta", "delta", "done"]
    assert events[-1]["reply"] == "Pending email decision handled safely."
    assert calls == [("email-owner", conversation_id, message)]


def test_pending_email_unrelated_text_is_held_without_a_second_tool(stream_guard, monkeypatch):
    async def classify(_message):
        return "other"

    async def must_not_confirm(*_args):
        raise AssertionError("Pending action must not execute on unrelated text")

    monkeypatch.setattr("app.api.chat.platform.classify_reply_async", classify)
    monkeypatch.setattr("app.api.chat.platform.handle_email_chat", must_not_confirm)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": "What is next?", "conversation_id": str(uuid4())},
            headers=_auth(),
        )
    assert response.status_code == 200, response.text
    assert "pending" in _events(response.text)[-1]["reply"].lower() or "confirm" in _events(response.text)[-1]["reply"].lower()


def test_email_provider_failure_never_becomes_plain_model_answer(stream_guard, monkeypatch):
    async def classify(_message):
        return "confirm"

    async def fails(*_args):
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr("app.api.chat.platform.classify_reply_async", classify)
    monkeypatch.setattr("app.api.chat.platform.handle_email_chat", fails)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": "CONFIRM", "conversation_id": str(uuid4())},
            headers=_auth(),
        )
    assert response.status_code == 503
    assert "simulated provider" not in response.text
