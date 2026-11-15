"""The last thing between a tool and a person: a reply that is not a sentence is not sent.

A tool returns structured data, and the model is supposed to turn it into a sentence. When the
model call fails or the model answers with the payload instead of a sentence, that payload used
to reach the user: a research block with `title:` and `url:` lines, or a JSON object, on
WhatsApp where there is no way to ask again.

Two shapes are caught:

* a research or document block - `<research>`, `title: ... url:`, `snippet:`, stray inline tags
* a tool dump - a reply that is mostly JSON, or that carries tool-call machinery like
  `function_call`, `tool_call_id` or `"arguments":`

A reply that fails either check is replaced with one honest sentence. Losing the detail is
better than sending the user something that is not addressed to them.
"""

from __future__ import annotations

import re

RAW_BLOCK_REPLY = (
    "I could not turn that into an answer just now. Please try again in a moment, "
    "or ask it a slightly different way."
)

_RAW_BLOCK = re.compile(
    r"<\s*(research|document|context)\s*>"
    r"|\btitle:\s*[^\n]{0,160}\n?\s*url:"
    r"|\bsnippet:\s*\S"
    r"|<\s*(sub|sup|b|i|span)\s*[>/]",
    re.IGNORECASE,
)

_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# The machinery of a tool call, which no person should ever be shown.
_TOOL_MACHINERY = re.compile(
    r'"(function_call|tool_call_id|tool_calls|arguments|functionCall|finish_reason)"\s*:'
    r"|\bfunction_call\b"
    r"|\btool_call_id\s*:"
    r'|\{\s*"name"\s*:\s*"'
    r'|\{\s*"ok"\s*:\s*(true|false)\s*,',
    re.IGNORECASE,
)

_JSON_START = re.compile(r"^\s*[\[{]")


def looks_like_raw_research(reply: str) -> bool:
    """True when the text is a research payload rather than something a person wrote."""

    text = (reply or "").strip()

    if not text:
        return False

    if _RAW_BLOCK.search(text):
        return True

    links = _URL.findall(text)

    if len(links) >= 3:
        without_links = _URL.sub(" ", text)
        words = [w for w in without_links.split() if any(c.isalpha() for c in w)]

        # A real answer carries its own sentences between the links.
        if len(words) < 12:
            return True

    return False


def looks_like_tool_dump(reply: str) -> bool:
    """True when the text is tool output or a tool call, not an answer."""

    text = (reply or "").strip()

    if not text:
        return False

    if _TOOL_MACHINERY.search(text):
        return True

    # A reply that opens with a brace or bracket and is mostly one object is a payload. A
    # sentence that happens to contain a brace is not: the closing character must be near the
    # end and the text must be short.
    if _JSON_START.match(text) and len(text) < 2_000:
        tail = text[-1]

        if tail in "}]":
            return True

    return False


def guard_reply(reply: str) -> tuple[str, str | None]:
    """Return a reply that is safe to send, and the reason it was replaced (or None)."""

    text = reply or ""

    if looks_like_raw_research(text):
        return RAW_BLOCK_REPLY, "raw_research_block"

    if looks_like_tool_dump(text):
        return RAW_BLOCK_REPLY, "tool_dump"

    return text, None
