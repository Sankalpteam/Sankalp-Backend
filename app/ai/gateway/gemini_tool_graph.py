"""MIT LangGraph StateGraph for Gemini function-calling rounds.

The model/tools conditional-edge pattern comes from the MIT-licensed
fastapi-langgraph-agent-production-ready-template, upstream commit
fe052a7c6acf692c05ca0e0a50c0ff2ffba448b7 (graph.py).
Sankalpa's authenticated execute_tool callback is the sole side-effect owner.
No graph checkpoint replay or speculative parallel writes are permitted.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph

from app.ai.gateway import gemini_service as provider
from app.ai.gateway.ai_rules import (
    INVALID_TOOL_ARGUMENTS, INVALID_TOOL_RESULT, TOOL_EXECUTION_FAILED,
    TOOL_LIMIT_REACHED, UNKNOWN_TOOL,
)


class _State(TypedDict):
    contents: list[dict[str, Any]]
    round_index: int
    executed_calls: int
    offered_names: tuple[str, ...]
    model_content: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    answer: str | None
    done: bool


async def run_gemini_tool_graph(
    *,
    client: Any,
    url: str,
    headers: dict[str, str],
    model: str,
    system_instruction: str,
    initial_contents: list[dict[str, Any]],
    declarations: Callable[[], list[dict[str, Any]]],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    max_tool_rounds: int,
    tool_mode: str,
    return_none_when_no_tool: bool,
    reuse_first_text_when_no_tool: bool,
) -> str | None:
    """Replace the hand-rolled provider round loop with official graph scheduling.

    Dynamically discovered tools are offered at each model step. Calls are
    executed sequentially with existing backend authorization and deduplication.
    """
    round_cap = max(1, min(int(max_tool_rounds), 6))

    async def model_step(state: _State) -> dict[str, Any]:
        turn = state["round_index"]
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": state["contents"],
            "generationConfig": provider._generation_config(model),
        }
        names: tuple[str, ...] = ()
        if turn < round_cap:
            offered = declarations()
            names = tuple(str(item["name"]) for item in offered)
            payload["tools"] = [{"functionDeclarations": offered}]
            if turn == 0:
                payload["toolConfig"] = {"functionCallingConfig": {"mode": tool_mode}}

        data = await provider._post_generate_content(client, url, headers, payload)
        content, calls = provider._extract_function_calls(data)
        if not calls:
            if state["executed_calls"] == 0:
                if return_none_when_no_tool:
                    if not reuse_first_text_when_no_tool:
                        return {"answer": None, "done": True}
                    try:
                        answer = provider._extract_text(data)
                    except RuntimeError:
                        answer = None
                    return {"answer": answer or None, "done": True}
                return {
                    "answer": "I couldn't verify the mailbox data because no email tool was executed. "
                              "Please retry; I won't guess about your emails.",
                    "done": True,
                }
            return {"answer": provider._extract_text(data), "done": True}

        if turn >= round_cap:
            return {
                "answer": "I couldn't complete that email request safely because the tool "
                          "sequence did not finish. No unconfirmed send or delete was performed.",
                "done": True,
            }
        content["role"] = "model"
        parts = content.get("parts")
        if isinstance(parts, list):
            content["parts"] = [
                part for part in parts
                if not (isinstance(part, dict) and part.get("thought") is True)
            ]
        return {
            "model_content": content, "tool_calls": calls,
            "offered_names": names, "done": False,
        }

    async def tool_step(state: _State) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        count = state["executed_calls"]
        allowed = frozenset(state["offered_names"])
        for call in state["tool_calls"]:
            name, call_id = str(call["name"]), str(call.get("id") or "")
            if count >= 8:
                result: dict[str, Any] = {
                    "ok": False, "code": "TOOL_LIMIT_REACHED", "message": TOOL_LIMIT_REACHED,
                }
            elif name not in allowed:
                result = {"ok": False, "code": "UNKNOWN_TOOL", "message": UNKNOWN_TOOL}
            elif call.get("invalid_arguments"):
                result = {
                    "ok": False, "code": "INVALID_TOOL_ARGUMENTS",
                    "message": INVALID_TOOL_ARGUMENTS,
                }
            else:
                try:
                    result = await execute_tool(name, call["args"])
                    if not isinstance(result, dict):
                        result = {
                            "ok": False, "code": "INVALID_TOOL_RESULT",
                            "message": INVALID_TOOL_RESULT,
                        }
                except Exception:
                    result = {
                        "ok": False, "code": "TOOL_EXECUTION_FAILED",
                        "message": TOOL_EXECUTION_FAILED,
                    }
            count += 1
            response: dict[str, Any] = {"name": name, "response": {"result": result}}
            if call_id:
                response["id"] = call_id
            responses.append({"functionResponse": response})
        return {
            "contents": state["contents"] + [
                state["model_content"], {"role": "user", "parts": responses},
            ],
            "executed_calls": count,
            "round_index": state["round_index"] + 1,
        }

    def route(state: _State) -> str:
        return END if state["done"] else "tools"

    builder = StateGraph(_State)
    builder.add_node("model", model_step)
    builder.add_node("tools", tool_step)
    builder.set_entry_point("model")
    builder.add_conditional_edges("model", route)
    builder.add_edge("tools", "model")
    graph = builder.compile()
    state = await graph.ainvoke(
        {
            "contents": list(initial_contents),
            "round_index": 0, "executed_calls": 0,
            "offered_names": (), "model_content": {}, "tool_calls": [],
            "answer": None, "done": False,
        },
        config={"recursion_limit": (round_cap + 1) * 2 + 4},
    )
    return state["answer"]
