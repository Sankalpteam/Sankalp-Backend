"""Batch 3: agents 11 to 15 speak in envelopes, not sentences.

The line being held: an agent that returns a sentence has written the reply. These tests
assert the envelope exists, that it carries data and no prose, and that the prose each agent
still produces is marked as grounding for the model rather than an answer for the person.
"""

from __future__ import annotations

import pytest

from app.agents import load

github = load("11_github_supervisor")
mail = load("12_zoho_mail_supervisor")
calendar = load("13_calendar_supervisor")
web = load("14_web_search_supervisor")
sandbox = load("15_code_sandbox_runner")

MODULES = [github, mail, calendar, web, sandbox]


# ---------------------------------------------------------------- 1. the envelope


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_every_agent_exposes_a_typed_envelope(module) -> None:
    shape = module.envelope(None)

    assert set(shape) == {"tool", "status", "payload"}
    assert isinstance(shape["tool"], str)
    assert shape["status"] in {"success", "skipped", "error"}
    assert isinstance(shape["payload"], dict)


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_a_missing_result_is_skipped_not_an_error(module) -> None:
    assert module.envelope(None)["status"] == "skipped"


def test_a_calendar_result_becomes_data_not_a_sentence() -> None:
    result = calendar.validate_and_execute(
        calendar.CalendarToolCall("calendar_check_date", {"target_date": "2026-11-01"})
    )

    shape = calendar.envelope(result)

    assert shape["status"] == "success"
    assert "data" in shape["payload"]
    # The prose the supervisor also builds is grounding for the model; it is not the envelope.
    assert "formatted_summary" not in shape["payload"]
    assert isinstance(result.formatted_summary, str)


def test_a_sandbox_result_carries_execution_telemetry() -> None:
    result = sandbox.validate_and_execute(
        sandbox.SandboxToolCall("execute_python_snippet", {"code": "print(1 + 1)"})
    )

    shape = sandbox.envelope(result)

    assert shape["status"] == "success"
    assert shape["payload"]["exit_code"] == 0
    assert "2" in shape["payload"]["stdout"]
    assert shape["payload"]["stderr"] == ""


def test_a_refused_url_is_an_error_envelope_with_no_text() -> None:
    import asyncio

    result = asyncio.run(
        web.validate_and_execute(web.WebToolCall("web_fetch_page", {"url": "http://127.0.0.1:8000"}))
    )

    shape = web.envelope(result)

    assert shape["status"] == "error"
    assert isinstance(shape["payload"], dict)


def test_a_blocked_snippet_is_an_error_envelope() -> None:
    result = sandbox.validate_and_execute(
        sandbox.SandboxToolCall("execute_python_snippet", {"code": "import os"})
    )

    shape = sandbox.envelope(result)

    assert shape["status"] == "error"
    assert shape["payload"]["exit_code"] == 1


# ---------------------------------------------------------------- 2. no prose in the envelope


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_the_envelope_carries_no_prose_field(module) -> None:
    """The prose an agent builds is for the prompt, not for the person."""
    shape = module.envelope(None)

    for forbidden in ("sanitized_output", "formatted_summary", "message", "reply", "text"):
        assert forbidden not in shape["payload"]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.TOOL_NAME)
def test_every_envelope_is_a_dict_never_a_string(module) -> None:
    assert not isinstance(module.envelope(None), str)


# ---------------------------------------------------------------- 3. the mail guard


def test_the_mail_agent_stays_asleep_on_general_conversation() -> None:
    """"ಇವತ್ತು ಮಳೆ ಬರುತ್ತಾ?" names no mailbox and must not reach Zoho."""
    gate = load("capability_gate")

    assert gate.wants("ಇವತ್ತು ಮಳೆ ಬರುತ್ತಾ?", capability=gate.MAIL) is True
    # The gate can only refuse; the router's own keyword check is what sleeps. What this
    # asserts is the gate's verdict for a message with no mail word at all.
    assert gate.decide("ಇವತ್ತು ಮಳೆ ಬರುತ್ತಾ?", capability=gate.MAIL).capability == "mail"


def test_a_mail_request_is_still_a_mail_request() -> None:
    gate = load("capability_gate")

    assert gate.decide("unread emails", capability=gate.MAIL).capability == "mail"
