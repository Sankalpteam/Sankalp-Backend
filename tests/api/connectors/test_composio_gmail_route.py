"""New Composio connect endpoint always requires a verified principal."""
from fastapi.testclient import TestClient
from app.main import app


def test_composio_oauth_link_does_not_allow_anonymous_access():
    with TestClient(app) as client:
        result = client.post("/api/composio/gmail/connect")
    assert result.status_code == 401


def _bearer(user_id: str) -> dict[str, str]:
    import base64
    import hashlib
    import hmac
    import time

    encoded = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    timestamp = base64.urlsafe_b64encode(str(int(time.time())).encode()).decode().rstrip("=")
    payload = f"{encoded}.{timestamp}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(b"x" * 32, payload, hashlib.sha256).digest()
    ).decode().rstrip("=")
    return {"Authorization": f"Bearer {encoded}.{timestamp}.{signature}"}


def test_signed_user_gets_only_their_own_composio_oauth_link(monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")
    observed = []

    async def fake_link(user_id):
        observed.append(user_id)
        return "https://connect.composio.dev/link/user-owned"

    monkeypatch.setattr("app.api.connectors.composio_gmail.gmail_connect_link", fake_link)

    with TestClient(app) as client:
        one = client.post(
            "/api/composio/gmail/connect",
            json={"user_id": "other-user"},
            headers=_bearer("user-one"),
        )
        two = client.post(
            "/api/composio/gmail/connect",
            headers=_bearer("user-two"),
        )

    assert one.status_code == 200
    assert two.status_code == 200
    assert one.json()["authorization_url"].startswith("https://connect.composio.dev/")
    assert observed == ["user-one", "user-two"], "untrusted body must never pick OAuth owner"


def test_composio_link_error_never_leaks_api_key(monkeypatch):
    monkeypatch.setenv("INDOONE_AUTH_SECRET", "x" * 32)
    monkeypatch.setenv("INDOONE_AUTH_REQUIRED", "true")

    async def fake_error(user_id):
        raise RuntimeError("upstream secret-key-do-not-expose")

    monkeypatch.setattr("app.api.connectors.composio_gmail.gmail_connect_link", fake_error)
    with TestClient(app) as client:
        result = client.post("/api/composio/gmail/connect", headers=_bearer("user-one"))

    assert result.status_code == 503
    assert "secret-key-do-not-expose" not in result.text
