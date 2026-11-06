"""The supervisors are wired into the chat path, not just built.

Before this, agents 11-15 existed and nothing called them: every tool turn went through the
older plugin code, so the perimeter, the sanitizers and the envelopes guarded nothing.
"""

from __future__ import annotations

import pytest

from app.agents import load

web = load("14_web_search_supervisor")


class FakeResult:
    """The shape execute_search_web_call returns, without the network."""

    def __init__(self, status: str, results: list[dict]) -> None:
        self.status = status
        self.results = [
            type(
                "Item",
                (),
                {"title": item["title"], "url": item["url"], "snippet": item["snippet"]},
            )()
            for item in results
        ]


def _patch_search(monkeypatch, payload):
    async def fake_call(call):
        return payload

    import app.web_research.tools as tools

    monkeypatch.setattr(tools, "execute_search_web_call", fake_call)


@pytest.mark.asyncio
async def test_search_web_runs_through_the_supervisor(monkeypatch) -> None:
    """The answer comes back in the supervisor's shape, which is the proof it ran."""
    from app.ai.execution import tool_loop

    _patch_search(
        monkeypatch,
        FakeResult(
            "ok",
            [
                {"title": "Karnataka holidays", "url": "https://example.com/h", "snippet": "Deepavali"},
            ],
        ),
    )

    result = await tool_loop.dispatch_tool("u", "c", "search_web", {"query": "karnataka holidays"})

    assert result["ok"] is True
    assert result["result"]["results"][0]["title"] == "Karnataka holidays"
    # The supervisor adds the domain, which the old path never did.
    assert result["result"]["results"][0]["domain"] == "example.com"


@pytest.mark.asyncio
async def test_tracking_parameters_are_stripped_by_the_perimeter(monkeypatch) -> None:
    from app.ai.execution import tool_loop

    _patch_search(
        monkeypatch,
        FakeResult(
            "ok",
            [
                {
                    "title": "A page",
                    "url": "https://example.com/p?utm_source=news&utm_medium=email",
                    "snippet": "text",
                },
            ],
        ),
    )

    result = await tool_loop.dispatch_tool("u", "c", "search_web", {"query": "a page"})

    assert result["result"]["results"][0]["url"] == "https://example.com/p"


@pytest.mark.asyncio
async def test_a_private_host_is_dropped_before_it_reaches_the_model(monkeypatch) -> None:
    """The supervisor's perimeter is now the one guarding the live tool."""
    from app.ai.execution import tool_loop

    _patch_search(
        monkeypatch,
        FakeResult(
            "ok",
            [
                {"title": "internal", "url": "http://127.0.0.1:8000/admin", "snippet": "secret"},
                {"title": "public", "url": "https://example.com/ok", "snippet": "fine"},
            ],
        ),
    )

    result = await tool_loop.dispatch_tool("u", "c", "search_web", {"query": "admin"})

    urls = [item["url"] for item in result["result"]["results"]]
    assert urls == ["https://example.com/ok"]


@pytest.mark.asyncio
async def test_a_failed_search_is_reported_not_raised(monkeypatch) -> None:
    from app.ai.execution import tool_loop

    _patch_search(monkeypatch, FakeResult("error", []))

    result = await tool_loop.dispatch_tool("u", "c", "search_web", {"query": "nothing"})

    assert result["ok"] is False
    assert result["code"].startswith("SEARCH_")
    assert result["message"]
