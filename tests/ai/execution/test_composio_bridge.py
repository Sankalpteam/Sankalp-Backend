"""Composio official SDK adapter: opt-in, user isolation, read-only and auth."""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from app.ai.execution import composio_bridge, tool_loop


class FakeSession:
    def __init__(self, user):
        self.user = user
        self.calls = []

    def execute(self, *, tool_slug, arguments):
        self.calls.append((tool_slug, arguments))
        return SimpleNamespace(
            result_type="completed", data={"messages": [{"subject": "Owned email"}]}
        )

    def authorize(self, toolkit):
        assert toolkit == "gmail"
        return SimpleNamespace(redirect_url="https://connect.composio.dev/link/example")


class FakeComposio:
    sessions = []

    def __init__(self, *, api_key):
        assert api_key == "fake-credential"
        self.sessions = SimpleNamespace(create=self.create)

    def create(self, *, user_id, toolkits, tools):
        assert toolkits == ["gmail"]
        assert tools == {"gmail": {"enable": ["GMAIL_FETCH_EMAILS"]}}
        obj = FakeSession(user_id)
        self.sessions.append(obj)
        return obj


@pytest.fixture(autouse=True)
def prepare(monkeypatch):
    FakeComposio.sessions = []
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", raising=False)
    monkeypatch.setitem(sys.modules, "composio", SimpleNamespace(Composio=FakeComposio))


def enable(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "fake-credential")
    monkeypatch.setenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", "true")


@pytest.mark.asyncio
async def test_disabled_no_sdk_session_or_model_declaration():
    assert (await composio_bridge.gmail_read("user-1", {}))["code"] == "COMPOSIO_DISABLED"
    assert FakeComposio.sessions == []
    assert "composio_gmail_fetch_emails" not in [
        item["name"] for item in tool_loop.model_tool_declarations("user-1", full=True)
    ]


@pytest.mark.asyncio
async def test_gmail_read_uses_authenticated_user_and_fixed_read_only_slug(monkeypatch):
    enable(monkeypatch)
    result = await tool_loop.dispatch_tool(
        "owner-A", "conversation-A", "composio_gmail_fetch_emails",
        {"query": "invoice", "max_results": 3, "user_id": "other", "tool_slug": "GMAIL_SEND_EMAIL", "account_id": "other"},
    )
    assert result["ok"] is True
    session = FakeComposio.sessions[0]
    assert session.user == composio_bridge._identity("owner-A")
    assert session.calls == [("GMAIL_FETCH_EMAILS", {
        "query": "invoice", "max_results": 3, "user_id": "me",
        "include_payload": False, "include_spam_trash": False,
    })]
    await tool_loop.dispatch_tool("owner-B", "conversation-B", "composio_gmail_fetch_emails", {})
    assert FakeComposio.sessions[1].user == composio_bridge._identity("owner-B")
    assert FakeComposio.sessions[0].user != FakeComposio.sessions[1].user


@pytest.mark.asyncio
async def test_unverified_sdk_result_does_not_claim_mailbox_data(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(FakeSession, "execute", lambda self, **kwargs:
        SimpleNamespace(result_type="blocked", error="", data={"sent": True}))
    result = await composio_bridge.gmail_read("owner", {"max_results": 1})
    assert result["ok"] is False
    assert result["code"] == "COMPOSIO_READ_UNVERIFIED"
    assert "sent" not in str(result)


@pytest.mark.asyncio
async def test_invalid_args_and_anonymous_requests_fail_closed(monkeypatch):
    enable(monkeypatch)
    assert (await composio_bridge.gmail_read("", {}))["ok"] is False
    assert (await composio_bridge.gmail_read("owner", {"max_results": 100}))["ok"] is False
    assert (await composio_bridge.gmail_read("owner", {"max_results": True}))["ok"] is False
    assert FakeComposio.sessions == []


@pytest.mark.asyncio
async def test_optional_model_tool_only_offered_to_authenticated_user(monkeypatch):
    enable(monkeypatch)
    names = lambda user: [item["name"] for item in tool_loop.model_tool_declarations(
        user, discovered=("composio_gmail_fetch_emails",),
    )]
    assert "composio_gmail_fetch_emails" in names("owner")
    assert "composio_gmail_fetch_emails" not in names("")


@pytest.mark.asyncio
async def test_oauth_link_is_user_scoped_and_host_checked(monkeypatch):
    enable(monkeypatch)
    assert await composio_bridge.gmail_connect_link("owner") == "https://connect.composio.dev/link/example"
    assert FakeComposio.sessions[0].user == composio_bridge._identity("owner")
    monkeypatch.setattr(FakeSession, "authorize",
        lambda self, toolkit: SimpleNamespace(redirect_url="https://evil.example.com/steal"))
    with pytest.raises(RuntimeError, match="unexpected authorization"):
        await composio_bridge.gmail_connect_link("owner")
