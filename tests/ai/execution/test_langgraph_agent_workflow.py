"""The upstream LangGraph engine replaces the legacy async agent step loop."""
from __future__ import annotations

import asyncio
from time import monotonic

from app.ai.agents.agent import AgentStep
from app.ai.execution.langgraph_orchestrator import run_graph_execution
from app.ai.execution.tools import ToolResult


def test_langgraph_executes_multiple_read_tools_in_order():
    events = []

    async def read(tool, payload, deadline, user_id):
        events.append((tool, payload, user_id))
        return ToolResult(tool, f"ran:{payload}", safe=True), 0

    async def scenario():
        return await run_graph_execution(
            (
                AgentStep(1, "text_stats", "one"),
                AgentStep(2, "text_stats", "two"),
            ),
            user_id="u1",
            approved_tools=frozenset(),
            approval_tokens=(),
            deadline=monotonic() + 20,
            execute_with_retry=read,
        )

    result = asyncio.run(scenario())
    assert events == [("text_stats", "one", "u1"), ("text_stats", "two", "u1")]
    assert [r.output for r in result["results"]] == ["ran:one", "ran:two"]
    assert result["retry_counts"] == (0, 0)
    assert result["blocked_steps"] == ()


def test_langgraph_stops_after_failed_tool_without_running_next_one():
    calls = []

    async def broken(tool, payload, deadline, user_id):
        calls.append(payload)
        return ToolResult(tool, "provider failed", safe=False, retryable=False), 0

    async def scenario():
        return await run_graph_execution(
            (AgentStep(1, "text_stats", "one"), AgentStep(2, "text_stats", "two")),
            user_id="u1",
            approved_tools=frozenset(),
            approval_tokens=(),
            deadline=monotonic() + 20,
            execute_with_retry=broken,
        )

    result = asyncio.run(scenario())
    assert calls == ["one"]
    assert result["steps"][0].payload == "one"
    assert result["blocked_steps"][0].payload == "two"


def test_langgraph_blocks_missing_chain_results():
    async def forbidden(*args):
        raise AssertionError("unresolved chain reference cannot be executed")

    async def scenario():
        return await run_graph_execution(
            (AgentStep(1, "calculator", "$result9+1"),),
            user_id="u1",
            approved_tools=frozenset(),
            approval_tokens=(),
            deadline=monotonic() + 20,
            execute_with_retry=forbidden,
        )

    result = asyncio.run(scenario())
    assert result["steps"] == ()
    assert result["blocked_steps"][0].tool == "calculator"
    assert result["results"][0].safe is False
    assert "Unresolved" in result["results"][0].output


def test_langgraph_keeps_approval_required_tool_blocked_without_token():
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("approval gate must run before a sensitive tool")

    async def scenario():
        return await run_graph_execution(
            (AgentStep(1, "github_delete_branch", '{"repository":"owner/repo","branch":"main"}', True),),
            user_id="u1",
            approved_tools=frozenset({"github_delete_branch"}),
            approval_tokens=(),
            deadline=monotonic() + 20,
            execute_with_retry=forbidden,
        )

    result = asyncio.run(scenario())
    assert not calls
    assert result["steps"] == ()
    assert result["blocked_steps"][0].requires_approval is True
