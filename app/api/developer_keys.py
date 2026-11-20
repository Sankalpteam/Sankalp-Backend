"""Development-only, owner-scoped developer API key management.

API keys are NOT accepted by the normal Firebase user/chat endpoints. This
module deliberately does not enable inference, billing, wallet top-ups or
access to connected users' OAuth tokens. The management feature is OFF by
default. It stores only HMAC key hashes; plaintext keys are returned once.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.security.dependencies import current_user_id
from app.storage import database
from app.storage.migrations import ensure_schema

router = APIRouter(prefix="/developers", tags=["developer-keys"])

_KEY_LIMIT = 20
_ALLOWED_PROVIDERS = frozenset({"gemini", "groq"})


class DeveloperKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    environment: Literal["test", "live"] = "test"


def _enabled() -> bool:
    return os.getenv("INDOONE_DEVELOPER_KEYS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=503, detail="Developer API key management is disabled")


def _secret() -> bytes:
    value = os.getenv("INDOONE_DEVELOPER_KEY_HASH_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError("INDOONE_DEVELOPER_KEY_HASH_SECRET must be 32+ bytes")
    if value in {
        os.getenv("INDOONE_AUTH_SECRET", ""),
        os.getenv("INDOONE_APPROVAL_SECRET", ""),
    }:
        raise RuntimeError("Developer key hash secret must differ from user authentication and approval secrets")
    return value.encode("utf-8")


def _key_hash(raw_key: str) -> str:
    return hmac.new(_secret(), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_record(row) -> dict[str, object]:
    return {
        "id": str(row["key_id"]),
        "name": str(row["name"]),
        "environment": str(row["environment"]),
        "display": str(row["key_prefix"]) + "..." + str(row["key_last4"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


def _require_owner(request: Request) -> str:
    # A localStorage uid, a JSON user_id, or an API key is never sufficient.
    return current_user_id(request)


@router.get("/status")
async def developer_status(request: Request) -> dict[str, object]:
    _require_owner(request)
    return {
        "key_management_enabled": _enabled(),
        "paid_inference_enabled": False,
        "wallet_payments_enabled": False,
        "billing_enabled": False,
        "usage_source": "verified_provider_counts_only",
    }


@router.get("/keys")
async def list_developer_keys(request: Request) -> dict[str, object]:
    owner = _require_owner(request)
    _require_enabled()
    ensure_schema()
    with closing(database.connect()) as db:
        rows = db.execute(
            "SELECT key_id, name, environment, key_prefix, key_last4, status, "
            "created_at, last_used_at, revoked_at "
            "FROM developer_api_keys WHERE owner_id = ? "
            "ORDER BY created_at DESC, key_id DESC LIMIT 100",
            (owner,),
        ).fetchall()
    return {"keys": [_public_record(row) for row in rows], "billing_enabled": False}


@router.post("/keys", status_code=201)
async def create_developer_key(request: Request, body: DeveloperKeyCreate) -> dict[str, object]:
    owner = _require_owner(request)
    _require_enabled()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Key name must not be blank")
    try:
        # A separate server-side secret is required before any key is minted.
        _secret()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    prefix = "sk_sankalp_" + body.environment + "_"
    raw_key = prefix + secrets.token_urlsafe(32)
    digest = _key_hash(raw_key)
    key_id = str(uuid4())
    created_at = _now()
    ensure_schema()
    with closing(database.connect()) as db:
        db.begin_write()
        count = db.execute(
            "SELECT COUNT(*) AS total FROM developer_api_keys "
            "WHERE owner_id = ? AND status = 'active'",
            (owner,),
        ).fetchone()
        if int(count["total"]) >= _KEY_LIMIT:
            raise HTTPException(status_code=429, detail="Active API key limit reached")
        db.execute(
            "INSERT INTO developer_api_keys "
            "(key_id, owner_id, name, environment, key_hash, key_prefix, "
            "key_last4, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (key_id, owner, name, body.environment, digest, prefix, raw_key[-4:], created_at),
        )
        db.commit()

    return {
        "key": raw_key,  # one-time response only; never returned by list or status
        "record": {
            "id": key_id,
            "name": name,
            "environment": body.environment,
            "display": prefix + "..." + raw_key[-4:],
            "status": "active",
            "created_at": created_at,
            "last_used_at": None,
            "revoked_at": None,
        },
        "billing_enabled": False,
        "message": "Store this key securely. Paid inference is not enabled.",
    }


@router.delete("/keys/{key_id}")
async def revoke_developer_key(key_id: str, request: Request) -> dict[str, object]:
    owner = _require_owner(request)
    _require_enabled()
    ensure_schema()
    with closing(database.connect()) as db:
        db.begin_write()
        row = db.execute(
            "SELECT status FROM developer_api_keys WHERE key_id = ? AND owner_id = ?" + database.for_update(),
            (key_id, owner),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="API key not found")
        if str(row["status"]) == "active":
            db.execute(
                "UPDATE developer_api_keys SET status = 'revoked', revoked_at = ? "
                "WHERE key_id = ? AND owner_id = ? AND status = 'active'",
                (_now(), key_id, owner),
            )
        db.commit()
    return {"revoked": True, "key_id": key_id}


def resolve_developer_key(raw_key: str) -> dict[str, str] | None:
    """Internal lookup for a future gated B2B gateway; never user OAuth authority."""
    if not _enabled() or not isinstance(raw_key, str) or len(raw_key) > 160:
        return None
    if not raw_key.startswith(("sk_sankalp_test_", "sk_sankalp_live_")):
        return None
    try:
        digest = _key_hash(raw_key)
        ensure_schema()
        with closing(database.connect()) as db:
            row = db.execute(
                "SELECT key_id, owner_id, environment, key_hash "
                "FROM developer_api_keys WHERE key_hash = ? AND status = 'active'",
                (digest,),
            ).fetchone()
    except RuntimeError:
        return None
    if row is None or not hmac.compare_digest(str(row["key_hash"]), digest):
        return None
    return {
        "key_id": str(row["key_id"]),
        "owner_id": str(row["owner_id"]),
        "environment": str(row["environment"]),
    }


def record_verified_usage(
    *, key_id: str, request_id: str, model: str, provider: str,
    input_tokens: int, output_tokens: int, provider_reported: bool = False,
) -> bool:
    """Persist only verified provider usage; reject estimates and replay.

    Not an HTTP endpoint. Future inference must reserve budget BEFORE calling
    an upstream provider; this ledger alone is NOT a billing engine.
    """
    if not _enabled() or not provider_reported:
        raise PermissionError("Unverified model usage must not enter the billing ledger")
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError("Unsupported usage provider")
    if not key_id or not request_id or len(request_id) > 128 or not model or len(model) > 160:
        raise ValueError("Invalid usage identifiers")
    if (
        type(input_tokens) is not int or type(output_tokens) is not int
        or input_tokens < 0 or output_tokens < 0
    ):
        raise ValueError("Provider token counts must be nonnegative integers")
    ensure_schema()
    with closing(database.connect()) as db:
        db.begin_write()
        row = db.execute(
            "SELECT owner_id, status FROM developer_api_keys WHERE key_id = ?" + database.for_update(),
            (key_id,),
        ).fetchone()
        if row is None or str(row["status"]) != "active":
            raise PermissionError("API key is revoked or unknown")
        cursor = db.execute(
            "INSERT INTO developer_verified_usage "
            "(event_id, request_id, key_id, owner_id, model, provider, "
            "input_tokens, output_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(request_id) DO NOTHING",
            (
                str(uuid4()), request_id, key_id, str(row["owner_id"]),
                model, provider, input_tokens, output_tokens, _now(),
            ),
        )
        inserted = cursor.rowcount == 1
        if inserted:
            db.execute(
                "UPDATE developer_api_keys SET last_used_at = ? WHERE key_id = ?",
                (_now(), key_id),
            )
        db.commit()
    return inserted


@router.get("/usage")
async def developer_usage(request: Request) -> dict[str, object]:
    owner = _require_owner(request)
    _require_enabled()
    ensure_schema()
    with closing(database.connect()) as db:
        row = db.execute(
            "SELECT COUNT(*) AS requests, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens "
            "FROM developer_verified_usage WHERE owner_id = ?",
            (owner,),
        ).fetchone()
    return {
        "requests": int(row["requests"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "billing_enabled": False,
        "currency": None,
        "amount_due": None,
    }
