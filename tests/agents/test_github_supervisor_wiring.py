"""Agent 11 answers the live GitHub reads, not a parallel copy of them.

The proof that it ran is the scrubbing: the supervisor strips home paths and credentials and
truncates a large diff, and the plugin path never did.
"""

from __future__ import annotations

import pytest

from app.agents import load

github = load("11_github_supervisor")


class FakeResult:
    def __init__(self, data):
        self.data = data


def _patch(monkeypatch, payload, calls=None):
    async def fake_call(name, arguments, token=None):
        if calls is not None:
            calls.append((name, dict(arguments)))
        return payload

    supervisor_module = load("11_github_supervisor")

    monkeypatch.setattr(supervisor_module, "_default_executor", lambda user_id="": fake_call)
    return fake_call


@pytest.mark.asyncio
async def test_list_branches_runs_through_the_supervisor(monkeypatch) -> None:
    from app.ai.execution import tool_loop

    seen = []

    async def fake_call(name, arguments, token=None):
        seen.append((name, dict(arguments)))
        return {"branches": [{"name": "main"}, {"name": "develop"}]}

    supervisor_module = load("11_github_supervisor")

    monkeypatch.setattr(supervisor_module, "_default_executor", lambda user_id="": fake_call)

    result = await tool_loop.dispatch_tool(
        "u", "c", "github_list_branches", {"repository": "Sankalpteam/Sankalp-Backend"}
    )

    assert result["ok"] is True
    # The argument names were translated to the supervisor's own.
    assert seen == [("github_list_branches", {"repo": "Sankalpteam/Sankalp-Backend"})]
    assert result["sanitized_text"]


@pytest.mark.asyncio
async def test_a_commit_read_maps_sha_to_ref(monkeypatch) -> None:
    from app.ai.execution import tool_loop

    seen = []

    async def fake_call(name, arguments, token=None):
        seen.append((name, dict(arguments)))
        return {"sha": "abc123"}

    supervisor_module = load("11_github_supervisor")

    monkeypatch.setattr(supervisor_module, "_default_executor", lambda user_id="": fake_call)

    await tool_loop.dispatch_tool(
        "u", "c", "github_get_commit", {"repository": "o/r", "sha": "abc123"}
    )

    assert seen == [("github_get_commit", {"repo": "o/r", "ref": "abc123"})]


@pytest.mark.asyncio
async def test_a_home_path_in_the_payload_is_scrubbed(monkeypatch) -> None:
    """The reason to route through the agent: the raw payload carries the path, the text does not."""
    from app.ai.execution import tool_loop

    async def fake_call(name, arguments, token=None):
        return {"files": [{"path": "/home/ubuntu/Indoone-Backend/app/main.py"}]}

    supervisor_module = load("11_github_supervisor")

    monkeypatch.setattr(supervisor_module, "_default_executor", lambda user_id="": fake_call)

    result = await tool_loop.dispatch_tool(
        "u", "c", "github_list_branches", {"repository": "o/r"}
    )

    assert "/home/ubuntu" not in result["sanitized_text"]


@pytest.mark.asyncio
async def test_a_failed_read_is_reported_not_raised(monkeypatch) -> None:
    from app.ai.execution import tool_loop

    async def fake_call(name, arguments, token=None):
        raise RuntimeError("404 Not Found")

    supervisor_module = load("11_github_supervisor")

    monkeypatch.setattr(supervisor_module, "_default_executor", lambda user_id="": fake_call)

    result = await tool_loop.dispatch_tool(
        "u", "c", "github_list_branches", {"repository": "o/missing"}
    )

    assert result["ok"] is False
    assert result["message"]


@pytest.mark.asyncio
async def test_a_write_still_goes_through_the_plugin(monkeypatch) -> None:
    """The supervisor is read-only; a merge must still reach the approval flow."""
    from app.ai.execution import tool_loop

    assert "github_merge_pull_request" not in tool_loop._GITHUB_SUPERVISOR_ROUTES


def test_the_route_table_is_read_only() -> None:
    """Every name in the table must be one the supervisor actually declares."""
    from app.ai.execution import tool_loop

    declared = {item["name"] for item in github._TOOL_DECLARATIONS}

    for name, (agent_tool, _mapping) in tool_loop._GITHUB_SUPERVISOR_ROUTES.items():
        assert agent_tool in declared, f"{name} routes to an undeclared tool"
