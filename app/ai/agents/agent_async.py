"""The /api/agent async executor delegates orchestration to LangGraph.

This is an adapter preserving the old API response and DB run contract. The
manual async step loop has been replaced by the official MIT LangGraph engine.
The old heuristic *planner* remains until its model-driven replacement passes
equivalent tool-routing and permission tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Iterable

from app.ai.agents.agent import (
    AgentExecution,
    AgentStep,
    MAX_AGENT_MESSAGE_LENGTH,
    MAX_AGENT_RETRIES,
    MAX_AGENT_RUNTIME_SECONDS,
    MAX_AGENT_STEPS,
    _bounded_approval_tokens,
    _load_memory_context,
    _serialize_result,
    _serialize_step,
    build_agent_steps,
)
from app.ai.execution.agent_policy import deadline_failure, remaining_budget, should_retry
from app.ai.execution.agent_tool_capabilities import admit_chat_plan
from app.ai.execution.approval import decide_tool
from app.ai.execution import langgraph_approval as durable_approval
from app.ai.execution.tool_registry import get_tool_spec
from app.ai.execution.langgraph_orchestrator import run_graph_execution
from app.ai.execution.tools import ToolResult, run_tool_async
from app.capabilities.connectors.store import create_agent_run, update_agent_run


async def _run_with_retry_async(tool: str, payload: str, deadline: float, user_id: str = "") -> tuple[ToolResult, int]:
    remaining = remaining_budget(deadline)
    if remaining <= 0:
        return deadline_failure(tool), 0
    result = await run_tool_async(tool, payload, max_runtime_seconds=remaining, user_id=user_id)
    retry_count = 0
    while should_retry(result, retry_count, MAX_AGENT_RETRIES, tool=tool):
        remaining = remaining_budget(deadline)
        if remaining <= 0:
            return deadline_failure(tool), retry_count
        retry_count += 1
        result = await run_tool_async(tool, payload, max_runtime_seconds=remaining, user_id=user_id)
    return result, retry_count


def _public_research_enabled() -> bool:
    """Separate opt-in: no change to ordinary chat or other graph tools."""
    return os.getenv("INDOONE_CHAT_LANGGRAPH_WEB_RESEARCH_ENABLED", "false").strip().lower() == "true"


async def _run_public_web_read(
    tool: str, payload: str, deadline: float, user_id: str
) -> tuple[ToolResult, int]:
    """Use the existing public-web supervisor, not an unreviewed MCP tool."""
    if tool != "search_web" or not user_id.strip():
        return ToolResult(tool, "Public web research is not authorized", safe=False), 0
    try:
        parsed = json.loads(payload)
        if not isinstance(parsed, dict) or set(parsed) != {"query"}:
            raise ValueError("unexpected web search arguments")
        query = parsed["query"]
        from app.web_research.tools import (
            MAX_SEARCH_QUERY_LENGTH, _PRIVATE_QUERY_PATTERNS,
        )
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_SEARCH_QUERY_LENGTH
            or any(pattern.search(query) for pattern in _PRIVATE_QUERY_PATTERNS)
        ):
            raise ValueError("invalid or private public-web query")
        remaining = remaining_budget(deadline)
        if remaining <= 0:
            return deadline_failure(tool), 0

        from app.ai.execution.tool_loop import dispatch_tool
        result = await asyncio.wait_for(
            dispatch_tool(user_id, "", "search_web", {"query": query}),
            timeout=min(12.0, remaining),
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            return ToolResult(
                tool, "Public web research unavailable or rejected by safety policy",
                safe=False, retryable=False,
            ), 0
        evidence = result.get("result")
        if not isinstance(evidence, dict) or evidence.get("status") != "ok":
            return ToolResult(tool, "No verified web results", safe=False, retryable=False), 0
        text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        truncated = len(text) > 8000
        return ToolResult(
            tool, text[:8000] if truncated else text,
            safe=True, retryable=False, truncated=truncated,
        ), 0
    except (ValueError, TypeError, asyncio.TimeoutError):
        return ToolResult(tool, "Public web research failed validation or timed out", safe=False), 0


async def execute_agent_async(
    message: str,
    user_id: str = "",
    approved_tools: set[str] | frozenset[str] | None = None,
    contacts: list[dict[str, Any]] | None = None,
    approval_tokens: Iterable[str] | None = None,
    chat_handoff: bool = False,
) -> AgentExecution:
    normalized = message.strip()
    if not normalized or len(normalized) > MAX_AGENT_MESSAGE_LENGTH:
        return AgentExecution(message=message, steps=(), results=())

    # An opted-in chat request is never a source of write authority.
    approved = frozenset() if chat_handoff else frozenset(approved_tools or ())
    tokens = () if chat_handoff else _bounded_approval_tokens(approval_tokens)
    memories = _load_memory_context(user_id, normalized)

    run_id: str | None = None
    if user_id.strip():
        run_id = str(create_agent_run(user_id, normalized)["id"])

    # Legacy explicit/structured requests keep their deterministic API contract.
    # For previously unmatched live-data requests, let the provider SELECT a
    # read-only tool under a bounded timeout. This returns a plan, never executes
    # a remote operation; the authenticated LangGraph executor remains the gate.
    planned = build_agent_steps(normalized, user_id=user_id, contacts=contacts)[:MAX_AGENT_STEPS]
    deadline = time.monotonic() + MAX_AGENT_RUNTIME_SECONDS
    public_research = bool(chat_handoff and _public_research_enabled())
    if not planned and public_research:
        from app.web_research.tools import wants_web_research

        if wants_web_research(normalized):
            # This is a public READ request selected from the user's explicit
            # search intent, not an arbitrary tool/URL supplied by the model.
            planned = (AgentStep(
                index=1, tool="search_web",
                payload=json.dumps({"query": normalized}, ensure_ascii=False),
                requires_approval=False,
            ),)
    if not planned:
        from app.ai.agents.model_planner import plan_model_read_steps

        planned = await plan_model_read_steps(normalized, user_id=user_id)
    if chat_handoff and not admit_chat_plan(
        tuple(step.tool for step in planned),
        allow_approval_write=durable_approval.enabled(),
        allow_web_research=public_research,
    ):
        # The policy views every registered local/connector tool, not only
        # "plugins"; specialized Gmail/Composio/MCP paths remain legacy-only
        # until an explicitly trusted official adapter is implemented.
        planned = ()
    async def execute_with_policy(
        tool: str, payload: str, end: float, principal: str
    ) -> tuple[ToolResult, int]:
        if public_research and tool == "search_web":
            return await _run_public_web_read(tool, payload, end, principal)
        return await _run_with_retry_async(tool, payload, end, principal)

    result = await run_graph_execution(
        planned,
        user_id=user_id,
        approved_tools=approved,
        approval_tokens=tokens,
        deadline=deadline,
        execute_with_retry=execute_with_policy,
        trusted_web_read=public_research,
    )
    steps = result["steps"]
    blocked = result["blocked_steps"]
    results = result["results"]
    retries = result["retry_counts"]

    unsafe = any(not item.safe for item in results)
    timed_out = bool(blocked and len(steps) + len(blocked) >= len(planned) and remaining_budget(deadline) <= 0)
    status = "timed_out" if timed_out else ("failed" if unsafe else ("blocked" if blocked and not results else "completed"))

    execution = AgentExecution(
        message=normalized, steps=steps, results=results, memories=memories,
        blocked_steps=blocked, retry_counts=retries, run_id=run_id,
    )
    if user_id.strip() and run_id is not None:
        update_agent_run(
            user_id=user_id,
            run_id=run_id,
            status=status,
            steps=[_serialize_step(step) for step in execution.steps],
            results=[_serialize_result(item) for item in execution.results],
            blocked_steps=[_serialize_step(step) for step in execution.blocked_steps],
            retry_counts=list(execution.retry_counts),
        )
        # This opt-in first checkpoint supports only an isolated write whose
        # entire plan consists of one unexecuted approval-required step.
        # Never stage connector-denied tools or replay a multi-step graph.
        if (
            durable_approval.enabled()
            and len(planned) == 1
            and len(blocked) == 1
            and not steps
            and not results
        ):
            pending = blocked[0]
            spec = get_tool_spec(pending.tool)
            if spec is not None and spec.requires_approval and spec.risk == "write":
                decision = decide_tool(
                    pending.tool, approved, tokens, user_id=user_id
                )
                if decision.reason == "valid server-issued approval is required":
                    await durable_approval.stage(user_id, run_id, _serialize_step(pending))
    return execution
