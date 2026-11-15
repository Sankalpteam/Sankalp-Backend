"""A channel bot answers strangers, so it may not answer as them.

Every channel had one owner id for the whole bot: "system:whatsapp", "system:telegram". Two
consequences, and the second is the serious one.

Connectors could never work on a channel - "system:telegram" has no GitHub or mailbox connected,
so every tool said "connect your account" no matter who was asking.

And pointing the bot at a real account, which is what an operator would do next, would have handed
that account's mailbox and repositories to every stranger who found the number.

The rule now: the owner is used only for senders on a trusted list. Everyone else is answered
under the channel's own scope, where no connectors exist.
"""

from __future__ import annotations

import pytest

from app.capabilities.platform.channel_common import channel_owner


def test_without_an_owner_the_channel_answers_as_itself(monkeypatch) -> None:
    monkeypatch.delenv("INDOONE_WHATSAPP_AI_OWNER_USER_ID", raising=False)

    owner, trusted = channel_owner("whatsapp", "9199", "INDOONE_WHATSAPP_AI_OWNER_USER_ID")

    assert owner == "system:whatsapp"
    assert trusted is False


def test_an_untrusted_sender_never_reaches_the_owner(monkeypatch) -> None:
    """The important one: a stranger must not read the owner's mail."""

    monkeypatch.setenv("INDOONE_WHATSAPP_AI_OWNER_USER_ID", "uid-owner")
    monkeypatch.setenv("INDOONE_WHATSAPP_AI_TRUSTED_SENDERS", "9199")

    stranger_owner, stranger_trusted = channel_owner(
        "whatsapp", "9100", "INDOONE_WHATSAPP_AI_OWNER_USER_ID"
    )

    assert stranger_owner == "system:whatsapp"
    assert stranger_trusted is False


def test_a_trusted_sender_gets_the_owner(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_WHATSAPP_AI_OWNER_USER_ID", "uid-owner")
    monkeypatch.setenv("INDOONE_WHATSAPP_AI_TRUSTED_SENDERS", "9199, 9188")

    owner, trusted = channel_owner("whatsapp", "9199", "INDOONE_WHATSAPP_AI_OWNER_USER_ID")

    assert owner == "uid-owner"
    assert trusted is True


def test_telegram_can_be_pointed_at_a_real_account(monkeypatch) -> None:
    """It used to be hardcoded, so no deployment could ever do this."""

    monkeypatch.setenv("INDOONE_TELEGRAM_AI_OWNER_USER_ID", "uid-owner")
    monkeypatch.setenv("INDOONE_TELEGRAM_AI_TRUSTED_SENDERS", "555")

    owner, trusted = channel_owner("telegram", "555", "INDOONE_TELEGRAM_AI_OWNER_USER_ID")

    assert owner == "uid-owner"
    assert trusted is True


def test_an_empty_trusted_list_trusts_nobody(monkeypatch) -> None:
    monkeypatch.setenv("INDOONE_TELEGRAM_AI_OWNER_USER_ID", "uid-owner")
    monkeypatch.delenv("INDOONE_TELEGRAM_AI_TRUSTED_SENDERS", raising=False)

    owner, trusted = channel_owner("telegram", "555", "INDOONE_TELEGRAM_AI_OWNER_USER_ID")

    assert owner == "system:telegram"
    assert trusted is False
