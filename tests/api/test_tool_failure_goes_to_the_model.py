"""A failed tool call must not answer in the assistant's voice.

An older design handed tool results to the model and the model reworded them - it reported a
branch as created when the call had not run. So tool output stopped going through a model.

That rule is right for a success, where the data must arrive exactly as it came back. It was
wrong for a failure, where there is no data to protect: the tool wrote its own English
sentence, "The GitHub request could not be completed.", and the chat sent it as the
assistant's reply - in English, in the middle of a Kannada conversation.

So the rule is split by outcome. Success: the tool's text, untouched. Failure: no sentence
from the tool; the reason goes to the model, which says it in the user's language.

These tests are the guard on both halves.
"""

from __future__ import annotations

import inspect

from app.ai.execution.tool_failure import (
    clear_tool_failure,
    failure_line,
    record_tool_failure,
    take_tool_failure,
)
from app.plugins.github.chat_tools import _format_github_result


def test_a_failed_call_returns_no_user_facing_sentence() -> None:
    clear_tool_failure()
    text = _format_github_result("github_list_repositories", {"ok": False, "message": "GitHub said 403"})

    assert text == "", "a failure must not carry words the user will read"


def test_the_reason_is_kept_for_the_model() -> None:
    clear_tool_failure()
    _format_github_result("github_list_repositories", {"ok": False, "message": "GitHub said 403"})

    assert "GitHub said 403" in take_tool_failure()


def test_a_held_write_still_asks_its_question() -> None:
    clear_tool_failure()
    question = "Reply 'yes' and I will create that branch."
    text = _format_github_result("github_create_branch", {"ok": False, "reply": question})

    assert text == question, "a confirmation is not a failure and must reach the user as written"
    assert take_tool_failure() == ""


def test_taking_the_reason_clears_it() -> None:
    clear_tool_failure()
    record_tool_failure("rate limited")

    assert take_tool_failure() == "rate limited"
    assert take_tool_failure() == "", "one failure must not be attached to two replies"


def test_the_line_forbids_inventing_a_cause() -> None:
    line = failure_line("GitHub said 403")

    assert "GitHub said 403" in line
    assert "Do not invent a cause" in line
    assert "in the language they wrote in" in line


def test_an_empty_reason_adds_nothing() -> None:
    assert failure_line("") == ""


def test_every_surface_passes_the_reason_to_the_model() -> None:
    from app.api.chat import chat, platform
    from app.capabilities.platform import channel_common

    for source in (
        inspect.getsource(chat),
        inspect.getsource(platform.stream_chat),
        inspect.getsource(channel_common),
    ):
        assert "take_tool_failure()" in source, "a surface that ignores the reason answers with nothing"
        assert "failure_line(" in source


def test_an_empty_reply_is_not_an_answer() -> None:
    """An empty string is not None, and sending it puts an empty bubble in front of the user.

    Every surface must fall through to the model when the tool produced no words, or the
    person is left looking at nothing with no way to know whether anything happened.
    """
    from app.api.chat import chat, platform
    from app.capabilities.platform import channel_common

    for source in (
        inspect.getsource(chat),
        inspect.getsource(platform.stream_chat),
        inspect.getsource(channel_common),
    ):
        assert ".strip()" in source and "tool_reply" in source or "reply" in source
        assert 'str(tool_reply).strip()' in source or 'str(reply).strip()' in source
