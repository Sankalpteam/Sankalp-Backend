"""Check LangGraph deadline semantics without relying on CI wall-clock jitter."""
from __future__ import annotations

from app.ai.agents import agent
from app.ai.execution import langgraph_sync_orchestrator as sync_graph
from app.ai.execution.tools import ToolResult


def _budget_after_n_calls(monkeypatch, calls: dict[str, int], limit: int) -> None:
    """A request deadline expires once exactly N tool calls have completed.

    LangGraph scheduling/import time depends on the machine, not the tool
    budget. Mock the deadline *policy* instead of sleeping 20 ms and hoping a
    CI runner schedules the graph in the remaining 10 ms.
    """
    def remaining(_deadline: float) -> float:
        return 1.0 if calls["count"] < limit else 0.0

    monkeypatch.setattr(agent, "remaining_budget", remaining)
    monkeypatch.setattr(sync_graph, "remaining_budget", remaining)


def test_agent_stops_when_total_runtime_budget_is_exhausted(monkeypatch) -> None:
    calls = {"count": 0}

    def tool(name: str, payload: str) -> ToolResult:
        calls["count"] += 1
        return ToolResult(name, payload, safe=True, retryable=False)

    monkeypatch.setattr(agent, "run_tool", tool)
    _budget_after_n_calls(monkeypatch, calls, limit=2)

    execution = agent.execute_agent("text stats: one; text stats: two; text stats: three")

    assert calls["count"] == 2
    assert len(execution.results) == 2
    assert len(execution.blocked_steps) == 1
    assert execution.blocked_steps[0].index == 3


def test_agent_runtime_budget_is_independent_of_step_limit(monkeypatch) -> None:
    calls = {"count": 0}

    def tool(name: str, payload: str) -> ToolResult:
        calls["count"] += 1
        return ToolResult(name, payload)

    monkeypatch.setattr(agent, "run_tool", tool)
    _budget_after_n_calls(monkeypatch, calls, limit=1)

    execution = agent.execute_agent("text stats: one; text stats: two")

    assert calls["count"] == 1
    assert len(execution.steps) == 1
    assert len(execution.results) == 1
    assert len(execution.blocked_steps) == 1
    assert execution.blocked_steps[0].index == 2
