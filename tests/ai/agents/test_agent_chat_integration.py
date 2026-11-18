import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

import pytest

from app.ai.agents.agent import AgentExecution, AgentStep
from app.ai.execution.tools import ToolResult
from app.main import app


def _auth_headers(user_id: str = "user-one") -> dict[str, str]:
    user = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii").rstrip("=")
    timestamp = base64.urlsafe_b64encode(str(int(time.time())).encode("ascii")).decode("ascii").rstrip("=")
    payload = f"{user}.{timestamp}".encode("ascii")
    signature = base64.urlsafe_b64encode(hmac.new(b"x" * 32, payload, hashlib.sha256).digest()).decode("ascii").rstrip("=")
    return {"Authorization": f"Bearer {user}.{timestamp}.{signature}"}


def test_an_email_request_reaches_the_email_handler(monkeypatch, tmp_path) -> None:
    """An email request is answered by the email handler, whatever the agent plans.

    The agent still runs - it decides. What it must not do is execute an email step of
    its own: every email tool it could plan is either gated on the user having connected
    that mailbox or gated on approval, so it produces no result and leaves the request to
    the email path, which knows the provider and asks the one confirmation.
    """
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_CAPABILITY_DB", str(tmp_path / "capabilities.db"))

    handled: list[str] = []

    async def fake_email_handler(user_id, conversation_id, message, history=None):
        handled.append(message)
        return "The email path handled this."

    monkeypatch.setattr("app.api.chat.chat.handle_email_chat_with_tools", fake_email_handler)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "send an email to ravi about the meeting"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert handled == ["send an email to ravi about the meeting"]
    assert response.json()["reply"] == "The email path handled this."


