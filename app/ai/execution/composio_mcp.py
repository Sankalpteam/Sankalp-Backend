"""Optional official MCP Python SDK v2 client for Composio Gmail READ only.

The SDK runs inside Sankalpa's existing FastAPI process and connects to a
Composio-managed, per-authenticated-user hosted session. It is disabled by
default; it never starts a local MCP daemon or trusts a model-supplied MCP URL.

Security:
- Fixed GMAIL_FETCH_EMAILS slug only; require an exclusively read-only tool list
- Composio-created session, HTTPS Composio DNS and the session's auth headers
- No user/tool/URL/account overrides from model arguments
- Bounded synchronous worker/timeout from existing composio_bridge._call_bounded
- No automatic model tool discovery/execution, prompts, elicitation or retries
- Only structured/verified tool results reach the model
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlsplit

from app.ai.execution import composio_bridge

_TOOL = "GMAIL_FETCH_EMAILS"
_MAX_OUTPUT = 12_000
_VALID_HEADERS = frozenset({"x-api-key", "x-user-api-key", "x-org-id", "x-project-id"})


def enabled() -> bool:
    return (
        composio_bridge.enabled()
        and os.getenv("INDOONE_COMPOSIO_MCP_GMAIL_READ_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _mcp_config(session: Any) -> tuple[str, dict[str, str]]:
    """Treat hosted config as untrusted until URL and auth headers are verified."""
    config = getattr(session, "mcp", None)
    endpoint = getattr(config, "url", None)
    headers = getattr(config, "headers", None)
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError("invalid hosted MCP endpoint")
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    # No arbitrary external host, IP address, localhost or cross-origin redirect.
    if (parsed.scheme != "https" or not host.endswith(".composio.dev")
            or parsed.username or parsed.password or parsed.port is not None
            or not parsed.path.startswith("/") or parsed.fragment):
        raise ValueError("unauthorized MCP endpoint")
    if not isinstance(headers, dict):
        raise ValueError("missing hosted MCP credentials")
    trusted: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("invalid hosted MCP header")
        name = key.casefold()
        if name not in _VALID_HEADERS or len(value) > 4096 or "\n" in value or "\r" in value:
            raise ValueError("unexpected MCP credentials")
        trusted[name] = value
    api_key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not api_key or trusted.get("x-api-key") != api_key:
        raise ValueError("MCP credentials do not match configured Composio project")
    return endpoint, trusted


async def _read_once(url: str, headers: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
    """The *actual upstream* Streamable HTTP client and ClientSession APIs."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with asyncio.timeout(12.0):
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(8.0, connect=3.0),
            follow_redirects=False,
            trust_env=False,
        ) as http:
            async with streamable_http_client(
                url, http_client=http,
                terminate_on_close=False, max_sse_event_size=64 * 1024,
            ) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    available = await session.list_tools()
                    names = [getattr(tool, "name", "") for tool in getattr(available, "tools", [])]
                    # Fail closed on meta/helper/write tools, even if upstream
                    # session settings accidentally change in the future.
                    if names != [_TOOL]:
                        return {"ok": False, "code": "MCP_TOOL_LIST_UNSAFE"}
                    result = await session.call_tool(_TOOL, args, read_timeout_seconds=8.0)
                    if getattr(result, "is_error", None) is not False:
                        return {"ok": False, "code": "MCP_READ_UNVERIFIED"}
                    data = getattr(result, "structured_content", None)
                    if not isinstance(data, (dict, list)):
                        # Free-form tool text is not verified structured mailbox data.
                        return {"ok": False, "code": "MCP_READ_UNVERIFIED"}
                    encoded = json.dumps(data, ensure_ascii=False, default=str)
                    return {
                        "ok": True,
                        "result": {"data": encoded[:_MAX_OUTPUT], "truncated": len(encoded) > _MAX_OUTPUT},
                    }


def read_gmail_via_mcp(user_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Runs in the bridge's existing 2-slot worker, never on the event loop."""
    from composio import Composio, SESSION_PRESET_DIRECT_TOOLS

    key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not key or not isinstance(args, dict):
        raise ValueError("MCP is unavailable")
    owner = composio_bridge._identity(user_id)
    client = Composio(api_key=key, timeout=8, max_retries=0)
    session = client.sessions.create(
        user_id=owner,
        toolkits=["gmail"],
        tools={"gmail": {"enable": [_TOOL]}},
        session_preset=SESSION_PRESET_DIRECT_TOOLS,
        manage_connections=False,
        sandbox={"enable": False},
        proxy_execute=False,
        mcp=True,
    )
    try:
        url, headers = _mcp_config(session)
        return asyncio.run(_read_once(url, headers, args))
    finally:
        # A temporary read session must not accumulate indefinitely at Composio.
        # Ignore cleanup errors without turning an unverified action into success.
        try:
            session.delete()
        except Exception:
            pass
