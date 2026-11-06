"""Agent 15 is offered to the model, and only when an operator says so.

Running user code is a decision a deployment makes deliberately. The runner is bounded, but
a default that executes code is still a default nobody chose.
"""

from __future__ import annotations

import pytest

from app.ai.execution import tool_loop


def test_the_sandbox_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_CHAT_SANDBOX_ENABLED", raising=False)

    assert tool_loop._sandbox_offered() is False


def test_turning_it_on_offers_the_tool(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_CHAT_SANDBOX_ENABLED", "true")

    names = {item["name"] for item in tool_loop.model_tool_declarations()}

    assert "execute_python_snippet" in names


def test_leaving_it_off_keeps_it_out_of_the_prompt(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_CHAT_SANDBOX_ENABLED", "false")

    names = {item["name"] for item in tool_loop.model_tool_declarations()}

    assert "execute_python_snippet" not in names


@pytest.mark.asyncio
async def test_a_snippet_runs_and_returns_its_output(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_CHAT_SANDBOX_ENABLED", "true")

    result = await tool_loop.dispatch_tool(
        "u", "c", "execute_python_snippet", {"code": "print(6 * 7)"}
    )

    assert result["ok"] is True
    assert "42" in result["result"]["stdout"]
    assert result["result"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_a_forbidden_import_is_refused_without_running(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_CHAT_SANDBOX_ENABLED", "true")

    result = await tool_loop.dispatch_tool(
        "u", "c", "execute_python_snippet", {"code": "import os\nos.system('id')"}
    )

    assert result["ok"] is False
    assert result["message"]


@pytest.mark.asyncio
async def test_an_endless_loop_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_CHAT_SANDBOX_ENABLED", "true")

    result = await tool_loop.dispatch_tool(
        "u", "c", "execute_python_snippet", {"code": "while True:\n    pass", "timeout_seconds": 1}
    )

    assert result["ok"] is False
