"""A channel must never send a research block as an answer.

This is the regression that reached real users: "Hi" on WhatsApp came back as
"<research> title: HI url: https://en.wikipedia.org/wiki/HI snippet: ..." with raw <sub> tags
inside, and on Instagram as a greeting followed by a list of Wikipedia links for the word "Hi".
The cause was a failed model call falling back to the results block, on a path with no router
in front of it. The guard below does not care about the cause - it refuses the shape.
"""

from __future__ import annotations

import pytest

from app.capabilities.platform.channel_common import looks_like_raw_research


@pytest.mark.parametrize(
    "reply",
    [
        # The exact shape WhatsApp sent.
        "<research>\ntitle: HI\nurl: https://en.wikipedia.org/wiki/HI\nsnippet: HI or Hi may refer to:",
        # The peerj table that came with it.
        "title: Table 4 The comparison results of HI <sub>ori1</sub> and HI <sub>pro1</sub>",
        # A greeting with a source dump stapled on.
        "Hello! How can I assist you today? Sources: 1. HI — https://en.wikipedia.org/wiki/HI "
        "2. https://news.google.com/rss/articles/abc 3. https://doi.org/10.7771/peerj.21005",
        # Three links and nothing else.
        "https://a.example/x https://b.example/y https://c.example/z",
    ],
)
def test_a_research_payload_is_refused(reply: str) -> None:
    assert looks_like_raw_research(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        "Hello! How can I help you today?",
        "ಕರ್ನಾಟಕ ಭಾರತದ ದಕ್ಷಿಣ ಭಾಗದಲ್ಲಿರುವ ಒಂದು ಪ್ರಮುಖ ರಾಜ್ಯವಾಗಿದೆ.",
        "Here is the fix: change the port in the service file, then restart it. "
        "More detail at https://example.com/docs/ports when you have a minute.",
        "",
    ],
)
def test_a_real_answer_is_left_alone(reply: str) -> None:
    assert looks_like_raw_research(reply) is False
