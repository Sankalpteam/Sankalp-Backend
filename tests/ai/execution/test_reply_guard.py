"""A tool's payload is not a reply.

The model is supposed to turn tool data into a sentence. When it fails to, or answers with the
payload itself, that payload used to reach the user - a research block on WhatsApp, a JSON
object in a chat bubble. These tests pin the two shapes that must never be sent.
"""

from __future__ import annotations

import pytest

from app.ai.execution.reply_guard import RAW_BLOCK_REPLY, guard_reply, looks_like_tool_dump


@pytest.mark.parametrize(
    "payload",
    [
        '<research> title: HI url: https://en.wikipedia.org/wiki/HI snippet: A greeting',
        "title: Bangalore weather\nurl: https://example.com/weather",
        "snippet: something the model was given",
        "Text with <sub>2</sub> inline tags from a page",
    ],
)
def test_a_research_payload_is_replaced(payload: str) -> None:
    text, reason = guard_reply(payload)

    assert text == RAW_BLOCK_REPLY
    assert reason == "raw_research_block"


@pytest.mark.parametrize(
    "payload",
    [
        '{"ok": true, "result": {"tools": []}}',
        '[{"name": "search_web", "arguments": {}}]',
        '{"function_call": {"name": "search_web"}}',
        'tool_call_id: abc123',
    ],
)
def test_a_tool_dump_is_replaced(payload: str) -> None:
    text, reason = guard_reply(payload)

    assert text == RAW_BLOCK_REPLY
    assert reason == "tool_dump"
    assert looks_like_tool_dump(payload) is True


@pytest.mark.parametrize(
    "answer",
    [
        "Ivattina Bangalore weather sumaru 24 degrees ide.",
        "You have 3 repositories: sankalp, indoone, eyesearch.",
        "Here is the answer in one sentence.",
        "The build passed on branch main. {That is all.}",
    ],
)
def test_a_real_answer_is_untouched(answer: str) -> None:
    text, reason = guard_reply(answer)

    assert text == answer
    assert reason is None


def test_an_empty_reply_is_left_alone() -> None:
    """An empty reply is somebody else's problem - the caller decides what to do with it."""

    text, reason = guard_reply("")

    assert text == ""
    assert reason is None


def test_a_sentence_with_one_brace_is_not_a_payload() -> None:
    assert looks_like_tool_dump("The value is {small} in this case.") is False


def test_a_repository_list_is_not_a_dump() -> None:
    """Names and URLs with few words is a list somebody asked for, not a source dump.

    This exact shape was eaten in production: "list my repositories" answered with the
    guard's apology because three links and no prose looked like a research block.
    """

    reply = (
        "You have 3 repositories:\n\n"
        "1. sankalp\nhttps://github.com/Sankalpteam/Sankalp\n\n"
        "2. Sankalp-Backend\nhttps://github.com/Sankalpteam/Sankalp-Backend\n\n"
        "3. Eyesearch\nhttps://github.com/Eyesearch/Eyesearch"
    )

    text, reason = guard_reply(reply)

    assert text == reply
    assert reason is None


def test_bare_links_with_no_structure_are_still_a_dump() -> None:
    text, reason = guard_reply("https://a.com/x https://b.com/y https://c.com/z")

    assert reason == "raw_research_block"
    assert text == RAW_BLOCK_REPLY
