"""Provider-isolated tests: Groq must never fabricate external tool completion.

No real provider credentials, connector accounts, network, or user data are used.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.ai.gateway import groq_service, groq_tool_service


def _declarations():
    return [{
        "name": "search_web",
        "description": "Find sourced results",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING"}},
            "required": ["query"],
        },
    }]


def _tool_call(name="search_web", arguments='{"query":"verified"}'):
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class _Response:
    status_code = 200

    def __init__(self, message):
        self.message = message

    def json(self):
        return {"choices": [{"message": self.message}]}


def _provider(monkeypatch, messages):
    payloads = []

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            payloads.append(json)
            return _Response(messages[len(payloads) - 1])

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(groq_service, "_get_config", lambda: ("test-key", "test-model"))
    monkeypatch.setattr(
        groq_service,
        "_messages",
        lambda message, history, context, instruction: [
            {"role": "system", "content": instruction},
            {"role": "user", "content": message},
        ],
    )
    return payloads


def _run(executor, **kwargs):
    return asyncio.run(groq_tool_service.generate_groq_function_call_reply(
        "Search the web",
        history=[],
        tool_declarations=_declarations(),
        execute_tool=executor,
        extra_system_instruction="",
        **kwargs,
    ))


def test_groq_runs_tools_through_dispatcher(monkeypatch):
    payloads = _provider(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": [_tool_call()]},
        {"role": "assistant", "content": "Verified result."},
    ])
    calls = []

    async def execute(name, args):
        calls.append((name, args))
        return {"ok": True, "result": [{"title": "source"}]}

    assert _run(execute) == "Verified result."
    assert calls == [("search_web", {"query": "verified"})]
    assert payloads[0]["tools"][0]["function"]["parameters"]["type"] == "object"
    assert payloads[1]["messages"][-1]["tool_call_id"] == "call-1"


def test_unknown_tool_is_never_dispatched(monkeypatch):
    _provider(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": [_tool_call("dangerous_write")]},
        {"role": "assistant", "content": "Done."},
    ])

    async def forbidden(*_args):
        raise AssertionError("unregistered tool executed")

    answer = _run(forbidden)
    assert "incomplete" in answer.lower()
    assert "done" not in answer.lower()


def test_required_tool_cannot_be_skipped(monkeypatch):
    payloads = _provider(monkeypatch, [{"role": "assistant", "content": "I sent it."}])

    async def forbidden(*_args):
        raise AssertionError("model never called the required tool")

    answer = _run(
        forbidden,
        tool_mode="ANY",
        return_none_when_no_tool=True,
        reuse_first_text_when_no_tool=False,
    )
    assert answer is None
    assert payloads[0]["tool_choice"] == "required"


def test_partial_tool_failure_is_not_described_as_success(monkeypatch):
    two_calls = [
        _tool_call(),
        {
            "id": "call-2",
            "type": "function",
            "function": {
                "name": "search_web",
                "arguments": '{"query":"other"}',
            },
        },
    ]
    _provider(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": two_calls},
        {"role": "assistant", "content": "Both succeeded."},
    ])

    async def execute(_name, args):
        return {"ok": args["query"] == "verified", "result": "source"}

    answer = _run(execute)
    assert "incomplete" in answer.lower()
    assert "both succeeded" not in answer.lower()


def test_plain_chat_uses_no_tool(monkeypatch):
    payloads = _provider(monkeypatch, [{"role": "assistant", "content": "Hello!"}])

    async def forbidden(*_args):
        raise AssertionError("tool called for ordinary chat")

    assert _run(forbidden) == "Hello!"
    assert payloads[0]["tool_choice"] == "auto"


def test_discovered_future_plugin_is_exposed_without_hardcoded_provider_names(monkeypatch):
    """New tools join the registry and are made callable on the next model round."""
    payloads = _provider(monkeypatch, [
        {
            "role": "assistant", "content": None,
            "tool_calls": [_tool_call("find_tools", '{"query":"future capability"}')],
        },
        {
            "role": "assistant", "content": None,
            "tool_calls": [_tool_call("new_plugin", '{"item":"example"}')],
        },
        {"role": "assistant", "content": "The registered tool returned the item."},
    ])
    discovered = []
    executed = []

    def declarations():
        tools = [{
            "name": "find_tools",
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING"}},
            },
        }]
        if discovered:
            tools.append({
                "name": "new_plugin",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"item": {"type": "STRING"}},
                },
            })
        return tools

    async def execute(name, args):
        executed.append(name)
        if name == "find_tools":
            discovered.append("new_plugin")
            return {"ok": True, "result": {"tools": [{"name": "new_plugin"}]}}
        assert name == "new_plugin"
        assert args == {"item": "example"}
        return {"ok": True, "result": "example"}

    answer = asyncio.run(groq_tool_service.generate_groq_function_call_reply(
        "Use my future capability",
        history=[],
        tool_declarations=declarations,
        execute_tool=execute,
        extra_system_instruction="",
    ))
    assert answer == "The registered tool returned the item."
    assert executed == ["find_tools", "new_plugin"]
    assert "new_plugin" not in [x["function"]["name"] for x in payloads[0]["tools"]]
    assert "new_plugin" in [x["function"]["name"] for x in payloads[1]["tools"]]
