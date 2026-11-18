"""Groq provider uses LangGraph and never replays unapproved tool calls."""
from __future__ import annotations

import asyncio

from app.ai.gateway import groq_service, groq_tool_service


class FakeReply:
    def __init__(self, message):
        self.status_code = 200
        self.message = message

    def json(self):
        return {"choices": [{"message": self.message}]}


class FakeClient:
    replies = []
    sent = []

    def __init__(self, *, timeout):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, typ, val, tb):
        return None

    async def post(self, url, *, headers, json):
        self.sent.append(json)
        return FakeReply(self.replies[len(self.sent) - 1])


def test_groq_langgraph_model_tool_model_and_dynamic_discovery(monkeypatch):
    FakeClient.sent = []
    FakeClient.replies = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "function": {"name": "find_tools", "arguments": '{"query":"future"}'}}
        ]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "b", "function": {"name": "plugin_read", "arguments": '{"item":"x"}'}}
        ]},
        {"role": "assistant", "content": "Verified result."},
    ]
    discovered = []
    dispatched = []

    def declared():
        items = [{"name": "find_tools", "parameters": {"type": "OBJECT", "properties": {}}}]
        if discovered:
            items.append({"name": "plugin_read", "parameters": {"type": "OBJECT", "properties": {}}})
        return items

    async def execute(name, args):
        dispatched.append((name, args))
        if name == "find_tools":
            discovered.append("plugin_read")
        return {"ok": True, "result": "verified"}

    monkeypatch.setattr(groq_tool_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(groq_service, "_get_config", lambda: ("fake-key", "fake-model"))
    result = asyncio.run(groq_tool_service.generate_groq_function_call_reply(
        "Find and use plugin", history=None, tool_declarations=declared,
        execute_tool=execute, extra_system_instruction="", max_tool_rounds=3,
    ))
    assert result == "Verified result."
    assert [x[0] for x in dispatched] == ["find_tools", "plugin_read"]
    assert "plugin_read" not in [x["function"]["name"] for x in FakeClient.sent[0]["tools"]]
    assert "plugin_read" in [x["function"]["name"] for x in FakeClient.sent[1]["tools"]]
    assert len(FakeClient.sent) == 3


def test_groq_langgraph_does_not_fabricate_success_after_denied_write(monkeypatch):
    FakeClient.sent = []
    FakeClient.replies = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "function": {"name": "unoffered_write", "arguments": "{}"}}
        ]},
        {"role": "assistant", "content": "I completed that write."},
    ]
    attempted = []

    async def forbid(*args):
        attempted.append(args)
        raise AssertionError("An unoffered write cannot execute")

    monkeypatch.setattr(groq_tool_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(groq_service, "_get_config", lambda: ("fake-key", "fake-model"))
    result = asyncio.run(groq_tool_service.generate_groq_function_call_reply(
        "Try unoffered write", history=None,
        tool_declarations=[{"name": "read_only", "parameters": {"type": "OBJECT", "properties": {}}}],
        execute_tool=forbid, extra_system_instruction="",
    ))
    assert attempted == []
    assert "incomplete" in result.lower()
    assert "completed that write" not in result.lower()
