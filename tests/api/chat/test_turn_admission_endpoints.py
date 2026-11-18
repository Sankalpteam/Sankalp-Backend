"""The same client turn cannot run twice across streamed and blocking chat APIs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage.migrations import reset_for_tests


def _auth(user: str = "user-one") -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(user.encode()).decode().rstrip("=")
    stamp = base64.urlsafe_b64encode(str(int(time.time())).encode()).decode().rstrip("=")
    data = f"{encoded}.{stamp}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(b"x" * 32, data, hashlib.sha256).digest()
    ).decode().rstrip("=")
    return {"Authorization": f"Bearer {encoded}.{stamp}.{signature}"}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INDOONE_CAPABILITY_DB", str(tmp_path / "turn-admission.db"))
    monkeypatch.delenv("INDOONE_DATABASE_URL", raising=False)
    monkeypatch.delenv("INDOONE_MIGRATE_ON_START", raising=False)
    monkeypatch.setattr("app.api.chat.chat._admit", lambda *_args, **_kwargs: None)
    reset_for_tests()
    yield
    reset_for_tests()


def test_stream_then_blocking_cannot_repeat_same_turn(monkeypatch):
    seen = []

    async def fake_github(user_id, message, **kwargs):
        seen.append((user_id, message))
        return "A safe mocked GitHub read result."

    monkeypatch.setattr(
        "app.api.chat.platform.handle_github_chat_with_tools", fake_github,
    )
    turn = "turn-across-endpoints-001"
    payload = {"message": "list github branches", "client_turn_id": turn}

    with TestClient(app) as client:
        first = client.post("/api/chat/stream", json=payload, headers=_auth())
        second = client.post("/api/chat", json=payload, headers=_auth())
        status = client.get(f"/api/chat/turns/{turn}", headers=_auth())
        other = client.get(f"/api/chat/turns/{turn}", headers=_auth("other-user"))

    assert first.status_code == 200
    assert second.status_code == 409
    assert seen == [("user-one", "list github branches")]
    assert status.status_code == 200
    assert status.json()["status"] == "replied"
    assert other.status_code == 404


def test_blocking_then_stream_cannot_repeat_same_turn(monkeypatch):
    monkeypatch.setattr("app.api.chat.chat._treasury_probe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.chat.chat.festival_answer",
        lambda _message: "This was answered without any external action.",
    )
    seen = []

    async def forbidden(*args, **kwargs):
        seen.append((args, kwargs))
        raise AssertionError("duplicate turn reached the stream tool handler")

    monkeypatch.setattr(
        "app.api.chat.platform.handle_github_chat_with_tools", forbidden,
    )
    turn = "turn-across-endpoints-002"
    payload = {"message": "What date is the festival?", "client_turn_id": turn}

    with TestClient(app) as client:
        first = client.post("/api/chat", json=payload, headers=_auth())
        second = client.post("/api/chat/stream", json=payload, headers=_auth())
        status = client.get(f"/api/chat/turns/{turn}", headers=_auth())

    assert first.status_code == 200
    assert second.status_code == 409
    assert seen == []
    assert status.status_code == 200
    assert status.json()["status"] == "replied"



def test_streamed_file_context_uses_authenticated_owner(monkeypatch):
    seen = []

    def read_owned_file(file_id, *, user_id=""):
        seen.append((file_id, user_id))
        return "owner supplied document"

    async def fake_github(*args, **kwargs):
        return "Verified fake result without calling any provider."

    monkeypatch.setattr("app.api.chat.platform.read_text_file", read_owned_file)
    monkeypatch.setattr("app.api.chat.platform.handle_github_chat_with_tools", fake_github)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": "summarize this file", "file_id": "file-example"},
            headers=_auth("user-one"),
        )
    assert response.status_code == 200
    assert seen == [("file-example", "user-one")]


def test_streamed_file_context_denies_wrong_user(monkeypatch):
    async def forbidden_tool(*args, **kwargs):
        raise AssertionError("file access refusal must happen before tools")

    def reject_wrong_owner(file_id, *, user_id=""):
        assert user_id == "user-one"
        raise PermissionError("file belongs to another user")

    monkeypatch.setattr("app.api.chat.platform.read_text_file", reject_wrong_owner)
    monkeypatch.setattr("app.api.chat.platform.handle_github_chat_with_tools", forbidden_tool)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": "summarize this file", "file_id": "someone-elses-file"},
            headers=_auth("user-one"),
        )
    assert response.status_code == 403
