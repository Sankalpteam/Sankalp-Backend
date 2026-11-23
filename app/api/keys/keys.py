"""Developer API keys: create, list, revoke, and verify.

A key is shown once, at creation, and stored only as a SHA-256 digest. Nothing here can
return a secret that already exists, so a leaked database backup does not leak keys.

Verification is a separate dependency, because the two callers are different people: the
console acts for a signed-in Firebase user, and the developer API acts for whoever holds
the key. Mixing them is how a key ends up able to mint keys.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.security.dependencies import current_user_id
from app.storage.database import connect

logger = logging.getLogger("indoone.keys")

router = APIRouter(tags=["keys"])

# "sk_sankalp_" so a key is recognisable in a log, a screenshot or a leaked file: the prefix
# is the only part that is stored in the clear, and it is what a person pastes into support.
_KEY_PREFIX = "sk_sankalp_"
_MAX_NAME = 60
_MAX_KEYS_PER_USER = 20


class CreateKeyRequest(BaseModel):
    name: str = Field(default="", max_length=_MAX_NAME)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store():
    return connect()


def _public_row(row: Any) -> dict[str, object]:
    """What the console may see. The hash never leaves this module."""
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "revoked": bool(row["revoked_at"]),
    }


@router.post("/keys")
async def create_key(request: Request, body: CreateKeyRequest) -> dict[str, object]:
    """Mint a key. The plaintext is in this response and nowhere else, ever."""
    user_id = current_user_id(request)
    name = " ".join(body.name.split())[: _MAX_NAME] or "Untitled key"

    with _store() as connection:
        live = connection.execute(
            "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()[0]
        if int(live) >= _MAX_KEYS_PER_USER:
            raise HTTPException(
                status_code=409,
                detail=f"this account already has {_MAX_KEYS_PER_USER} active keys; revoke one first",
            )

        secret = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        key_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO api_keys (id, user_id, name, prefix, key_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key_id, user_id, name, secret[: len(_KEY_PREFIX) + 6], _digest(secret), _now()),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()

    logger.info("api key created for user=%s id=%s", user_id, key_id)
    payload = _public_row(row)
    # The only time the secret is returned. `once` says so in the response itself, because
    # a client that stores this and expects to read it again is a client that will lose it.
    payload["key"] = secret
    payload["shown_once"] = True
    return payload


@router.get("/keys")
async def list_keys(request: Request) -> dict[str, object]:
    user_id = current_user_id(request)
    with _store() as connection:
        rows = connection.execute(
            "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return {"keys": [_public_row(row) for row in rows]}


@router.delete("/keys/{key_id}")
async def revoke_key(request: Request, key_id: str) -> dict[str, object]:
    """Revoke, never delete: a key that was used has a history worth keeping."""
    user_id = current_user_id(request)
    with _store() as connection:
        row = connection.execute(
            "SELECT * FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such key")
        if row["revoked_at"]:
            return _public_row(row)
        connection.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ?", (_now(), key_id)
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()

    logger.info("api key revoked for user=%s id=%s", user_id, key_id)
    return _public_row(updated)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="api key required")
    return token.strip()


def verify_api_key(request: Request) -> str:
    """The user a developer key belongs to, or 401.

    A key that is unknown and a key that is revoked answer the same way: telling the caller
    which of the two it is tells them their key was once real, which is not their business.
    """
    token = _bearer_token(request)
    if not token.startswith(_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="api key required")

    with _store() as connection:
        row = connection.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (_digest(token),)
        ).fetchone()
        if row is None or row["revoked_at"]:
            raise HTTPException(status_code=401, detail="api key required")
        connection.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), row["id"])
        )
        connection.commit()

    request.state.api_key_id = row["id"]
    return str(row["user_id"])


@router.get("/keys/whoami")
async def whoami(request: Request) -> dict[str, object]:
    """What a developer key is, from the key's point of view.

    The first call anyone makes with a new key: it proves the key works without touching
    anything that costs money or writes data.
    """
    user_id = verify_api_key(request)
    return {
        "ok": True,
        "user_id": user_id,
        "key_id": str(getattr(request.state, "api_key_id", "")),
    }
