"""A channel answers a GitHub question the same way the app does.

The app has run a deterministic GitHub router since the invented-branch fix: a repository
question never reaches the model, because a model answering from memory produces branch names
that never came from the API. The channels never got that router, so the same question on
WhatsApp was answered from memory — the one place it is hardest to notice, because there is no
second screen to check it against.
"""

from __future__ import annotations

import pytest

from app.capabilities.platform import channel_common


@pytest.mark.asyncio
async def test_a_github_question_is_answered_by_the_router_on_a_channel(monkeypatch) -> None:
    """The router's answer is returned, and the model is never asked."""

    def fake_frontline(*_args, **_kwargs):
        return None

    async def fake_router(_user_id, _text, **_kwargs):
        return "You have 3 repositories: sankalp, indoone, eyesearch."

    async def fake_tool_loop(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the model must not be asked once the router answered")

    monkeypatch.setattr(channel_common, "_frontline", fake_frontline)
    monkeypatch.setattr(channel_common, "handle_github_chat_with_tools", fake_router)
    monkeypatch.setattr(channel_common, "run_tool_loop", fake_tool_loop)
    monkeypatch.setattr(channel_common, "festival_answer", lambda _text: None)

    async def no_history(*_args, **_kwargs):
        return None

    monkeypatch.setattr(channel_common, "conversation_history_answer", no_history)
    monkeypatch.setattr(channel_common, "email_read_fast_path", no_history)
    monkeypatch.setattr(channel_common, "web_research_answer", no_history)

    async def no_pending(*_args, **_kwargs):
        return None

    monkeypatch.setattr(channel_common, "_resolve_pending_write", no_pending)
    monkeypatch.setattr(channel_common, "_instruction", lambda _user: "")

    class Store:
        def recent(self, *_args, **_kwargs):
            return []

        def append(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(channel_common, "ConversationStore", Store)
    monkeypatch.setattr(channel_common, "without_stale_refusals", lambda history, _user: history)

    reply = await channel_common._generate_with_history(
        "user-1", "whatsapp", "919999999999", "list my repositories"
    )

    assert reply == "You have 3 repositories: sankalp, indoone, eyesearch."
