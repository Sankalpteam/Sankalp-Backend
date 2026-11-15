"""A delete that half worked must say so.

The trash loop counted a success for every call that did not raise, and stopped at the first
one that did. So a run that moved two of five mails to Trash raised an error and told the user
nothing at all - leaving them to believe no mail had moved, on the one operation where being
wrong about the state of their mailbox is least acceptable.
"""

from __future__ import annotations

import pytest

from app.plugins.email import email_assistant


@pytest.mark.asyncio
async def test_the_ones_that_moved_are_counted_and_the_failure_is_named(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_delete(_user, _account, _folder, message_id, **_kwargs):
        calls.append(message_id)

        if message_id == "m3":
            raise RuntimeError("Zoho Mail API failed: rate limited")

        return {"status": {"code": 200}}

    monkeypatch.setattr(email_assistant, "delete_message", fake_delete)

    items = [
        {"account_id": "a", "folder_id": "f", "id": "m1"},
        {"account_id": "a", "folder_id": "f", "id": "m2"},
        {"account_id": "a", "folder_id": "f", "id": "m3"},
        {"account_id": "a", "folder_id": "f", "id": "m4"},
    ]

    completed, failure = await email_assistant._delete_each("u", items, "id")

    assert completed == 2
    assert "rate limited" in failure
    # It stopped at the failure rather than hammering a rate-limited API.
    assert calls == ["m1", "m2", "m3"]


@pytest.mark.asyncio
async def test_a_clean_run_reports_no_failure(monkeypatch) -> None:
    async def fake_delete(*_args, **_kwargs):
        return {"status": {"code": 200}}

    monkeypatch.setattr(email_assistant, "delete_message", fake_delete)

    completed, failure = await email_assistant._delete_each(
        "u", [{"account_id": "a", "folder_id": "f", "id": "m1"}], "id"
    )

    assert completed == 1
    assert failure == ""


@pytest.mark.asyncio
async def test_the_message_names_both_numbers(monkeypatch) -> None:
    async def fake_delete(*_args, **_kwargs):
        raise RuntimeError("expired token")

    monkeypatch.setattr(email_assistant, "delete_message", fake_delete)

    reply = await email_assistant._execute_pending(
        "u",
        "zoho_mail",
        "delete_messages",
        {"items": [{"account_id": "a", "folder_id": "f", "id": "m1"}]},
    )

    assert "0 of 1" in reply
    assert "expired token" in reply
