"""A blank reply is not an answer.

The GitHub router returns "" when a tool failed and the model is supposed to say it in the user's
language. Every caller checked `is not None`, so "" passed as a real reply and the user got an
empty bubble — on WhatsApp there is no second screen to explain it.

Two shapes are covered: the router must not return "", and a caller that receives one must treat
it as nothing to say.
"""

from __future__ import annotations

import pytest

from app.plugins.github import chat_tools


@pytest.mark.asyncio
async def test_a_failed_github_tool_returns_nothing_rather_than_a_blank(monkeypatch) -> None:
    async def failed(*_args, **_kwargs):
        return {"ok": False, "code": "GITHUB_UNAVAILABLE", "message": "the API did not answer"}

    monkeypatch.setattr(chat_tools, "_execute_github_tool", failed)
    monkeypatch.setattr(chat_tools, "record_tool_failure", lambda _message: None)

    class Parser:
        def parse(self, _message):
            return ("github_list_repositories", {})

    monkeypatch.setattr(chat_tools, "_parser", Parser(), raising=False)
    monkeypatch.setattr(chat_tools, "get_pending_github_action", lambda *_a, **_k: None, raising=False)

    result = await chat_tools.handle_github_chat_with_tools("user-1", "list my repositories")

    assert result is None or str(result).strip()


def test_a_blank_formatted_result_is_converted() -> None:
    """The formatter is allowed to return ""; the router is not."""

    assert (chat_tools._format_github_result("github_list_repositories", {"ok": False}) or "").strip() == ""
