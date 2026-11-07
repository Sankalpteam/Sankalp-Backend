"""Every door into the model must tell it what day it is.

There is one backend and four ways in - the app's blocking chat, the web's stream, the
channel handler that serves WhatsApp, Telegram and Instagram, and the web-research fast path.
Each builds its own system instruction, and that is the whole problem: they drift. The stream
did drift, and answered a date question with "Wednesday, May 22, 2024".

This test is the guard. A new door that forgets the date fails here instead of in a
screenshot from a user.
"""

from __future__ import annotations

import inspect
import re

from app.ai.execution.request_context import now_line

_DATE = re.compile(r"Today is \d{4}-\d{2}-\d{2} \([A-Z][a-z]+\), \d{2}:\d{2} IST\.")


def test_the_line_itself_is_todays_date_in_ist() -> None:
    assert _DATE.match(now_line())


def test_the_blocking_chat_endpoint_sends_it() -> None:
    from app.api.chat import chat

    source = inspect.getsource(chat)
    assert "now_line()" in source


def test_the_streaming_endpoint_sends_it() -> None:
    from app.api.chat import platform

    assert "now_line()" in inspect.getsource(platform.stream_chat)


def test_the_channels_send_it() -> None:
    from app.capabilities.platform import channel_common

    instruction = channel_common._instruction("")
    assert "Today is" in instruction


def test_the_web_research_answer_sends_it() -> None:
    from app.web_research import fast_path

    assert "Today is" in fast_path._instruction()
