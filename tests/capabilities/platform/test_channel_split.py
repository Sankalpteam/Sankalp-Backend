"""A long answer on a channel is several messages, not one cut in half.

The channel formatters split a reply at the channel's limit - 4096 for WhatsApp, 1000 for
Instagram - and then only the first piece was ever sent. Everything past the limit was lost
silently, which for a research answer means the sources and the conclusion.

These tests pin the splitter, not the sender: the sender's loop is two lines and would be
obvious in review, while "does the splitter keep the text" is the part that is easy to get
wrong in Kannada.
"""

from __future__ import annotations

from app.capabilities.platform.channel_common import split_reply


def test_a_short_answer_is_one_message() -> None:
    assert split_reply("Ivattina weather 24 degrees ide.") == [
        "Ivattina weather 24 degrees ide."
    ]


def test_a_long_answer_keeps_every_word() -> None:
    answer = " ".join(f"word{index}" for index in range(2000))

    pieces = split_reply(answer, limit=4096)

    assert len(pieces) > 1
    assert all(len(piece) <= 4096 for piece in pieces)
    assert " ".join(pieces).split() == answer.split()


def test_a_kannada_answer_is_not_cut_inside_a_glyph() -> None:
    answer = ("ಬೆಂಗಳೂರಿನಲ್ಲಿ ಇವತ್ತಿನ ಹವಾಮಾನ ಚೆನ್ನಾಗಿದೆ. " * 400).strip()

    pieces = split_reply(answer, limit=1000)

    assert len(pieces) > 1
    for piece in pieces:
        # A piece must not begin with a combining mark or a virama: that is the broken-glyph
        # case the splitter exists to avoid.
        assert piece[0] not in {"\u0ccd", "\u0cbf", "\u0cc6"}
        assert len(piece) <= 1000


def test_an_empty_reply_is_still_one_piece() -> None:
    assert split_reply("") == [""]
