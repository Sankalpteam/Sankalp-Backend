"""Regression tests for the actual LangGraph Gemini provider round replacement."""
from __future__ import annotations

import asyncio

from app.ai.gateway import gemini_service as service


def response(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


class FakeClient:
    def __init__(self, *, timeout):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        return None


def test_discovery_grows_offered_tools_across_graph_rounds(monkeypatch):
    sent, executed, count = [], [], [0]

    async def post(client, url, headers, payload):
        sent.append(payload)
        if len(sent) == 1:
            return response([{"functionCall": {"name": "find_tools", "args": {"query": "github"}, "id": "first"}}])
        if len(sent) == 2:
            return response([{"functionCall": {"name": "read_repo", "args": {"repo": "a/b"}, "id": "second"}}])
        return response([{"text": "Verified repository result."}])

    def declared():
        count[0] += 1
        items = [{"name": "find_tools", "parameters": {"type": "OBJECT", "properties": {}}}]
        if count[0] > 2:
            items.append({"name": "read_repo", "parameters": {"type": "OBJECT", "properties": {}}})
        return items

    async def execute(name, arguments):
        executed.append(name)
        return {"ok": True, "result": {"found": True}}

    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(service, "_get_tool_config", lambda: ("placeholder-test-key", "test-model"))
    monkeypatch.setattr(service, "_post_generate_content", post)
    result = asyncio.run(service.generate_gemini_function_call_reply(
        "Read my repo", history=None, tool_declarations=declared,
        execute_tool=execute, extra_system_instruction="", max_tool_rounds=2,
        return_none_when_no_tool=True,
    ))
    assert result == "Verified repository result."
    assert executed == ["find_tools", "read_repo"]
    assert count[0] == 3
    assert len(sent) == 3
    assert "tools" not in sent[-1]
    assert sent[1]["tools"][0]["functionDeclarations"][1]["name"] == "read_repo"


def test_unoffered_and_invalid_tool_calls_are_refused(monkeypatch):
    sent, executed = [], []

    async def post(client, url, headers, payload):
        sent.append(payload)
        if len(sent) == 1:
            return response([
                {"functionCall": {"name": "unoffered_write", "args": {}, "id": "a"}},
                {"functionCall": {"name": "read_only", "args": ["invalid"], "id": "b"}},
            ])
        return response([{"text": "Could not verify request."}])

    async def execute(name, arguments):
        executed.append(name)
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(service, "_get_tool_config", lambda: ("placeholder-test-key", "test-model"))
    monkeypatch.setattr(service, "_post_generate_content", post)
    result = asyncio.run(service.generate_gemini_function_call_reply(
        "Read safely", history=None,
        tool_declarations=[{"name": "read_only", "parameters": {"type": "OBJECT", "properties": {}}}],
        execute_tool=execute, extra_system_instruction="", max_tool_rounds=1,
        return_none_when_no_tool=True,
    ))
    assert result == "Could not verify request."
    assert executed == []
    replies = sent[1]["contents"][-1]["parts"]
    assert replies[0]["functionResponse"]["response"]["result"]["code"] == "UNKNOWN_TOOL"
    assert replies[1]["functionResponse"]["response"]["result"]["code"] == "INVALID_TOOL_ARGUMENTS"


def test_tool_free_first_reply_is_not_mailbox_evidence(monkeypatch):
    async def post(client, url, headers, payload):
        return response([{"text": "Your mailbox has four messages."}])

    async def forbidden(*args):
        raise AssertionError("tool must not execute")

    monkeypatch.setattr(service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(service, "_get_tool_config", lambda: ("placeholder-test-key", "test-model"))
    monkeypatch.setattr(service, "_post_generate_content", post)
    result = asyncio.run(service.generate_gemini_function_call_reply(
        "Read emails", history=None,
        tool_declarations=[{"name": "read_only", "parameters": {"type": "OBJECT", "properties": {}}}],
        execute_tool=forbidden, extra_system_instruction="",
    ))
    assert "couldn't verify the mailbox data" in result
    assert "four messages" not in result
