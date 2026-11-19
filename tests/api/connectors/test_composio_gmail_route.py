"""New Composio connect endpoint always requires a verified principal."""
from fastapi.testclient import TestClient
from app.main import app


def test_composio_oauth_link_does_not_allow_anonymous_access():
    with TestClient(app) as client:
        result = client.post("/api/composio/gmail/connect")
    assert result.status_code == 401
