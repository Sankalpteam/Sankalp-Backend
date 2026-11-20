"""Official LangGraph interrupt/resume for an authenticated, isolated write.

The pause, durable checkpoint and resume are supplied by upstream LangGraph
(StateGraph + interrupt + Command + AsyncSqliteSaver/AsyncPostgresSaver).
Only the *application-specific* ownership and one-time external dispatch gate
uses Sankalpa's existing agent_runs store. It does not implement a second
checkpoint engine or create another server/database.

The graph's review node is PURE. An external write is dispatched only after:
  1. an owner-scoped DB lookup and matching LangGraph interrupt checkpoint;
  2. an atomic transition from awaiting_approval to approval_claimed;
  3. successful Command(resume="approve") on the exact saved graph.
Any crash after the claim is fail-closed and requires manual reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.ai.execution.tool_registry import get_tool_spec
from app.capabilities.connectors import store
from app.storage import database

APPROVAL_TTL_SECONDS = 300


class ApprovalConflict(Exception):
    """This action cannot be resumed or dispatched safely."""


class ApprovalExpired(ApprovalConflict):
    """The short consent window has passed."""


class _Review(TypedDict):
    owner: str
    run_id: str
    step_hash: str
    approved: bool


def enabled() -> bool:
    return os.getenv("INDOONE_AGENT_DURABLE_APPROVAL_ENABLED", "false").strip().lower() == "true"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expires(run: dict[str, Any]) -> datetime:
    try:
        created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created + timedelta(seconds=APPROVAL_TTL_SECONDS)
    except (ValueError, TypeError, KeyError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _step_hash(step: dict[str, Any]) -> str:
    fields = {key: step[key] for key in ("index", "tool", "payload")}
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _single_write(run: dict[str, Any], *, completed: bool = False) -> dict[str, Any] | None:
    steps = run["steps"] if completed else run["blocked_steps"]
    if len(steps) != 1 or not isinstance(steps[0], dict):
        return None
    step = steps[0]
    if (
        not isinstance(step.get("index"), int)
        or not isinstance(step.get("tool"), str)
        or not isinstance(step.get("payload"), str)
        or step.get("requires_approval") is not True
    ):
        return None
    spec = get_tool_spec(step["tool"])
    return step if spec is not None and spec.risk == "write" and spec.requires_approval else None


def _config(run_id: str) -> dict[str, Any]:
    # agent_runs IDs are server-generated UUIDs. The authenticated owner is
    # verified independently; an untrusted thread_id never authorizes a write.
    return {"configurable": {"thread_id": f"sankalp-agent-approval:{run_id}"}}


def _ask_human(state: _Review) -> dict[str, bool]:
    # Do not place network, DB, or write side effects ahead of interrupt():
    # upstream LangGraph restarts this node when Command(resume=...) arrives.
    answer = interrupt({"run_id": state["run_id"], "step_hash": state["step_hash"]})
    return {"approved": answer == "approve"}


def _graph(checkpointer: Any) -> Any:
    graph = StateGraph(_Review)
    graph.add_node("ask_human", _ask_human)
    graph.add_edge(START, "ask_human")
    graph.add_edge("ask_human", END)
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def _checkpointer() -> AsyncIterator[Any]:
    # Both are the maintained official MIT LangGraph checkpointer backends.
    # They add checkpoint tables to the EXISTING configured application DB.
    # Imports are deliberately lazy: this feature is disabled by default.
    if database.is_postgres():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(database.database_url()) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(str(database.sqlite_path())) as saver:
            await saver.setup()
            yield saver


async def _start_checkpoint(user_id: str, run_id: str, digest: str) -> bool:
    async with _checkpointer() as saver:
        graph = _graph(saver)
        output = await graph.ainvoke(
            {"owner": user_id, "run_id": run_id, "step_hash": digest, "approved": False},
            _config(run_id),
        )
        return "__interrupt__" in output


async def _checkpoint_pending(user_id: str, run_id: str, digest: str) -> bool:
    async with _checkpointer() as saver:
        snapshot = await _graph(saver).aget_state(_config(run_id))
        return bool(
            "ask_human" in snapshot.next
            and snapshot.values.get("owner") == user_id
            and snapshot.values.get("run_id") == run_id
            and snapshot.values.get("step_hash") == digest
        )


async def _resume_checkpoint(user_id: str, run_id: str, digest: str, decision: str) -> bool:
    async with _checkpointer() as saver:
        graph = _graph(saver)
        snapshot = await graph.aget_state(_config(run_id))
        if (
            "ask_human" not in snapshot.next
            or snapshot.values.get("owner") != user_id
            or snapshot.values.get("run_id") != run_id
            or snapshot.values.get("step_hash") != digest
        ):
            raise ApprovalConflict("approval checkpoint is missing or does not match this run")
        completed = await graph.ainvoke(Command(resume=decision), _config(run_id))
        if (
            completed.get("owner") != user_id
            or completed.get("run_id") != run_id
            or completed.get("step_hash") != digest
            or bool(completed.get("approved")) != (decision == "approve")
            or (await graph.aget_state(_config(run_id))).next
        ):
            raise ApprovalConflict("LangGraph did not finish the reviewed approval")
        return decision == "approve"


async def stage(user_id: str, run_id: str, step: dict[str, Any]) -> bool:
    """Persist an official interrupt before exposing a single-action consent UI."""
    if not enabled() or not user_id.strip() or not run_id.strip():
        return False
    run = store.get_agent_run(user_id, run_id)
    if (
        run is None
        or run["status"] != "blocked"
        or run["steps"]
        or run["results"]
        or _single_write(run) != step
    ):
        return False

    # Persist BEFORE advertising an awaiting approval. An orphan checkpoint
    # after an interrupted stage is inert: DB status remains blocked.
    if not await _start_checkpoint(user_id, run_id, _step_hash(step)):
        return False
    with closing(database.connect()) as db:
        db.begin_write()
        changed = db.execute(
            "UPDATE agent_runs SET status = 'awaiting_approval', updated_at = ? "
            "WHERE user_id = ? AND id = ? AND status = 'blocked'",
            (_utcnow().isoformat(), user_id, run_id),
        )
        db.commit()
        return changed.rowcount == 1


def get(user_id: str, run_id: str) -> dict[str, Any] | None:
    """Read only the authenticated owner's recorded, never client-supplied, action."""
    if not enabled() or not user_id.strip():
        return None
    run = store.get_agent_run(user_id, run_id)
    if run is None or run["status"] not in (
        "awaiting_approval", "approval_claimed", "completed",
        "needs_review", "denied", "blocked",
    ):
        return None
    step = _single_write(run) or (
        _single_write(run, completed=True) if run["status"] in ("completed", "needs_review") else None
    )
    if step is None:
        return None
    status = {
        "awaiting_approval": "pending",
        "approval_claimed": "claimed",
    }.get(run["status"], run["status"])
    if status == "pending" and _utcnow() >= _expires(run):
        status = "expired"
    return {
        "run_id": run_id,
        "status": status,
        "tool": step["tool"],
        "payload": step["payload"],
        "expires_at": _expires(run).isoformat(),
    }


async def decide(user_id: str, run_id: str, *, approve: bool) -> dict[str, Any]:
    """One-time owner-scoped claim, followed by upstream Command(resume=...).

    SQL is ONLY an external-write idempotency guard; LangGraph manages the
    suspended workflow and its durable checkpoint. If a crash occurs after
    claiming, no second request can reach a provider write on the same run.
    """
    pending = get(user_id, run_id)
    if pending is None:
        raise LookupError("approval not found")
    if pending["status"] == "expired":
        with closing(database.connect()) as db:
            db.execute(
                "UPDATE agent_runs SET status = 'blocked' "
                "WHERE id = ? AND user_id = ? AND status = 'awaiting_approval'",
                (run_id, user_id),
            )
            db.commit()
        raise ApprovalExpired("approval expired; create a new request")
    if pending["status"] != "pending":
        raise ApprovalConflict("approval has already been decided or claimed")
    run = store.get_agent_run(user_id, run_id)
    step = _single_write(run) if run is not None else None
    if step is None:
        raise ApprovalConflict("saved action is invalid")
    digest = _step_hash(step)
    if not await _checkpoint_pending(user_id, run_id, digest):
        raise ApprovalConflict("saved action does not match its LangGraph checkpoint")

    # Serialize concurrent approve/deny requests in the EXISTING store. A
    # process crash after this CAS blocks duplicate external writes forever.
    with closing(database.connect()) as db:
        db.begin_write()
        stored = db.execute(
            "SELECT status, blocked_steps, created_at FROM agent_runs "
            "WHERE id = ? AND user_id = ?" + database.for_update(),
            (run_id, user_id),
        ).fetchone()
        if stored is None or stored["status"] != "awaiting_approval":
            db.commit()
            raise ApprovalConflict("approval has already been claimed")
        current = json.loads(stored["blocked_steps"])
        if len(current) != 1 or current[0] != step:
            db.execute(
                "UPDATE agent_runs SET status = 'needs_review' WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            )
            db.commit()
            raise ApprovalConflict("original action changed; manual review required")
        if _utcnow() >= _expires({"created_at": stored["created_at"]}):
            db.execute(
                "UPDATE agent_runs SET status = 'blocked' WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            )
            db.commit()
            raise ApprovalExpired("approval expired; create a new request")
        db.execute(
            "UPDATE agent_runs SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            ("approval_claimed" if approve else "denied", _utcnow().isoformat(), run_id, user_id),
        )
        db.commit()

    try:
        verified = await _resume_checkpoint(
            user_id, run_id, digest, "approve" if approve else "deny"
        )
        if verified != approve:
            raise ApprovalConflict("checkpoint decision mismatch")
    except Exception as exc:
        if approve:
            # No tool dispatched. Do not reset a claim or allow replay.
            finish(
                user_id, run_id, steps=[], results=[],
                blocked_steps=[step], retry_counts=[],
            )
        raise ApprovalConflict("LangGraph resume failed; no write was dispatched") from exc
    return step


def finish(
    user_id: str, run_id: str, *,
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    blocked_steps: list[dict[str, Any]],
    retry_counts: list[int],
) -> str:
    """Write verified result once; a remote timeout is NEVER a retry grant."""
    status = (
        "completed"
        if steps and results and len(steps) == len(results)
        and all(result.get("safe") is True for result in results)
        and not blocked_steps
        else "needs_review"
    )
    with closing(database.connect()) as db:
        db.begin_write()
        updated = db.execute(
            "UPDATE agent_runs SET status = ?, steps = ?, results = ?, "
            "blocked_steps = ?, retry_counts = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'approval_claimed'",
            (
                status, json.dumps(steps, ensure_ascii=False, sort_keys=True),
                json.dumps(results, ensure_ascii=False, sort_keys=True),
                json.dumps(blocked_steps, ensure_ascii=False, sort_keys=True),
                json.dumps(retry_counts), _utcnow().isoformat(), run_id, user_id,
            ),
        )
        db.commit()
    if updated.rowcount != 1:
        raise ApprovalConflict("write attempt cannot be completed twice")
    return status
