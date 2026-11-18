"""Groq function-calling adapter using the existing authenticated tool dispatcher.

Model selection is separate from execution: connector permissions, approvals and
idempotency remain enforced by app.ai.execution.tool_loop.dispatch_tool.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from app.ai.gateway import groq_service
from app.ai.gateway.ai_rules import TOOL_SYSTEM_INSTRUCTION
from app.ai.gateway.gemini_service import BUSY_MESSAGE, ModelBusyError

logger = logging.getLogger("indoone.groq.tools")
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate Gemini tool declarations into OpenAI JSON Schema."""
    allowed = {
        "type", "description", "properties", "items", "required", "enum",
        "minimum", "maximum", "minItems", "maxItems", "format",
    }
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = value.lower()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {
                str(name): _json_schema(prop)
                for name, prop in value.items() if isinstance(prop, dict)
            }
        elif key == "items" and isinstance(value, dict):
            out[key] = _json_schema(value)
        else:
            out[key] = value
    if out.get("type") == "array" and "items" not in out:
        out["items"] = {"type": "string"}
    return out


def _declared_tools(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for item in declarations:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        schema = item.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(item.get("description") or ""),
                "parameters": _json_schema(schema),
            },
        })
    return tools


async def generate_groq_function_call_reply(
    message: str,
    *,
    history: list[tuple[str, str]] | None,
    tool_declarations: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]],
    execute_tool: ToolExecutor,
    extra_system_instruction: str,
    max_tool_rounds: int = 4,
    tool_mode: str = "AUTO",
    return_none_when_no_tool: bool = False,
    reuse_first_text_when_no_tool: bool = False,
) -> str | None:
    """Bounded Groq tool calls with backend-owned authorization and approvals."""
    if not message.strip():
        raise ValueError("message cannot be empty")

    api_key, model = groq_service._get_config()
    instructions = TOOL_SYSTEM_INSTRUCTION
    if extra_system_instruction.strip():
        instructions += "\n\n" + extra_system_instruction.strip()
    messages = groq_service._messages(message, history, "", instructions)
    messages[0]["content"] = instructions
    # Source-replacement checkpoint: LangGraph owns provider model/tool rounds.
    # Authentication and tool side effects remain in the caller's secure callback.
    from app.ai.gateway.groq_tool_graph import run_groq_tool_graph

    timeout = httpx.Timeout(groq_service._TIMEOUT_SECONDS, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await run_groq_tool_graph(
            client=client,
            api_key=api_key,
            model=model,
            initial_messages=messages,
            tool_declarations=tool_declarations,
            execute_tool=execute_tool,
            tool_mode=tool_mode,
            max_tool_rounds=max_tool_rounds,
            return_none_when_no_tool=return_none_when_no_tool,
            reuse_first_text_when_no_tool=reuse_first_text_when_no_tool,
        )
