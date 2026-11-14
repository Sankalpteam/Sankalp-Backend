"""One small model decides whether a tool is needed, before any tool is offered.

The problem this exists to fix: the assistant was calling tools for messages that needed none.
"Hi" could start a search; a plain question could reach for GitHub. The cause is that the model
that writes the answer was also the model that chose whether to act, and it chose differently on
different days.

So the decision moves to one small, fast model with one narrow job, and it answers in a fixed
shape. Groq runs it: 30 requests a minute and 200,000 tokens a day are far more than this needs,
and the latency is a few hundred milliseconds rather than a few seconds.

Three rules keep it from doing harm:

* A tool name it invents is not a tool. Only names from the registry are accepted.
* Low confidence means "no tool", so a guess cannot start an action.
* If the router itself fails, the caller behaves exactly as it did before - a router that is
  down must never cost a user their answer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("indoone.router")

DEFAULT_CONFIDENCE = 0.6
_MAX_HISTORY_TURNS = 4

_ROUTER_PROMPT = """You decide whether answering this message needs a tool, and which one.

You are not writing the answer. You are only classifying.

Reply with JSON and nothing else:
{"use_tool": true or false, "tool": "<tool name or empty>", "confidence": 0.0 to 1.0}

Rules:
- A greeting, small talk, a general question you can answer from knowledge, or a question about
  the conversation itself needs no tool. use_tool is false.
- A request that names a place, a time, a price, the news, or asks to look something up needs a
  tool. Pick the closest tool from the list.
- If you are unsure, set use_tool false and confidence low. A wrong tool call is worse than no
  tool call.
- Never invent a tool name. Use one from the list, or leave it empty."""


@dataclass(slots=True, frozen=True)
class RouterVerdict:
    use_tool: bool
    tool: str | None
    confidence: float
    reason: str = ""


def _threshold() -> float:
    raw = os.getenv("INDOONE_ROUTER_CONFIDENCE", "").strip()

    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CONFIDENCE

    return value if 0 < value <= 1 else DEFAULT_CONFIDENCE


def _parse(raw: str) -> tuple[bool, str, float]:
    """Read the model's JSON out of whatever it wrapped it in."""

    match = re.search(r"\{.*\}", raw or "", re.DOTALL)

    if not match:
        return False, "", 0.0

    try:
        data: Any = json.loads(match.group(0))
    except ValueError:
        return False, "", 0.0

    if not isinstance(data, dict):
        return False, "", 0.0

    use_tool = bool(data.get("use_tool"))
    tool = str(data.get("tool") or "").strip()

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return use_tool, tool, max(0.0, min(confidence, 1.0))


async def route(message: str, *, available: tuple[str, ...] = (), history: list[tuple[str, str]] | None = None) -> RouterVerdict:
    """Should this message use a tool, and which one.

    Returns a verdict that is safe to ignore: ``use_tool`` False means "answer directly", and
    the caller then behaves exactly as it does today.
    """

    text = (message or "").strip()

    if not text:
        return RouterVerdict(False, None, 0.0, "empty")

    from app.ai.gateway.groq_service import groq_available

    if not groq_available():
        return RouterVerdict(True, None, 0.0, "no router model configured")

    catalogue = ", ".join(sorted(available)) or "none"

    lines = [f"Tools available: {catalogue}", ""]

    if history:
        lines.append("Recent turns:")
        for role, content in history[-_MAX_HISTORY_TURNS:]:
            spoken = " ".join(str(content).split())[:200]
            if spoken:
                lines.append(f"- {role}: {spoken}")
        lines.append("")

    lines.append(f"Message: {text}")

    try:
        from app.ai.gateway.groq_service import generate_groq_reply

        raw = await generate_groq_reply(
            "\n".join(lines),
            history=None,
            extra_system_instruction=_ROUTER_PROMPT,
        )
    except Exception as exc:  # a router that is down must not change behaviour
        logger.warning("router failed, leaving the decision to the model: %s", exc)
        return RouterVerdict(True, None, 0.0, "router unavailable")

    use_tool, tool, confidence = _parse(raw)

    if tool and tool not in available:
        logger.info("router named a tool that does not exist: %s", tool)
        use_tool, tool, confidence = False, "", 0.0

    if use_tool and confidence < _threshold():
        return RouterVerdict(False, None, confidence, "below confidence threshold")

    return RouterVerdict(use_tool, tool or None, confidence, "model")
