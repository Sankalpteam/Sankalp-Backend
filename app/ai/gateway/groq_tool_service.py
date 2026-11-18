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
    round_limit = max(1, min(int(max_tool_rounds), 6))
    max_calls = 8
    count = 0
    success_count = 0
    failure_count = 0
    timeout = httpx.Timeout(groq_service._TIMEOUT_SECONDS, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for round_index in range(round_limit + 1):
            source = tool_declarations() if callable(tool_declarations) else tool_declarations
            offered = _declared_tools([item for item in source if isinstance(item, dict)])
            permitted = {item["function"]["name"] for item in offered}
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": groq_service._MAX_TOKENS,
            }
            if round_index < round_limit and offered:
                payload["tools"] = offered
                payload["tool_choice"] = (
                    "required" if tool_mode == "ANY" and round_index == 0 else "auto"
                )
            response = await client.post(
                groq_service._ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code == 429:
                raise ModelBusyError(BUSY_MESSAGE)
            if response.status_code != 200:
                raise RuntimeError(f"Groq tool request returned HTTP {response.status_code}")
            try:
                data = response.json()
                assistant = data["choices"][0]["message"]
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                raise RuntimeError("Groq returned an invalid tool response") from exc
            if not isinstance(assistant, dict):
                raise RuntimeError("Groq returned an invalid assistant message")
            calls = assistant.get("tool_calls") or []
            if not isinstance(calls, list):
                raise RuntimeError("Groq returned malformed tool_calls")
            if not calls:
                content = assistant.get("content")
                answer = content.strip() if isinstance(content, str) else ""
                if count == 0 and return_none_when_no_tool and not reuse_first_text_when_no_tool:
                    return None
                if not answer:
                    raise RuntimeError("Groq returned an empty answer")
                if count > 0 and failure_count > 0:
                    return (
                        "The tool sequence was incomplete. Some operations could not "
                        "be verified; no further action was attempted."
                    )
                if count > 0 and success_count == 0:
                    return "I couldn't complete the requested tool operation. No success was verified."
                return answer

            if round_index >= round_limit:
                break
            messages.append({
                "role": "assistant",
                "content": assistant.get("content"),
                "tool_calls": calls,
            })
            for call in calls:
                count += 1
                fn = call.get("function") if isinstance(call, dict) else None
                fn = fn if isinstance(fn, dict) else {}
                name = str(fn.get("name") or "")
                call_id = (
                    str(call.get("id") or f"sankalp-tool-{count}")
                    if isinstance(call, dict) else f"sankalp-tool-{count}"
                )
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = None
                if count > max_calls:
                    result: dict[str, Any] = {"ok": False, "code": "TOOL_LIMIT_REACHED"}
                elif name not in permitted:
                    result = {"ok": False, "code": "UNKNOWN_TOOL"}
                elif not isinstance(args, dict):
                    result = {"ok": False, "code": "INVALID_TOOL_ARGUMENTS"}
                else:
                    try:
                        result = await execute_tool(name, args)
                        if not isinstance(result, dict):
                            result = {"ok": False, "code": "INVALID_TOOL_RESULT"}
                    except Exception:
                        logger.exception("Groq tool executor failed for %s", name)
                        result = {"ok": False, "code": "TOOL_EXECUTION_FAILED"}
                if result.get("ok") is True:
                    success_count += 1
                else:
                    failure_count += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
                })
            if count >= max_calls:
                round_limit = round_index + 1

    return (
        "I couldn't safely complete the tool sequence. "
        "No unverified action is reported as successful."
    )
