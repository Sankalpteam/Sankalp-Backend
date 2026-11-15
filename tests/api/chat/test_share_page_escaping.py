"""A shared page may not run what it displays.

The share page escapes a message before formatting it, which is the right order. But it escaped
with quote=False, which leaves a double quote alone - so a URL inside a message closed the href
attribute it was placed in:

    [here](https://a.com/x"onmouseover="alert(1))

became a real event handler on a page anyone can open. A share link is public and
unauthenticated by design, so this was reachable by anyone who could get a message into a thread
and share it.
"""

from __future__ import annotations

from app.api.chat.share_page import _inline, render_share_page


def test_a_url_cannot_close_the_attribute_it_sits_in() -> None:
    rendered = _inline('look [here](https://a.com/x"onmouseover="alert(1)) now')

    assert 'onmouseover="alert' not in rendered
    assert "&quot;onmouseover=&quot;" in rendered


def test_a_bare_url_cannot_do_it_either() -> None:
    """The URL pattern stops at the quote, so the rest is escaped text outside the tag."""

    rendered = _inline('see https://a.com/x"onmouseover="alert(1) now')

    assert 'href="https://a.com/x"onmouseover' not in rendered


def test_a_normal_link_is_still_a_link() -> None:
    rendered = _inline("read [the docs](https://example.com/docs)")

    assert '<a href="https://example.com/docs"' in rendered
    assert ">the docs</a>" in rendered


def test_a_script_tag_in_a_message_is_escaped() -> None:
    page = render_share_page("<script>alert(1)</script>", [("user", "<script>alert(2)</script>")])

    assert "<script>alert(1)</script>" not in page
    assert "<script>alert(2)</script>" not in page
    assert "&lt;script&gt;" in page
