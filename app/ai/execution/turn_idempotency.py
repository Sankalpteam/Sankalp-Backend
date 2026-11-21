"""Durable, user-scoped chat turn admission across blocking and streaming endpoints.

A client-provided turn ID is claimed atomically in SQLite or Postgres before any
tool can run. The claim remains after a connection loss or process restart.
An ambiguous outcome is *never* automatically replayed. This is an admission
guard, NOT an exactly-once guarantee for arbitrary third-party API calls.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import closing

from app.storage import database
from app.storage.migrations import ensure_schema

MAX_TURN_ID = 128


def fingerprint(message: str, conversation_id: str | None, file_id: str | None) -> str:
    """Hash the shared cross-endpoint identity, not the user's plaintext."""
    payload = json.dumps(
        {
            "message": message,
            "conversation_id": conversation_id or "",
            "file_id": file_id or "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validated(user_id: str, client_turn_id: str) -> tuple[str, str]:
    user = str(user_id or "").strip()
    turn = str(client_turn_id or "").strip()
    if not user or len(user) > 256:
        raise ValueError("authenticated user is required")
    if not turn or len(turn) > MAX_TURN_ID:
        raise ValueError("invalid client turn id")
    return user, turn


def claim_turn(
    user_id: str,
    client_turn_id: str,
    *,
    message: str,
    conversation_id: str | None = None,
    file_id: str | None = None,
) -> str:
    """Return 'new', 'duplicate', or 'conflict'.

    A unique DB key makes this atomic across processes and replicas. The
    request hash catches accidental reuse of an ID for different content.
    No remote operation is retried or replayed by this function.
    """
    user, turn = _validated(user_id, client_turn_id)
    request_hash = fingerprint(message, conversation_id, file_id)
    ensure_schema()
    with closing(database.connect()) as db:
        inserted = db.execute(
            "INSERT INTO chat_turn_claims (user_id, turn_id, request_hash, status) "
            "VALUES (?, ?, ?, 'received') "
            "ON CONFLICT (user_id, turn_id) DO NOTHING",
            (user, turn, request_hash),
        )
        if inserted.rowcount > 0:
            db.commit()
            return "new"
        row = db.execute(
            "SELECT request_hash FROM chat_turn_claims WHERE user_id = ? AND turn_id = ?",
            (user, turn),
        ).fetchone()
        db.commit()
    if row is None:
        # Never treat an unexpectedly missing row as permission to run a write.
        raise RuntimeError("Chat turn claim could not be verified")
    return "duplicate" if hmac.compare_digest(str(row["request_hash"]), request_hash) else "conflict"


def mark_turn_replied(user_id: str, client_turn_id: str) -> None:
    """Record that an answer was produced; cannot authorize a replay."""
    user, turn = _validated(user_id, client_turn_id)
    ensure_schema()
    with closing(database.connect()) as db:
        db.execute(
            "UPDATE chat_turn_claims SET status = 'replied', "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND turn_id = ?",
            (user, turn),
        )
        db.commit()


def turn_status(user_id: str, client_turn_id: str) -> str | None:
    """Only the authenticated owner may inspect a submitted turn."""
    user, turn = _validated(user_id, client_turn_id)
    ensure_schema()
    with closing(database.connect()) as db:
        row = db.execute(
            "SELECT status FROM chat_turn_claims WHERE user_id = ? AND turn_id = ?",
            (user, turn),
        ).fetchone()
    return str(row["status"]) if row else None
