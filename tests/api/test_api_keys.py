import os, tempfile, pathlib
os.environ.setdefault("INDOONE_DATABASE_URL", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.storage.database import connect
from app.api.keys.keys import router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "k.db"
    monkeypatch.setenv("INDOONE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INDOONE_DATABASE_PATH", str(db))
    import app.api.keys.keys as k
    monkeypatch.setattr(k, "_store", lambda: connect(str(db)))
    with connect(str(db)) as c:
        c.execute("""CREATE TABLE api_keys (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            name TEXT NOT NULL, prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_used_at TEXT, revoked_at TEXT)""")
        c.commit()

    app = FastAPI()
    @app.middleware("http")
    async def principal(request, call_next):
        request.state.principal_id = request.headers.get("x-test-user", "")
        return await call_next(request)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_create_returns_the_secret_once(client):
    r = client.post("/api/keys", json={"name": "laptop"}, headers={"x-test-user": "u1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"].startswith("sk_sankalp_")
    assert body["shown_once"] is True
    assert body["revoked"] is False

    listing = client.get("/api/keys", headers={"x-test-user": "u1"}).json()["keys"]
    assert len(listing) == 1
    assert "key" not in listing[0] and "key_hash" not in listing[0]


def test_requires_a_signed_in_user(client):
    assert client.post("/api/keys", json={}).status_code == 401


def test_a_key_verifies_and_a_revoked_one_does_not(client):
    key = client.post("/api/keys", json={"name": "k"}, headers={"x-test-user": "u1"}).json()
    ok = client.get("/api/keys/whoami", headers={"authorization": f"Bearer {key['key']}"})
    assert ok.status_code == 200 and ok.json()["user_id"] == "u1"

    client.delete(f"/api/keys/{key['id']}", headers={"x-test-user": "u1"})
    gone = client.get("/api/keys/whoami", headers={"authorization": f"Bearer {key['key']}"})
    assert gone.status_code == 401


def test_another_user_cannot_revoke_it(client):
    key = client.post("/api/keys", json={"name": "k"}, headers={"x-test-user": "u1"}).json()
    assert client.delete(f"/api/keys/{key['id']}", headers={"x-test-user": "u2"}).status_code == 404


def test_unknown_key_is_401_not_404(client):
    r = client.get("/api/keys/whoami", headers={"authorization": "Bearer sk_sankalp_nope"})
    assert r.status_code == 401
