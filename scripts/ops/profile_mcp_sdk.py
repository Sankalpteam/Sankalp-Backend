"""GitHub CI ONLY: import verified official MCP v2 APIs and measure own RSS.

Does not access remote MCP, credentials, user data or AWS infrastructure.
"""
from __future__ import annotations

import importlib.metadata
import json
import resource
from inspect import signature


def main() -> None:
    before = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from composio import Composio, SESSION_PRESET_DIRECT_TOOLS

    assert callable(getattr(ClientSession, "initialize", None))
    assert callable(getattr(ClientSession, "list_tools", None))
    assert callable(getattr(ClientSession, "call_tool", None))
    assert {"http_client", "max_sse_event_size", "terminate_on_close"}.issubset(
        signature(streamable_http_client).parameters
    )
    sdk = Composio(api_key="ci-not-a-real-key", timeout=3, max_retries=0)
    assert callable(getattr(sdk.sessions, "create", None))
    assert SESSION_PRESET_DIRECT_TOOLS == "direct_tools"
    after = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print(json.dumps({
        "label": "Optional SDK import on GitHub runner ONLY; not AWS server usage",
        "composio_version": importlib.metadata.version("composio"),
        "mcp_version": importlib.metadata.version("mcp"),
        "initial_rss_kib": before, "peak_rss_kib": after,
        "additional_rss_kib_upper_bound": max(0, after - before),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
