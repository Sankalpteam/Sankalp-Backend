"""Opt-in, fail-closed adapter from the existing chat API to official LangGraph.

Normal /api/chat and /api/chat/stream remain unchanged unless both:
  * INDOONE_CHAT_LANGGRAPH_ENABLED=true on the server; AND
  * the authenticated client explicitly asks for execution_mode="langgraph".

The existing tool planners, LangGraph execution and official HITL checkpoints
are reused, NEVER rewritten. The bridge cannot authorize a write through a
client-supplied tool name or token. /api/chat's legacy CONFIRM/CANCEL path stays
entirely separate from the LangGraph approval path.

A client_turn_id is mandatory for opt-in calls: the shared chat_turn_claims DB
claim is made by the endpoint before this adapter runs. Never auto-replay a
stream/request after an uncertain remote outcome.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException

from app.ai.agents.agent_async import execute_agent_async
from app.ai.execution import langgraph_approval
from app.ai.execution.confirmation import classify_reply
from app.ai.execution.reply_guard import guard_reply
from app.capabilities.connectors.store import get_agent_run


@dataclass(frozen=True)
class ChatAgentOutcome:
    reply: str
    agent_run_id: str | None
    agent_status: str


def enabled() -> bool:
    return os.getenv("INDOONE_CHAT_LANGGRAPH_ENABLED", "false").strip().lower() == "true"


def validate_opt_in(
    *,
    user_id: str,
    message: str,
    client_turn_id: str | None,
    has_attachments: bool,
    has_approval_tokens: bool = False,
) -> None:
    """Reject unsupported requests BEFORE the chat turn is claimed or dispatched."""
    if not enabled():
        raise HTTPException(status_code=409, detail="LangGraph chat mode is not enabled")
    if not user_id.strip():
        raise HTTPException(status_code=401, detail="authenticated user required")
    if not client_turn_id:
        raise HTTPException(status_code=422, detail="LangGraph chat mode requires a client_turn_id")
    if has_attachments:
        raise HTTPException(status_code=422, detail="LangGraph chat mode does not yet support attachments")
    if has_approval_tokens:
        raise HTTPException(status_code=422, detail="approval tokens cannot be supplied in LangGraph chat mode")
    # "Confirm" is an action in the OLD chat store, not proof of permission to
    # resume the LangGraph checkpoint. Never mix these two approval systems.
    if classify_reply(message) in ("confirm", "cancel"):
        raise HTTPException(
            status_code=409,
            detail="Confirm or cancel the original chat action in normal chat mode",
        )


async def run_opt_in_agent(*, user_id: str, message: str) -> ChatAgentOutcome:
    """Run one explicitly opted-in agent turn with no client approvals.

    Only read-only registry tools can execute immediately. A single official
    approval-required write may be checkpointed, but MUST pause for the owner
    to review it through the separately authenticated /api/agent approval UI.
    """
    execution = await execute_agent_async(
        message,
        user_id=user_id,
        approved_tools=frozenset(),
        approval_tokens=(),
        chat_handoff=True,
    )
    run_id = execution.run_id
    if run_id is None:
        return ChatAgentOutcome(
            "No authorized agent action was selected. Use normal chat for this request.",
            None, "no_action",
        )

    # Never advertise an actionable approval solely because the model/tool
    # said "blocked": verify the server-owned DB run and exact persisted action.
    pending = langgraph_approval.get(user_id, run_id)
    stored = get_agent_run(user_id, run_id)
    if (
        pending is not None
        and pending["status"] == "pending"
        and stored is not None
        and stored["status"] == "awaiting_approval"
        and len(execution.blocked_steps) == 1
        and not execution.results
        and not execution.steps
    ):
        return ChatAgentOutcome(
            "This action is paused. Review its exact tool and payload in Pending "
            "agent approvals, then choose Approve once or Deny. No change was sent.",
            run_id, "awaiting_approval",
        )

    if execution.blocked_steps:
        return ChatAgentOutcome(
            "The agent did not execute the blocked action. This request is not "
            "eligible for an automatic approval or replay.",
            run_id, "blocked",
        )

    if not execution.results:
        return ChatAgentOutcome(
            "No supported LangGraph tool matched this request. Use normal chat.",
            run_id, "no_action",
        )

    if not all(result.safe for result in execution.results):
        return ChatAgentOutcome(
            "The agent tool outcome could not be verified. Check the relevant "
            "service before requesting another action.",
            run_id, "needs_review",
        )

    # The tool result is authenticated, not model-supplied executable code.
    # Show a bounded result instead of starting a SECOND model/tool pass.
    result_text = "\n\n".join(
        f"{step.tool}: {result.output[:4000]}"
        for step, result in zip(execution.steps, execution.results)
    )[:8000]
    safe_reply, _ = guard_reply(result_text or "Agent completed the read-only action.")
    return ChatAgentOutcome(safe_reply, run_id, "completed")
