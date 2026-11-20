"""Persistent, fail-closed human approval for a SINGLE agent write.

This is a narrow, opt-in pause/resume boundary backed by the existing
SQLite/PostgreSQL capability DB; it is not LangGraph's unrestricted checkpoint
deserialization. The exact action comes from the authenticated, persisted
agent_run, never from a client/model-supplied resume payload.

Claim the permission before any external dispatch. If a process dies after
claiming, the action stays claimed and MUST be reconciled manually; it is
never replayed automatically, because the remote write may have succeeded.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from app.ai.execution.tool_registry import get_tool_spec
from app.capabilities.connectors import store
from app.storage import database

APPROVAL_TTL_SECONDS = 300


class ApprovalConflict(Exception):
    """An approval cannot safely be claimed or replayed."""


class ApprovalExpired(ApprovalConflict):
    """The approval window has elapsed."""


def enabled() -> bool:
    return os.getenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "false").strip().lower() == "true"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _step_fingerprint(step: dict[str, Any]) -> str:
    selected = {
        "index": step["index"],
        "tool": step["tool"],
        "payload": step["payload"],
    }
    raw = json.dumps(selected, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pending_step(row: Any) -> dict[str, Any] | None:
    try:
        blocked = json.loads(row["blocked_steps"])
        if len(blocked) != 1 or not isinstance(blocked[0], dict):
            return None
        step = blocked[0]
        if (
            not isinstance(step.get("index"), int)
            or not isinstance(step.get("tool"), str)
            or not isinstance(step.get("payload"), str)
            or step.get("requires_approval") is not True
        ):
            return None
        return step
    except (KeyError, ValueError, TypeError):
        return None


def _valid_write_step(step: dict[str, Any]) -> bool:
    spec = get_tool_spec(step["tool"])
    return bool(spec and spec.requires_approval and spec.risk == "write")


def stage(user_id: str, run_id: str, step: dict[str, Any]) -> bool:
    """Stage only a blocked single-write run, without changing legacy callers.

    The original run must have no executed steps/results. Multi-step graph
    checkpointing is a separate release gate and is deliberately not replayed.
    """
    if not enabled() or not user_id.strip() or not run_id.strip():
        return False
    if not _valid_write_step(step):
        return False
    store.initialize()
    now = _utcnow()
    with closing(database.connect()) as db:
        db.begin_write()
        row = db.execute(
            "SELECT status, steps, results, blocked_steps FROM agent_runs "
            "WHERE id = ? AND user_id = ?" + database.for_update(),
            (run_id, user_id),
        ).fetchone()
        saved = _pending_step(row) if row is not None else None
        if (
            row is None
            or row["status"] != "blocked"
            or json.loads(row["steps"])
            or json.loads(row["results"])
            or saved is None
            or saved != step
        ):
            db.commit()
            return False
        result = db.execute(
            "INSERT INTO pending_agent_approvals "
            "(run_id, user_id, step_hash, status, created_at, expires_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?) ON CONFLICT(run_id) DO NOTHING",
            (
                run_id, user_id, _step_fingerprint(step), now.isoformat(),
                (now + timedelta(seconds=APPROVAL_TTL_SECONDS)).isoformat(),
                now.isoformat(),
            ),
        )
        if result.rowcount != 1:
            db.commit()
            return False
        db.execute(
            "UPDATE agent_runs SET status = 'awaiting_approval', updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (now.isoformat(), run_id, user_id),
        )
        db.commit()
    return True


def get(user_id: str, run_id: str) -> dict[str, Any] | None:
    """Return only this authenticated owner's own pending action."""
    if not enabled() or not user_id.strip():
        return None
    store.initialize()
    with closing(database.connect()) as db:
        row = db.execute(
            "SELECT p.status, p.expires_at, p.created_at, p.step_hash, "
            "r.blocked_steps FROM pending_agent_approvals p "
            "JOIN agent_runs r ON r.id = p.run_id AND r.user_id = p.user_id "
            "WHERE p.user_id = ? AND p.run_id = ?",
            (user_id, run_id),
        ).fetchone()
    if row is None:
        return None
    step = _pending_step(row)
    if step is None or _step_fingerprint(step) != row["step_hash"]:
        # Never display or approve data that differs from the saved intent.
        return None
    status = str(row["status"])
    if status == "pending" and datetime.fromisoformat(str(row["expires_at"])) <= _utcnow():
        status = "expired"
    return {
        "run_id": run_id,
        "status": status,
        "tool": step["tool"],
        "payload": step["payload"],
        "expires_at": str(row["expires_at"]),
    }


def decide(user_id: str, run_id: str, *, approve: bool) -> dict[str, Any]:
    """Atomically deny or one-time claim the exact recorded action.

    Both SQLite BEGIN IMMEDIATE and PostgreSQL FOR UPDATE serialize competing
    requests. Claimed actions are never made pending again on timeout.
    """
    if not enabled():
        raise LookupError("approval is unavailable")
    store.initialize()
    now = _utcnow().isoformat()
    with closing(database.connect()) as db:
        db.begin_write()
        row = db.execute(
            "SELECT p.status, p.step_hash, p.expires_at, r.blocked_steps, "
            "r.status AS run_status FROM pending_agent_approvals p "
            "JOIN agent_runs r ON r.id = p.run_id AND r.user_id = p.user_id "
            "WHERE p.user_id = ? AND p.run_id = ?" + database.for_update(),
            (user_id, run_id),
        ).fetchone()
        if row is None:
            db.commit()
            raise LookupError("approval not found")
        if row["status"] != "pending":
            db.commit()
            raise ApprovalConflict("approval has already been decided or claimed")
        if datetime.fromisoformat(str(row["expires_at"])) <= _utcnow():
            db.execute(
                "UPDATE pending_agent_approvals SET status = 'expired', updated_at = ? "
                "WHERE user_id = ? AND run_id = ?", (now, user_id, run_id)
            )
            db.execute(
                "UPDATE agent_runs SET status = 'blocked', updated_at = ? "
                "WHERE user_id = ? AND id = ?", (now, user_id, run_id)
            )
            db.commit()
            raise ApprovalExpired("approval expired; create a new request")
        step = _pending_step(row)
        if (
            row["run_status"] != "awaiting_approval"
            or step is None
            or _step_fingerprint(step) != row["step_hash"]
            or not _valid_write_step(step)
        ):
            db.execute(
                "UPDATE pending_agent_approvals SET status = 'needs_review', updated_at = ? "
                "WHERE user_id = ? AND run_id = ?", (now, user_id, run_id)
            )
            db.execute(
                "UPDATE agent_runs SET status = 'needs_review', updated_at = ? "
                "WHERE user_id = ? AND id = ?", (now, user_id, run_id)
            )
            db.commit()
            raise ApprovalConflict("saved approval does not match the original action")
        next_status = "claimed" if approve else "denied"
        run_status = "approval_claimed" if approve else "denied"
        db.execute(
            "UPDATE pending_agent_approvals SET status = ?, updated_at = ? "
            "WHERE user_id = ? AND run_id = ?",
            (next_status, now, user_id, run_id),
        )
        db.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? "
            "WHERE user_id = ? AND id = ?",
            (run_status, now, user_id, run_id),
        )
        db.commit()
        return step


def finish(
    user_id: str, run_id: str, *,
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    blocked_steps: list[dict[str, Any]],
    retry_counts: list[int],
) -> str:
    """Finish a claimed run transactionally; unknown outcomes need review."""
    store.initialize()
    success = bool(results) and all(item.get("safe") is True for item in results) and not blocked_steps
    status = "completed" if success else "needs_review"
    now = _utcnow().isoformat()
    with closing(database.connect()) as db:
        db.begin_write()
        row = db.execute(
            "SELECT p.status, r.status AS run_status FROM pending_agent_approvals p "
            "JOIN agent_runs r ON r.id = p.run_id AND r.user_id = p.user_id "
            "WHERE p.user_id = ? AND p.run_id = ?" + database.for_update(),
            (user_id, run_id),
        ).fetchone()
        if row is None or row["status"] != "claimed" or row["run_status"] != "approval_claimed":
            db.commit()
            raise ApprovalConflict("write attempt cannot be completed twice")
        db.execute(
            "UPDATE agent_runs SET status = ?, steps = ?, results = ?, "
            "blocked_steps = ?, retry_counts = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (
                status, json.dumps(steps, ensure_ascii=False, sort_keys=True),
                json.dumps(results, ensure_ascii=False, sort_keys=True),
                json.dumps(blocked_steps, ensure_ascii=False, sort_keys=True),
                json.dumps(retry_counts), now, run_id, user_id,
            ),
        )
        db.execute(
            "UPDATE pending_agent_approvals SET status = ?, updated_at = ? "
            "WHERE user_id = ? AND run_id = ?",
            (status, now, user_id, run_id),
        )
        db.commit()
    return status
