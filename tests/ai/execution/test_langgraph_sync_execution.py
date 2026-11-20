"""The legacy synchronous executor now uses the official LangGraph runtime."""
from __future__ import annotations

from time import monotonic

from app.ai.agents.agent import AgentStep
from app.ai.execution.langgraph_sync_orchestrator import run_graph_execution_sync
from app.ai.execution.tools import ToolResult


def test_sync_graph_uses_authenticated_executor_and_preserves_step_order():
    seen = []

    def executor(name, payload, deadline, user_id):
        seen.append((name, payload, user_id))
        return ToolResult(name, "ok", safe=True), 0

    result = run_graph_execution_sync(
        (AgentStep(1, "text_stats", "one"), AgentStep(2, "text_stats", "two")),
        user_id="user-1",
        approved_tools=frozenset(),
        approval_tokens=(),
        deadline=monotonic() + 10,
        execute_with_retry=executor,
    )
    assert seen == [("text_stats", "one", "user-1"), ("text_stats", "two", "user-1")]
    assert [step.payload for step in result["steps"]] == ["one", "two"]
    assert result["retry_counts"] == (0, 0)


def test_sync_graph_does_not_dispatch_after_unsafe_tool():
    calls = []

    def fail(name, payload, deadline, user_id):
        calls.append(payload)
        return ToolResult(name, "unverified", safe=False, retryable=False), 0

    result = run_graph_execution_sync(
        (AgentStep(1, "text_stats", "one"), AgentStep(2, "text_stats", "two")),
        user_id="user-1",
        approved_tools=frozenset(),
        approval_tokens=(),
        deadline=monotonic() + 10,
        execute_with_retry=fail,
    )
    assert calls == ["one"]
    assert [step.payload for step in result["blocked_steps"]] == ["two"]


def test_sync_graph_blocks_write_without_server_approval_token():
    calls = []

    def fail_if_executed(*args):
        calls.append(args)
        raise AssertionError("unapproved write reached executor")

    result = run_graph_execution_sync(
        (AgentStep(1, "github_delete_branch", '{"repository":"owner/repo","branch":"main"}', True),),
        user_id="user-1",
        approved_tools=frozenset({"github_delete_branch"}),
        approval_tokens=(),
        deadline=monotonic() + 10,
        execute_with_retry=fail_if_executed,
    )
    assert calls == []
    assert result["steps"] == ()
    assert result["blocked_steps"][0].tool == "github_delete_branch"


def test_sync_unapproved_write_halts_following_steps():
    calls = []

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("no step may execute after a blocked write")

    result = run_graph_execution_sync(
        (
            AgentStep(1, "github_delete_branch", '{"repository":"org/repo","branch":"feature"}', True),
            AgentStep(2, "text_stats", "should never run"),
        ),
        user_id="u1", approved_tools=frozenset(), approval_tokens=(),
        deadline=monotonic() + 20, execute_with_retry=forbidden,
    )
    assert calls == []
    assert result["steps"] == ()
    assert [step.index for step in result["blocked_steps"]] == [1, 2]
