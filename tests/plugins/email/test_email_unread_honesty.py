"""An unread count is a claim about the mailbox, so it may only be made from data.

The user asked for unread mail and got eight mails back, six of which were read. Nothing in the
path looked at the read state at all: "unread" was treated as a word that means "list recent",
and the reply then implied all eight were unread.

These tests pin the three possible states: the provider says unread, the provider says read, and
the provider says nothing - which must never be reported as unread.
"""

from __future__ import annotations

from app.plugins.email.email_assistant import _format_results, _unread_state, _wants_unread


def test_the_provider_flag_is_read_in_both_directions() -> None:
    assert _unread_state({"isRead": False}) == "yes"
    assert _unread_state({"isRead": True}) == "no"
    assert _unread_state({"isUnread": True}) == "yes"
    assert _unread_state({"status": "unread"}) == "yes"
    assert _unread_state({"status": "read"}) == "no"


def test_a_missing_flag_is_unknown_and_not_unread() -> None:
    assert _unread_state({}) == ""
    assert _unread_state({"subject": "anything"}) == ""


def test_only_an_unread_list_is_called_unread() -> None:
    row = {"subject": "A", "from": "a@b.com", "date": "2026-10-27T18:58:55Z"}

    unread = _format_results([{**row, "unread": "yes"}], "Zoho Mail")
    unknown = _format_results([{**row, "unread": ""}], "Zoho Mail")

    assert "1 unread email" in unread
    assert "unread" not in unknown


def test_the_question_is_recognised_in_english_and_kannada() -> None:
    assert _wants_unread("how many unread mails do I have") is True
    assert _wants_unread("ಓದದ ಮೇಲ್ ಎಷ್ಟಿದೆ") is True
    assert _wants_unread("nanna mail nodu") is False
