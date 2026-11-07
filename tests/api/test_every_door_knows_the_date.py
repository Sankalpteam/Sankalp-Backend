"""Every door into the model must build its instruction the same way.

There is one backend and four ways in - the app's blocking chat, the web's stream, the channel
handler behind WhatsApp, Telegram and Instagram, and the web-research fast path.

They used to assemble the same three lines separately, at each door. Four copies is four
chances to leave one out, and two were left out: the stream answered a date question with
"Wednesday, May 22, 2024", and the research path had no date at all.

The guard is now the strongest kind available: there is one builder, and every door calls it.
A new door that writes its own instruction fails here.
"""

from __future__ import annotations

import inspect
import re

from app.ai.execution.request_context import conversation_instruction, now_line

_DATE = re.compile(r"Today is \d{4}-\d{2}-\d{2} \([A-Z][a-z]+\), \d{2}:\d{2} IST\.")


def test_the_line_itself_is_todays_date_in_ist() -> None:
    assert _DATE.match(now_line())


def test_the_builder_carries_the_date() -> None:
    assert "Today is" in conversation_instruction("")


def test_the_blocking_chat_endpoint_uses_the_builder() -> None:
    from app.api.chat import chat

    assert "conversation_instruction(user_id)" in inspect.getsource(chat)


def test_the_streaming_endpoint_uses_the_builder() -> None:
    from app.api.chat import platform

    assert "conversation_instruction(user_id)" in inspect.getsource(platform.stream_chat)


def test_the_channels_use_the_builder() -> None:
    from app.capabilities.platform import channel_common

    assert "Today is" in channel_common._instruction("")
    assert "conversation_instruction(" in inspect.getsource(channel_common._instruction)


def test_the_web_research_answer_uses_the_builder() -> None:
    from app.web_research import fast_path

    assert "Today is" in fast_path._instruction("")
    assert "conversation_instruction(" in inspect.getsource(fast_path._instruction)
