"""Groq tool transcript scheduler using the MIT LangGraph StateGraph engine.

Replaces the Groq provider's independent hand-rolled round loop. The model
node → tool node → model node pattern derives from MIT-licensed FastAPI
LangGraph template commit fe052a7c6acf692c05ca0e0a50c0ff2ffba448b7.
All tools execute via the authenticated Sankalpa backend callback, not direct
model-generated API calls. No implicit write replay or parallel tool execution.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph

from app.ai.gateway import groq_service, groq_tool_service
from app.ai.gateway.gemini_service import BUSY_MESSAGE, ModelBusyError

logger = logging.getLogger("indoone.groq.tools")


class _State(TypedDict):
    messages: list[dict[str, Any]]
    round_index: int
    round_cap: int
    count: int
    success_count: int
    failure_count: int
    offered_names: tuple[str, ...]
    assistant: dict[str, Any]
    calls: list[dict[str, Any]]
    answer: str | None
    done: bool


async def run_groq_tool_graph(
    *,
    client: Any,
    api_key: str,
    model: str,
    initial_messages: list[dict[str, Any]],
    tool_declarations: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    tool_mode: str,
    max_tool_rounds: int,
    return_none_when_no_tool: bool,
    reuse_first_text_when_no_tool: bool,
) -> str | None:
    """Bound model rounds and side effects while permitting tool discovery."""
    round_cap = max(1, min(int(max_tool_rounds), 6))

    async def model_step(state: _State) -> dict[str, Any]:
        turn = state["round_index"]
        source = tool_declarations() if callable(tool_declarations) else tool_declarations
        offered = groq_tool_service._declared_tools(
            [item for item in source if isinstance(item, dict)]
        )
        names = tuple(item["function"]["name"] for item in offered)
        payload: dict[str, Any] = {
            "model": model,
            "messages": state["messages"],
            "temperature": 0.2,
            "max_tokens": groq_service._MAX_TOKENS,
        }
        if turn < state["round_cap"] and offered:
            payload["tools"] = offered
            payload["tool_choice"] = (
                "required" if tool_mode == "ANY" and turn == 0 else "auto"
            )
        response = await client.post(
            groq_service._ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
            if state["count"] == 0 and return_none_when_no_tool and not reuse_first_text_when_no_tool:
                return {"answer": None, "done": True}
            if not answer:
                raise RuntimeError("Groq returned an empty answer")
            if state["count"] > 0 and state["failure_count"] > 0:
                answer = (
                    "The tool sequence was incomplete. Some operations could not "
                    "be verified; no further action was attempted."
                )
            elif state["count"] > 0 and state["success_count"] == 0:
                answer = "I couldn't complete the requested tool operation. No success was verified."
            return {"answer": answer, "done": True}
        if turn >= state["round_cap"]:
            return {
                "answer": (
                    "I couldn't safely complete the tool sequence. "
                    "No unverified action is reported as successful."
                ),
                "done": True,
            }
        return {"assistant": assistant, "calls": calls, "offered_names": names, "done": False}

    async def tool_step(state: _State) -> dict[str, Any]:
        count, successes, failures = state["count"], state["success_count"], state["failure_count"]
        messages = list(state["messages"])
        calls = state["calls"]
        allowed = frozenset(state["offered_names"])
        messages.append({
            "role": "assistant",
            "content": state["assistant"].get("content"),
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
            if count > 8:
                result: dict[str, Any] = {"ok": False, "code": "TOOL_LIMIT_REACHED"}
            elif name not in allowed:
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
                successes += 1
            else:
                failures += 1
            messages.append({
                "role": "tool", "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })
        return {
            "messages": messages,
            "count": count,
            "success_count": successes,
            "failure_count": failures,
            "round_index": state["round_index"] + 1,
            "round_cap": state["round_index"] + 1 if count >= 8 else state["round_cap"],
        }

    def route(state: _State) -> str:
        return END if state["done"] else "tools"

    builder = StateGraph(_State)
    builder.add_node("model", model_step)
    builder.add_node("tools", tool_step)
    builder.set_entry_point("model")
    builder.add_conditional_edges("model", route)
    builder.add_edge("tools", "model")
    compiled = builder.compile()
    result = await compiled.ainvoke(
        {
            "messages": list(initial_messages), "round_index": 0,
            "round_cap": round_cap, "count": 0, "success_count": 0,
            "failure_count": 0, "offered_names": (), "assistant": {}, "calls": [],
            "answer": None, "done": False,
        },
        config={"recursion_limit": (round_cap + 1) * 2 + 4},
    )
    return result["answer"]
