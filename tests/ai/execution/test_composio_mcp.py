"""MCP opt-in tool safety, auth routing and result grounding (network mocked)."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from app.ai.execution import composio_bridge, composio_mcp


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "mcp-test-key")
    monkeypatch.setenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", "true")
    monkeypatch.setenv("INDOONE_COMPOSIO_MCP_GMAIL_READ_ENABLED", "true")


def test_mcp_opt_in_requires_both_flags(monkeypatch):
    assert composio_mcp.enabled()
    monkeypatch.setenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", "false")
    assert not composio_mcp.enabled()
    monkeypatch.setenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", "true")
    monkeypatch.setenv("INDOONE_COMPOSIO_MCP_GMAIL_READ_ENABLED", "false")
    assert not composio_mcp.enabled()


@pytest.mark.parametrize("url", [
    "http://api.composio.dev/mcp",
    "https://example.com/mcp",
    "https://evilcomposio.dev/mcp",
    "https://api.composio.dev.evil.test/mcp",
    "https://127.0.0.1/mcp",
    "https://user:pass@api.composio.dev/mcp",
    "https://api.composio.dev:8443/mcp",
])
def test_mcp_endpoint_rejects_ssrf_and_bad_origins(url):
    session = SimpleNamespace(mcp=SimpleNamespace(url=url, headers={"x-api-key": "mcp-test-key"}))
    with pytest.raises(ValueError):
        composio_mcp._mcp_config(session)


def test_mcp_rejects_missing_or_untrusted_project_credentials():
    for headers in [{"x-api-key": "wrong"}, {}, {"Authorization": "Bearer secret"}]:
        session = SimpleNamespace(
            mcp=SimpleNamespace(url="https://backend.composio.dev/mcp/s1", headers=headers)
        )
        with pytest.raises(ValueError):
            composio_mcp._mcp_config(session)


def test_valid_hosted_mcp_config_only_returns_project_headers():
    session = SimpleNamespace(
        mcp=SimpleNamespace(url="https://backend.composio.dev/mcp/s1", headers={"x-api-key": "mcp-test-key"})
    )
    assert composio_mcp._mcp_config(session) == (
        "https://backend.composio.dev/mcp/s1", {"x-api-key": "mcp-test-key"}
    )


class FakeSession:
    deleted = []
    def __init__(self, user):
        self.user = user
        self.mcp = SimpleNamespace(url="https://backend.composio.dev/mcp/s1", headers={"x-api-key": "mcp-test-key"})
    def delete(self):
        type(self).deleted.append(self.user)


class FakeSdk:
    created = []
    def __init__(self, **kwargs):
        assert kwargs["api_key"] == "mcp-test-key"
        self.sessions = SimpleNamespace(create=self.create)
    def create(self, **kwargs):
        assert kwargs["tools"] == {"gmail": {"enable": ["GMAIL_FETCH_EMAILS"]}}
        assert kwargs["session_preset"] == "direct_tools"
        assert kwargs["mcp"] is True
        assert kwargs["sandbox"] == {"enable": False}
        assert kwargs["manage_connections"] is False
        assert kwargs["proxy_execute"] is False
        obj = FakeSession(kwargs["user_id"])
        self.created.append(obj)
        return obj


def test_mcp_session_uses_authenticated_pseudonym_and_cleanup(monkeypatch):
    FakeSdk.created = []
    FakeSession.deleted = []
    monkeypatch.setitem(sys.modules, "composio", SimpleNamespace(Composio=FakeSdk, SESSION_PRESET_DIRECT_TOOLS="direct_tools"))
    seen = []
    async def fake_mcp(url, headers, args):
        seen.append((url, headers, args))
        return {"ok": True, "result": {"data": "{}"}}
    monkeypatch.setattr(composio_mcp, "_read_once", fake_mcp)
    a = composio_mcp.read_gmail_via_mcp("first", {"query": "invoice"})
    b = composio_mcp.read_gmail_via_mcp("second", {"query": "other"})
    assert a["ok"] and b["ok"]
    assert FakeSdk.created[0].user != FakeSdk.created[1].user
    assert FakeSdk.created[0].user == composio_bridge._identity("first")
    assert len(FakeSession.deleted) == 2
    assert seen[0][2] == {"query": "invoice"}


@pytest.mark.asyncio
async def test_bridge_mcp_toggle_reuses_strict_model_argument_filter(monkeypatch):
    captured = []
    def fake_sync(user, args):
        captured.append((user, args))
        return {"ok": True, "result": {"data": "{}"}}
    monkeypatch.setattr(composio_mcp, "read_gmail_via_mcp", fake_sync)
    result = await composio_bridge.gmail_read("owner", {
        "query": "invoice", "max_results": 3, "user_id": "attacker",
        "tool_slug": "GMAIL_SEND_EMAIL", "account_id": "another",
    })
    assert result["ok"] is True
    assert captured[0][0] == "owner"
    assert captured[0][1] == {
        "query": "invoice", "max_results": 3,
        "user_id": "me", "include_payload": False, "include_spam_trash": False,
    }


def test_read_once_refuses_untrusted_tools_without_execution(monkeypatch):
    # All protocol objects fake: standard CI does not install optional MCP package.
    class FakeHttp:
        def __init__(self, **kwargs):
            assert kwargs["headers"]["x-api-key"] == "mcp-test-key"
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
    class FakeTransport:
        async def __aenter__(self): return ("in", "out")
        async def __aexit__(self, *_): return False
    class FakeMcpSession:
        names = ["GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL"]
        calls = []
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def initialize(self): pass
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in type(self).names])
        async def call_tool(self, slug, args, **kwargs):
            type(self).calls.append(slug)
            return SimpleNamespace(is_error=False, structured_content={"emails": [{"id": "a"}]})
    monkeypatch.setitem(sys.modules, "httpx2", SimpleNamespace(AsyncClient=FakeHttp, Timeout=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "mcp", SimpleNamespace(ClientSession=FakeMcpSession))
    monkeypatch.setitem(sys.modules, "mcp.client", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", SimpleNamespace(streamable_http_client=lambda *a, **k: FakeTransport()))
    FakeMcpSession.calls = []
    refused = asyncio.run(composio_mcp._read_once(
        "https://backend.composio.dev/mcp/s1",
        {"x-api-key": "mcp-test-key"}, {"user_id": "me"},
    ))
    assert refused["code"] == "MCP_TOOL_LIST_UNSAFE"
    assert FakeMcpSession.calls == []

    FakeMcpSession.names = ["GMAIL_FETCH_EMAILS"]
    allowed = asyncio.run(composio_mcp._read_once(
        "https://backend.composio.dev/mcp/s1",
        {"x-api-key": "mcp-test-key"}, {"user_id": "me"},
    ))
    assert allowed["ok"] is True
    assert FakeMcpSession.calls == ["GMAIL_FETCH_EMAILS"]
