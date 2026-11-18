"""Bounded Gemini model-selected *read-only* planner for the legacy /api/agent.

The main chat path already uses Gemini/Groq LangGraph tool execution. This
compatibility bridge reuses that provider's function declarations and tool
selection without inventing a second scheduler or trusting model proposals to
authorize side effects. It returns at most one read-only AgentStep. Writes and
interactive approvals remain solely in the existing server-authenticated
LangGraph executor; this planner never dispatches a tool.

The planner is opt-in pending live credential and single-host performance tests:
INDOONE_AGENT_MODEL_PLANNER=true.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

import httpx

from app.ai.agents.agent import AgentStep, MAX_AGENT_PAYLOAD_LENGTH
from app.ai.execution.tool_intent import requires_tool_result
from app.ai.execution.tool_registry import get_tool_spec


def enabled() -> bool:
    return os.getenv("INDOONE_AGENT_MODEL_PLANNER", "false").strip().lower() == "true"


async def plan_model_read_steps(message: str, *, user_id: str = "") -> tuple[AgentStep, ...]:
    """One model call proposes a registered read tool; result is never authorization."""
    if not enabled() or not user_id.strip() or not requires_tool_result(message):
        return ()

    # Use the provider's shortlist and function-call parser, not a new homemade
    # agent engine or an unbounded full tool catalogue.
    from app.ai.execution.tool_loop import (
        _families_for, _find_tools_result, model_tool_declarations,
    )
    from app.ai.gateway import gemini_service as provider
    from app.ai.execution.connector_authorization import authorize_tool

    found: tuple[str, ...] = ()
    try:
        lookup = _find_tools_result(" ".join(message.split())[:200], user_id)
        if lookup.get("ok") is True:
            found = tuple(
                str(item["name"]) for item in
                (lookup.get("result") or {}).get("tools") or []
                if isinstance(item, dict) and item.get("name")
            )[:12]
    except Exception:
        # Failure to discover a tool must never cause unscoped execution.
        return ()

    offered = model_tool_declarations(
        user_id, discovered=found, families=_families_for(message),
    )
    read_only = []
    for item in offered:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        spec = get_tool_spec(name)
        if spec is not None and spec.risk == "read" and not spec.requires_approval and spec.model_facing:
            # Do not even offer connected-account tools that this authenticated
            # user cannot access. The graph executor rechecks at execution time.
            if authorize_tool(user_id, spec.name).allowed:
                read_only.append(item)
    if not read_only:
        return ()
    read_only = read_only[:12]
    names = {str(item["name"]) for item in read_only}

    try:
        api_key, model = provider._get_tool_config()
        url = f"{provider._API_BASE_URL}/models/{quote(model, safe='')}:generateContent"
        payload = {
            "contents": provider._build_contents(message, history=None, document_context=""),
            "systemInstruction": {
                "parts": [{
                    "text": (
                        "Select exactly one registered READ tool to retrieve the user's "
                        "requested live data. Never propose a write, fabricated tool, "
                        "account identity, or external action. The backend will validate "
                        "the selected name, input, and authenticated user's permissions."
                    )
                }]
            },
            "generationConfig": provider._generation_config(model),
            "tools": [{"functionDeclarations": read_only}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
            data = await asyncio.wait_for(
                provider._post_generate_content(
                    client, url, {"x-goog-api-key": api_key, "Content-Type": "application/json"}, payload,
                ),
                timeout=6.0,
            )
        _, calls = provider._extract_function_calls(data)
    except Exception:
        # Tool selection fails closed; provider details are not returned.
        # No provider error details or credentials should enter a user response.
        return ()

    # The provider response is a *proposal*. Never execute multiple model calls
    # here and never grant write permissions through a model-produced tool name.
    for call in calls[:1]:
        name = str(call.get("name") or "")
        if name not in names or call.get("invalid_arguments"):
            continue
        spec = get_tool_spec(name)
        arguments = call.get("args")
        if spec is None or spec.risk != "read" or spec.requires_approval or not isinstance(arguments, dict):
            continue
        from app.ai.execution.tool_loop import _core_payload
        serialized = _core_payload(spec, arguments)
        if not serialized or len(serialized) > MAX_AGENT_PAYLOAD_LENGTH:
            continue
        return (AgentStep(index=1, tool=name, payload=serialized, requires_approval=False),)
    return ()
