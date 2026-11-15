"""The channel cap existed and nothing called it.

`22_omnichannel_quota_guard` holds the free WhatsApp ceiling - 900 messages a month - and the
"download the app to keep chatting" line that is supposed to follow it. No module outside its
own tests ever called it, so channel traffic was never counted and the cap could not fire.

A channel that never counts cannot charge, and the funnel the cap exists for never opened.
"""

from __future__ import annotations

import pytest

from app.capabilities.platform import channel_common


def test_the_guard_is_given_an_identifier_it_understands() -> None:
    """It keys on wa:/tg:/ig:, not on the channel's own name."""

    assert channel_common._quota_identifier("whatsapp", "9199") == "wa:9199"
    assert channel_common._quota_identifier("telegram", "555") == "tg:555"
    assert channel_common._quota_identifier("instagram", "777") == "ig:777"
    assert channel_common._quota_identifier("app", "x") == ""


def test_a_spent_month_stops_the_reply(monkeypatch) -> None:
    class Verdict:
        is_allowed = False
        action_chips = []
        message = "This month's free limit is used up."

    class Guard:
        def enabled(self):
            return True

        def check_quota(self, _identifier):
            return Verdict()

    monkeypatch.setattr(channel_common, "load_agent", lambda _name: Guard())

    verdict = channel_common._channel_quota("whatsapp", "9199")

    assert verdict is not None
    assert verdict.is_allowed is False
    assert channel_common._quota_message("whatsapp", verdict) == (
        "This month's free limit is used up."
    )


def test_a_guard_that_cannot_run_does_not_silence_the_channel(monkeypatch) -> None:
    def explode(_name):
        raise RuntimeError("the store is down")

    monkeypatch.setattr(channel_common, "load_agent", explode)

    assert channel_common._channel_quota("whatsapp", "9199") is None


def test_usage_is_recorded_after_an_answer(monkeypatch) -> None:
    recorded: list[str] = []

    class Guard:
        def record_usage(self, identifier):
            recorded.append(identifier)

    monkeypatch.setattr(channel_common, "load_agent", lambda _name: Guard())

    channel_common._record_channel_usage("whatsapp", "9199")

    assert recorded == ["wa:9199"]
