"""Synchronous adapter to the official LangGraph StateGraph workflow engine.

The sync /api/agent compatibility API previously contained a separate manual
execution loop. This replaces scheduling with the upstream MIT LangGraph
runtime (model/tool StateGraph pattern from the reviewed template at
fe052a7c6acf692c05ca0e0a50c0ff2ffba448b7).

This remains a request-scoped graph with no persisted checkpoint replay.
Existing Sankalpa approval, tool scopes, bounded retries and run-store APIs
remain authoritative, so a provider timeout never permits an automatic
repeat of a data-changing external action.
"""
from __future__ import annotations

from collections.abc import Callable
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


class _SyncState(TypedDict):
    planned: tuple[AgentStep, ...]
    executable: tuple[AgentStep, ...]
    blocked: tuple[AgentStep, ...]
    results: tuple[ToolResult, ...]
    retry_counts: tuple[int, ...]
    attempted_writes: tuple[tuple[str, str], ...]
    cursor: int
    stopped: bool


class _Outcome(TypedDict):
    steps: tuple[AgentStep, ...]
    blocked_steps: tuple[AgentStep, ...]
    results: tuple[ToolResult, ...]
    retry_counts: tuple[int, ...]


def run_graph_execution_sync(
    planned: tuple[AgentStep, ...],
    *,
    user_id: str,
    approved_tools: frozenset[str],
    approval_tokens: tuple[str, ...],
    deadline: float,
    execute_with_retry: Callable[[str, str, float, str], tuple[ToolResult, int]],
) -> _Outcome:
    """Use a synchronous LangGraph node without creating an asyncio event loop.

    Reads and writes are dispatched through the original authenticated executor;
    no new independently deployed agent or database is involved.
    """
    if not planned:
        return {"steps": (), "blocked_steps": (), "results": (), "retry_counts": ()}
    planned = planned[:MAX_AGENT_STEPS]

    def execute(state: _SyncState) -> dict[str, object]:
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
                    "DUPLICATE_WRITE_BLOCKED: verify the prior write outcome before retrying",
                    safe=False, retryable=False,
                )
                retries = 0
            else:
                if spec is not None and spec.risk == "write":
                    writes += (fingerprint,)
                executed += (step,)
                result, retries = execute_with_retry(step.tool, payload, deadline, user_id)

        stopped = not result.safe
        return {
            "executable": executed,
            "blocked": blocked + (pending[cursor + 1:] if stopped else ()),
            "results": prior + (result,),
            "retry_counts": state["retry_counts"] + (retries,),
            "attempted_writes": writes,
            "cursor": len(pending) if stopped else cursor + 1,
            "stopped": stopped,
        }

    def route(state: _SyncState) -> str:
        return END if state["stopped"] or state["cursor"] >= len(state["planned"]) else "execute"

    graph = StateGraph(_SyncState)
    graph.add_node("execute", execute)
    graph.set_entry_point("execute")
    graph.add_conditional_edges("execute", route)
    compiled = graph.compile()
    final = compiled.invoke(
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
        "steps": final["executable"],
        "blocked_steps": final["blocked"],
        "results": final["results"],
        "retry_counts": final["retry_counts"],
    }
