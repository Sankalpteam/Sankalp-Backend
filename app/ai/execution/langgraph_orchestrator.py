"""LangGraph-based execution of a bounded, approval-gated agent plan.

Architecture derived from the MIT-licensed graph implementation in:
https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template
source commit fe052a7c6acf692c05ca0e0a50c0ff2ffba448b7
(app/core/langgraph/graph.py).

The *workflow engine* here is official LangGraph, rather than a bespoke for-loop.
Sankalpa's existing tool schemas, approvals, authentication, retry limits and
persisted run metadata remain authoritative. Do not enable the template's
unscoped threads, OpenAI model config or autonomous parallel writes.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.ai.agents.agent import (
    AgentStep,
    MAX_AGENT_STEPS,
    _chain_payload,
    _unresolved_chain_references,
)
from app.ai.execution.agent_policy import remaining_budget
from app.ai.execution.approval import decide_tool
from app.ai.execution.tool_registry import get_tool_spec
from app.ai.execution.tools import ToolResult


class _State(TypedDict):
    planned: tuple[AgentStep, ...]
    executable: tuple[AgentStep, ...]
    blocked: tuple[AgentStep, ...]
    results: tuple[ToolResult, ...]
    retry_counts: tuple[int, ...]
    attempted_writes: tuple[tuple[str, str], ...]
    cursor: int
    stopped: bool


class GraphExecution(TypedDict):
    steps: tuple[AgentStep, ...]
    blocked_steps: tuple[AgentStep, ...]
    results: tuple[ToolResult, ...]
    retry_counts: tuple[int, ...]


async def run_graph_execution(
    planned: tuple[AgentStep, ...],
    *,
    user_id: str,
    approved_tools: frozenset[str],
    approval_tokens: tuple[str, ...],
    deadline: float,
    execute_with_retry: Callable[[str, str, float, str], Awaitable[tuple[ToolResult, int]]],
) -> GraphExecution:
    """Execute through LangGraph StateGraph; no implicit retries or writable checkpoint.

    This graph is request-scoped. Existing DB agent_run persistence remains the
    system of record; durable LangGraph resume is a separate migration because
    a per-user stable checkpoint namespace and approval proof are required.
    """
    if not planned:
        return {"steps": (), "blocked_steps": (), "results": (), "retry_counts": ()}
    if len(planned) > MAX_AGENT_STEPS:
        planned = planned[:MAX_AGENT_STEPS]

    async def execute(state: _State) -> dict[str, object]:
        cursor = state["cursor"]
        pending = state["planned"]
        if remaining_budget(deadline) <= 0:
            return {
                "blocked": state["blocked"] + pending[cursor:],
                "cursor": len(pending),
                "stopped": True,
            }

        step = pending[cursor]
        decision = decide_tool(step.tool, approved_tools, approval_tokens, user_id=user_id)
        if not decision.allowed:
            # An unresolved human-approval gate stops the entire remaining plan.
            # Never execute later steps after a write the user has not approved.
            if decision.requires_approval:
                return {
                    "blocked": state["blocked"] + pending[cursor:],
                    "cursor": len(pending),
                    "stopped": True,
                }
            return {"blocked": state["blocked"] + (step,), "cursor": cursor + 1}

        prior = state["results"]
        if prior and not prior[-1].safe:
            return {
                "blocked": state["blocked"] + pending[cursor:],
                "cursor": len(pending),
                "stopped": True,
            }

        payload = _chain_payload(step.payload, prior)
        unresolved = _unresolved_chain_references(payload, prior)
        executed = state["executable"]
        blocked = state["blocked"]
        writes = state["attempted_writes"]

        if unresolved:
            blocked += (step,)
            result = ToolResult(
                step.tool, f"Unresolved chain reference(s): {', '.join(unresolved)}",
                safe=False, retryable=False,
            )
            retries = 0
        elif prior and prior[-1].truncated:
            blocked += (step,)
            result = ToolResult(
                step.tool, "Cannot chain from truncated tool output",
                safe=False, retryable=False,
            )
            retries = 0
        else:
            spec = get_tool_spec(step.tool)
            fingerprint = (step.tool, payload)
            if spec is not None and spec.risk == "write" and fingerprint in writes:
                blocked += (step,)
                result = ToolResult(
                    step.tool,
                    "DUPLICATE_WRITE_BLOCKED: verify the earlier write outcome before repeating it",
                    safe=False, retryable=False,
                )
                retries = 0
            else:
                if spec is not None and spec.risk == "write":
                    # Mark before dispatch: remote timeouts can have side effects.
                    writes += (fingerprint,)
                executed += (step,)
                result, retries = await execute_with_retry(step.tool, payload, deadline, user_id)

        stop = not result.safe
        return {
            "executable": executed,
            "blocked": blocked + (pending[cursor + 1:] if stop else ()),
            "results": prior + (result,),
            "retry_counts": state["retry_counts"] + (retries,),
            "attempted_writes": writes,
            "cursor": len(pending) if stop else cursor + 1,
            "stopped": stop,
        }

    def route(state: _State) -> str:
        return END if state["stopped"] or state["cursor"] >= len(state["planned"]) else "execute"

    # Upstream StateGraph node/conditional-edge/compile pattern. LangGraph owns
    # node scheduling, recursion and runtime checks; Sankalpa owns security.
    builder = StateGraph(_State)
    builder.add_node("execute", execute)
    builder.set_entry_point("execute")
    builder.add_conditional_edges("execute", route)
    graph = builder.compile()
    output = await graph.ainvoke(
        {
            "planned": planned,
            "executable": (),
            "blocked": (),
            "results": (),
            "retry_counts": (),
            "attempted_writes": (),
            "cursor": 0,
            "stopped": False,
        },
        config={"recursion_limit": MAX_AGENT_STEPS * 3 + 4},
    )
    return {
        "steps": output["executable"],
        "blocked_steps": output["blocked"],
        "results": output["results"],
        "retry_counts": output["retry_counts"],
    }
