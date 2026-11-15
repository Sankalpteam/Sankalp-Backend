"""One message, one answering path.

Seven places in the chat handler are willing to answer a message. They are chained with
`if tool_reply is None`, so one of them answers in practice - but nothing recorded which, and
nothing noticed if two did. That is the difference between "it works" and "we know why it works".
"""

from __future__ import annotations

import logging

import pytest

from app.ai.execution import executor_claim


@pytest.fixture(autouse=True)
def _clean() -> None:
    executor_claim._CLAIM.clear()


def test_the_first_claim_names_the_executor() -> None:
    executor_claim.claim("turn-1", "email_read_fast_path")

    assert executor_claim.claimed_by("turn-1") == "email_read_fast_path"


def test_a_second_different_claim_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="indoone.executor"):
        executor_claim.claim("turn-2", "web_research")
        executor_claim.claim("turn-2", "tool_loop")

    assert "two executors answered one turn" in caplog.text
    assert "web_research" in caplog.text
    assert "tool_loop" in caplog.text
    # The later answer is the one the user saw, so the claim follows it rather than the first.
    assert executor_claim.claimed_by("turn-2") == "tool_loop"


def test_the_same_executor_claiming_twice_is_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="indoone.executor"):
        executor_claim.claim("turn-3", "tool_loop")
        executor_claim.claim("turn-3", "tool_loop")

    assert caplog.text == ""


def test_a_turn_with_no_claim_has_no_executor() -> None:
    assert executor_claim.claimed_by("never-claimed") is None


def test_an_empty_turn_id_is_ignored() -> None:
    executor_claim.claim("", "tool_loop")

    assert executor_claim.claimed_by("") is None


def test_forgetting_a_turn_lets_the_next_message_start_clean() -> None:
    executor_claim.claim("turn-4", "agent_planner")
    executor_claim.forget("turn-4")

    assert executor_claim.claimed_by("turn-4") is None
