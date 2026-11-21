"""The chat endpoint invokes the registered tool loop for email actions."""
import base64
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from app.main import app


def _auth_headers(user_id: str = "user-one") -> dict[str, str]:
    user = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii").rstrip("=")
    timestamp = base64.urlsafe_b64encode(str(int(time.time())).encode("ascii")).decode("ascii").rstrip("=")
    payload = f"{user}.{timestamp}".encode("ascii")
    signature = base64.urlsafe_b64encode(hmac.new(b"x" * 32, payload, hashlib.sha256).digest()).decode("ascii").rstrip("=")
    return {"Authorization": f"Bearer {user}.{timestamp}.{signature}"}


def test_an_email_request_reaches_shared_tool_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_CAPABILITY_DB", str(tmp_path / "capabilities.db"))
    seen = []

    async def fake_tool_loop(user_id, conversation_id, message, **_kwargs):
        seen.append((user_id, message))
        return "The email has NOT been sent. Confirm the intended recipient first."

    monkeypatch.setattr("app.api.chat.chat.run_tool_loop", fake_tool_loop)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "send an email to ravi about the meeting"},
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    assert seen == [("user-one", "send an email to ravi about the meeting")]
    assert "NOT been sent" in response.json()["reply"]
