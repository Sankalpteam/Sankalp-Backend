"""The streaming endpoint must tell the model today's date.

The web streams; the app does not. Without the date line the model answers a date question
from its training memory, and the two clients disagreed: the app said 2026, the web said
"Wednesday, May 22, 2024".
"""

from __future__ import annotations

import re

from app.ai.execution.request_context import now_line


def test_the_context_line_carries_todays_date() -> None:
    line = now_line()
    assert re.match(r"^Today is \d{4}-\d{2}-\d{2} \([A-Z][a-z]+\), \d{2}:\d{2} IST\.$", line)


def test_the_streaming_endpoint_builds_the_same_context_as_the_blocking_one() -> None:
    import inspect

    from app.api.chat import platform

    source = inspect.getsource(platform.stream_chat)
    assert "now_line()" in source, "the stream must send today's date, like /api/chat does"
    assert "user_name_line(user_id)" in source


def test_both_model_calls_in_the_stream_receive_it() -> None:
    import inspect

    from app.api.chat import platform

    source = inspect.getsource(platform.stream_chat)
    # run_tool_loop and stream_generate must both be given the enriched instruction
    assert source.count("extra_system_instruction=accounts") == 2
