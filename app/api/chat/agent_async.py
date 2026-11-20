from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Literal
from uuid import UUID
import time

from app.ai.agents.agent import AgentStep, MAX_AGENT_RUNTIME_SECONDS, MAX_AGENT_STEPS, _serialize_result, _serialize_step
from app.ai.agents.agent_async import _run_with_retry_async
from app.ai.execution import durable_approval
from app.ai.execution.approval import issue_approval_token
from app.ai.execution.langgraph_orchestrator import run_graph_execution
from app.ai.execution.tools import ToolResult
from app.api.security.dependencies import current_user_id
from app.ai.agents.agent_async import execute_agent_async
from app.api.content.capabilities import AgentRequest, _scoped_user

router = APIRouter(tags=["capabilities"])


@router.post("/agent")
async def agent(request: AgentRequest, http_request: Request) -> dict[str, object]:
    # A body-supplied user_id is NOT an identity claim. The auth middleware
    # verified the bearer principal; reject mismatches before reading memories,
    # executing connected tools, or writing an agent_run record.
    scoped_user = _scoped_user(http_request, request.user_id)
    execution = await execute_agent_async(
        request.message,
        user_id=scoped_user,
        approved_tools=frozenset(request.approved_tools),
    )
    if not execution.steps and not execution.blocked_steps:
        return {
            "intent": "general",
            "tool": None,
            "tool_payload": None,
            "result": None,
            "steps": [],
            "results": [],
            "memories": list(execution.memories),
            "blocked_steps": [],
            "retry_counts": list(execution.retry_counts),
            "run_id": execution.run_id,
            "max_steps": MAX_AGENT_STEPS,
        }

    first = execution.steps[0] if execution.steps else execution.blocked_steps[0]
    first_result = execution.results[0] if execution.results else None

    def serialize_result(result):
        return {
            "name": result.name,
            "output": result.output,
            "safe": result.safe,
            "retryable": result.retryable,
            "truncated": result.truncated,
        }

    return {
        "intent": "tool",
        "tool": first.tool,
        "tool_payload": first.payload,
        "result": None if first_result is None else serialize_result(first_result),
        "steps": [
            {
                "index": step.index,
                "tool": step.tool,
                "payload": step.payload,
                "requires_approval": step.requires_approval,
            }
            for step in execution.steps
        ],
        "results": [serialize_result(result) for result in execution.results],
        "memories": list(execution.memories),
        "blocked_steps": [
            {
                "index": step.index,
                "tool": step.tool,
                "payload": step.payload,
                "requires_approval": step.requires_approval,
            }
            for step in execution.blocked_steps
        ],
        "retry_counts": list(execution.retry_counts),
        "run_id": execution.run_id,
        "max_steps": MAX_AGENT_STEPS,
    }


class AgentApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]


@router.get("/agent/runs/{run_id}/approval")
async def read_agent_approval(run_id: UUID, request: Request) -> dict[str, object]:
    """Show the exact user-owned action before a human approves it."""
    user_id = current_user_id(request)
    pending = durable_approval.get(user_id, str(run_id))
    if pending is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return pending


@router.post("/agent/runs/{run_id}/approval")
async def decide_agent_approval(
    run_id: UUID, body: AgentApprovalDecision, request: Request
) -> dict[str, object]:
    """Approve or deny once; do not accept a tool/payload/user from the request.

    A claimed write may have reached an external provider even if the HTTP
    response is lost. Repeat POSTs must never re-dispatch that write.
    """
    user_id = current_user_id(request)
    run_key = str(run_id)
    pending = durable_approval.get(user_id, run_key)
    if pending is None:
        raise HTTPException(status_code=404, detail="approval not found")

    token = ""
    if body.decision == "approve" and pending["status"] == "pending":
        # Preflight the server secret *before* committing a one-time claim.
        try:
            token = issue_approval_token(user_id, str(pending["tool"]), ttl_seconds=60)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail="approval service unavailable") from exc

    try:
        saved = durable_approval.decide(user_id, run_key, approve=body.decision == "approve")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="approval not found") from exc
    except durable_approval.ApprovalExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except durable_approval.ApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if body.decision == "deny":
        return {"run_id": run_key, "status": "denied", "executed": False}

    step = AgentStep(
        index=saved["index"], tool=saved["tool"], payload=saved["payload"],
        requires_approval=True,
    )
    try:
        outcome = await run_graph_execution(
            (step,),
            user_id=user_id,
            approved_tools=frozenset(),
            approval_tokens=(token,),
            deadline=time.monotonic() + MAX_AGENT_RUNTIME_SECONDS,
            execute_with_retry=_run_with_retry_async,
        )
        executed = [_serialize_step(item) for item in outcome["steps"]]
        results = [_serialize_result(item) for item in outcome["results"]]
        blocked = [_serialize_step(item) for item in outcome["blocked_steps"]]
        retries = list(outcome["retry_counts"])
    except Exception:
        # The write might already have happened. Never retry on this run.
        executed = [_serialize_step(step)]
        results = [_serialize_result(ToolResult(
            step.tool, "Outcome could not be verified; review the provider before trying again",
            safe=False, retryable=False,
        ))]
        blocked = []
        retries = [0]

    status = durable_approval.finish(
        user_id, run_key, steps=executed, results=results,
        blocked_steps=blocked, retry_counts=retries,
    )
    return {
        "run_id": run_key,
        "status": status,
        "executed": bool(executed),
        "steps": executed,
        "results": results,
        "blocked_steps": blocked,
        "retry_counts": retries,
        "requires_manual_review": status != "completed",
    }
