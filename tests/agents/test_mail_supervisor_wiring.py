"""Agent 12 sanitises every mail result on the live path.

A mail body is the one payload in this system written by a stranger: an OTP, a reset token,
a tracking pixel. The model reads all of it unless something strips it first.
"""

from __future__ import annotations

import pytest

from app.agents import load

mail = load("12_zoho_mail_supervisor")


def test_the_sanitiser_redacts_an_otp() -> None:
    assert "482913" not in mail.sanitize_mail_body("Your OTP is 482913.")


def test_it_redacts_a_reset_token() -> None:
    cleaned = mail.sanitize_mail_body("Reset: https://app.example.com/reset?token=abc123secret")

    assert "abc123secret" not in cleaned


def test_it_leaves_ordinary_prose_alone() -> None:
    body = "Hi Asha, the invoice is attached. Thanks."

    assert mail.sanitize_mail_body(body) == body


@pytest.mark.asyncio
async def test_the_live_dispatcher_sanitises_every_text_field(monkeypatch) -> None:
    """The proof it is wired: the tool result the model receives carries no OTP."""
    from app.plugins.email import email_tools

    async def fake_raw(*args, **kwargs):
        return {
            "ok": True,
            "messages": [
                {"subject": "Your code", "body": "Your OTP is 482913.", "from": "bank@example.com"}
            ],
        }

    monkeypatch.setattr(email_tools, "_execute_email_tool_unsanitized", fake_raw)

    result = await email_tools.execute_email_tool("u", "read_email", {"message_id": "1"})

    assert "482913" not in str(result)
    assert "[OTP Redacted]" in result["messages"][0]["body"]
    # What is not text is untouched.
    assert result["messages"][0]["from"] == "bank@example.com"


@pytest.mark.asyncio
async def test_a_nested_body_is_sanitised_too(monkeypatch) -> None:
    from app.plugins.email import email_tools

    async def fake_raw(*args, **kwargs):
        return {"ok": True, "thread": {"messages": [{"body": "code 771204"}]}}

    monkeypatch.setattr(email_tools, "_execute_email_tool_unsanitized", fake_raw)

    result = await email_tools.execute_email_tool("u", "read_email", {})

    assert "771204" not in str(result)


@pytest.mark.asyncio
async def test_a_failure_result_passes_through(monkeypatch) -> None:
    from app.plugins.email import email_tools

    async def fake_raw(*args, **kwargs):
        return {"ok": False, "code": "NO_PROVIDER", "message": "Connect Zoho in Plugins."}

    monkeypatch.setattr(email_tools, "_execute_email_tool_unsanitized", fake_raw)

    result = await email_tools.execute_email_tool("u", "read_email", {})

    assert result["ok"] is False
    assert result["code"] == "NO_PROVIDER"
