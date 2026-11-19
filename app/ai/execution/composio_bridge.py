"""Optional official Composio Python SDK bridge: a single Gmail READ tool.

Composio's cloud-managed OAuth is separate from the existing Sankalpa OAuth
store. This is opt-in, and it does not import the SDK or create an extra process
when disabled. The ONLY executable Composio tool is GMAIL_FETCH_EMAILS; neither
the model nor its arguments may choose a provider slug, account, or user ID.

Upstream: ComposioHQ/composio (MIT), python/README.md, Composio.create()
and ToolRouterSession.execute(tool_slug=..., arguments=...). Do not substitute
the older Composio tools.execute() return contract: session.execute success
is ONLY result_type == "completed".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit


# These are initialized lazily, only when this opt-in feature is used.
_EXECUTOR: ThreadPoolExecutor | None = None
_SLOTS = threading.BoundedSemaphore(2)
_GMAIL_READ_SLUG = "GMAIL_FETCH_EMAILS"
_MAX_RESULT_CHARS = 12_000


def enabled() -> bool:
    return (
        os.getenv("INDOONE_COMPOSIO_GMAIL_READ_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
        and bool(os.getenv("COMPOSIO_API_KEY", "").strip())
    )


def _identity(user_id: str) -> str:
    user = str(user_id or "").strip()
    if not user or len(user) > 256:
        raise ValueError("authenticated user is required")
    # Stable, pseudonymous and unique per Sankalpa account; no raw profile data.
    return "sankalpa-v1-" + hashlib.sha256(user.encode("utf-8")).hexdigest()


def _make_session(user_id: str) -> Any:
    key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Composio is not configured")
    # Real official SDK, lazily imported only after explicit configuration.
    from composio import Composio

    client = Composio(api_key=key)
    return client.create(
        user_id=_identity(user_id),
        toolkits=["gmail"],
        tools={"gmail": {"enable": [_GMAIL_READ_SLUG]}},
    )


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Do not pass model-supplied user_id, account_id or arbitrary tool params."""
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    query = arguments.get("query", "")
    if not isinstance(query, str) or len(query) > 300:
        raise ValueError("query must be a string of at most 300 characters")
    count = arguments.get("max_results", 5)
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("max_results must be between 1 and 20")
    return {
        "query": query,
        "max_results": count,
        "user_id": "me",
        "include_payload": False,
        "include_spam_trash": False,
    }


def _read(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    session = _make_session(user_id)
    result = session.execute(tool_slug=_GMAIL_READ_SLUG, arguments=arguments)
    if getattr(result, "result_type", None) != "completed":
        return {
            "ok": False, "code": "COMPOSIO_READ_UNVERIFIED",
            "message": "The Gmail read did not complete. No mailbox data was verified.",
        }
    try:
        encoded = json.dumps(getattr(result, "data", None), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"ok": False, "code": "COMPOSIO_INVALID_RESPONSE"}
    return {
        "ok": True,
        "result": {
            # User's own connected Gmail data: bounded before reaching the model.
            "data": encoded[:_MAX_RESULT_CHARS],
            "truncated": len(encoded) > _MAX_RESULT_CHARS,
        },
    }


def _link(user_id: str) -> str:
    session = _make_session(user_id)
    request = session.authorize("gmail")
    url = str(getattr(request, "redirect_url", "") or "")
    target = urlsplit(url)
    if target.scheme != "https" or target.hostname != "connect.composio.dev":
        raise RuntimeError("Composio returned an unexpected authorization URL")
    return url


async def _call_bounded(fn, *args) -> Any:
    """At most two background SDK requests; timed-out calls retain their slot.

    This intentionally does not put Composio in the main requirements, and
    does not create another Uvicorn worker, daemon, MCP server or LiteLLM proxy.
    """
    if not _SLOTS.acquire(blocking=False):
        raise RuntimeError("Composio request concurrency limit reached")
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="composio-read")

    def worker():
        try:
            return fn(*args)
        finally:
            _SLOTS.release()

    loop = asyncio.get_running_loop()
    try:
        pending = loop.run_in_executor(_EXECUTOR, worker)
    except BaseException:
        _SLOTS.release()
        raise
    return await asyncio.wait_for(pending, timeout=15.0)


async def gmail_read(user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not enabled():
        return {"ok": False, "code": "COMPOSIO_DISABLED", "message": "Composio Gmail is not enabled."}
    try:
        user = _identity(user_id)
        del user
        safe_arguments = _bounded_arguments(arguments)
        return await _call_bounded(_read, user_id, safe_arguments)
    except ValueError:
        return {"ok": False, "code": "COMPOSIO_INVALID_REQUEST"}
    except Exception:
        # Never echo SDK/network errors, API keys or hosted OAuth details.
        return {"ok": False, "code": "COMPOSIO_UNAVAILABLE", "message": "Gmail integration is unavailable."}


async def gmail_connect_link(user_id: str) -> str:
    if not enabled():
        raise RuntimeError("Composio Gmail is not enabled")
    _identity(user_id)
    return await _call_bounded(_link, user_id)
