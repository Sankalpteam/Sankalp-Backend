"""Conservative fail-closed check for explicitly requested external operations.

A tool intent does not authorize execution. The shared tool registry and backend
executor enforce per-user access, confirmation, and tool-side constraints.
"""
from __future__ import annotations

import re

_ACTION = re.compile(
    r"\b(?:show|list|read|fetch|get|check|search|find|lookup|look up|send|"
    r"create|delete|remove|update|edit|merge|commit|push|reply|forward|"
    r"draft|open|close|connect|disconnect|count|summarize|summarise)\b"
    r"|ತೋರಿಸು|ತೋರಿಸಿ|ಹುಡುಕು|ಹುಡುಕಿ|ಓದು|ಓದಿ|ಕಳಿಸು|ಕಳುಹಿಸು|"
    r"ಮಾಡು|ಮಾಡಿ|ಅಳಿಸು|ಡಿಲೀಟ್|ಪರಿಶೀಲಿಸು|ಕಂಡುಹಿಡಿ|ಕಳುಹಿಸಿ|"
    r"చూపించు|వెతుకు|పంపు|చదువు|తొలగించు",
    re.IGNORECASE,
)
_CONNECTOR = re.compile(
    r"\b(?:github|repo|repository|repositories|branches|commit|pull request|"
    r"gmail|email|mailbox|inbox|zoho|instagram|calendar|web search)\b"
    r"|ಗಿಟ್.?ಹಬ್|ರೆಪೊ|ರೆಪೋ|ಬ್ರಾಂಚ್|ಇಮೇಲ್|ಮೇಲ್|ವೆಬ್.?ಸರ್ಚ್|"
    r"గిట్.?హబ్|రెపో|మెయిల్",
    re.IGNORECASE,
)


def requires_tool_result(message: str) -> bool:
    """An action on live data needs an executed tool, never an invented answer."""
    return bool(_ACTION.search(message or "") and _CONNECTOR.search(message or ""))
