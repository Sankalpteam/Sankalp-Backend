"""Model tool selection never authorizes user-scoped writes."""
from __future__ import annotations

import asyncio
import json

from app.ai.agents import model_planner


def test_model_read_planner_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("INDOONE_AGENT_MODEL_PLANNER", raising=False)

    async def scenario():
        return await model_planner.plan_model_read_steps("list github branches", user_id="user")

    assert asyncio.run(scenario()) == ()


def _arrange(monkeypatch, name, args):
    from app.ai.gateway import gemini_service
    from app.ai.execution import tool_loop, connector_authorization
    from types import SimpleNamespace

    monkeypatch.setenv("INDOONE_AGENT_MODEL_PLANNER", "true")
    monkeypatch.setattr(
        connector_authorization, "authorize_tool",
        lambda user_id, name: SimpleNamespace(allowed=True),
    )
    monkeypatch.setattr(
        tool_loop, "_find_tools_result",
        lambda message, user_id: {"ok": True, "result": {"tools": [
            {"name": "github_list_branches"}, {"name": "github_delete_branch"},
        ]}},
    )
    monkeypatch.setattr(
        tool_loop, "model_tool_declarations",
        lambda *args, **kwargs: [
            {"name": "github_list_branches", "parameters": {"type": "OBJECT", "properties": {}}},
            {"name": "github_delete_branch", "parameters": {"type": "OBJECT", "properties": {}}},
        ],
    )
    monkeypatch.setattr(tool_loop, "_families_for", lambda message: frozenset({"github"}))
    monkeypatch.setattr(gemini_service, "_get_tool_config", lambda: ("fake-key", "model"))
    monkeypatch.setattr(gemini_service, "_generation_config", lambda model: {})
    # Keep provider use completely mocked: no network and no real user data.
    monkeypatch.setattr(gemini_service, "_post_generate_content", lambda *unused: _return(name, args))
    monkeypatch.setattr(model_planner.httpx, "AsyncClient", FakeClient)


async def _return(name, args):
    return {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": name, "args": args}}
    ]}}]}


class FakeClient:
    def __init__(self, *, timeout):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def test_model_selected_registered_read_is_a_plan_not_an_executed_tool(monkeypatch):
    _arrange(monkeypatch, "github_list_branches", {"repository": "owner/repo"})
    steps = asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="user-1"))
    assert len(steps) == 1
    assert steps[0].tool == "github_list_branches"
    assert json.loads(steps[0].payload)["repository"] == "owner/repo"
    assert steps[0].requires_approval is False


def test_model_proposed_write_is_refused_without_dispatch(monkeypatch):
    _arrange(monkeypatch, "github_delete_branch", {"repository": "owner/repo", "branch": "main"})
    assert asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="user-1")) == ()


def test_model_unregistered_tool_is_refused_without_dispatch(monkeypatch):
    _arrange(monkeypatch, "unregistered_tool", {"payload": "x"})
    assert asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="user-1")) == ()


def test_plain_greeting_does_not_call_model_even_when_enabled(monkeypatch):
    _arrange(monkeypatch, "github_list_branches", {"repository": "owner/repo"})
    assert asyncio.run(model_planner.plan_model_read_steps("hello", user_id="user-1")) == ()



def test_anonymous_api_does_not_spend_provider_quota(monkeypatch):
    _arrange(monkeypatch, "github_list_branches", {"repository": "owner/repo"})
    assert asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="")) == ()


def test_disconnected_connector_is_not_even_offered_to_the_model(monkeypatch):
    from app.ai.execution import connector_authorization
    from types import SimpleNamespace

    _arrange(monkeypatch, "github_list_branches", {"repository": "owner/repo"})
    monkeypatch.setattr(
        connector_authorization, "authorize_tool",
        lambda user_id, name: SimpleNamespace(allowed=False),
    )
    assert asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="user-1")) == ()



def test_model_planner_refuses_forged_backend_user_identity(monkeypatch):
    _arrange(monkeypatch, "github_list_branches", {"repository": "owner/repo", "user_id": "victim"})
    assert asyncio.run(model_planner.plan_model_read_steps("list github branches", user_id="user-1")) == ()
