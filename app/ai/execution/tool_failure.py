"""What a failed tool call tells the model, and what it must never tell the user.

A tool that succeeds returns its data and the data reaches the user as it is. That is
deliberate: an earlier version handed tool results to the model and the model reworded
them - it once reported a branch as created when the call had not run, and summarised a
list wrongly. Nothing that a successful tool returns goes through a model.

A tool that FAILS is the opposite case. There is no data to protect, only a reason, and
that reason used to be written as a sentence by the tool layer: "The GitHub request could
not be completed." It arrived in the chat as the assistant's own reply, in English,
whatever language the person was writing in, with no idea of what actually went wrong.

So the rule is split by outcome:

* success - the tool's own text, untouched;
* failure - no sentence from the tool. The reason is recorded here, the surface that owns
  the conversation adds it to the instruction, and the model says it in the user's own
  language.

A ContextVar, not a module global: two requests are served at once, and one user's GitHub
failure must never surface in another user's reply.
"""

from __future__ import annotations

from contextvars import ContextVar

_FAILURE: ContextVar[str] = ContextVar("indoone_tool_failure", default="")

# Long enough for a provider's own error, short enough that it cannot become the prompt.
MAX_REASON_CHARS = 300


def record_tool_failure(reason: str) -> None:
    """Remember why a tool call failed, for the model to phrase."""
    text = " ".join(str(reason or "").split()).strip()

    if not text:
        return

    _FAILURE.set(text[:MAX_REASON_CHARS])


def take_tool_failure() -> str:
    """Read the recorded failure and clear it.

    Taking rather than reading is the point: the same failure must not be attached to two
    replies, and a request that never failed must not inherit the one before it.
    """
    reason = _FAILURE.get()
    _FAILURE.set("")
    return reason


def clear_tool_failure() -> None:
    _FAILURE.set("")


def failure_line(reason: str) -> str:
    """The one fact the model is given about a failed call.

    It is written as a fact and an instruction together. Without the instruction a model
    treats a failure as an invitation to explain, and an explanation it invents reads like
    a diagnosis - "your token expired" when the token was fine and GitHub was rate
    limiting. Without the fact it apologises vaguely and the person learns nothing.
    """
    text = " ".join(str(reason or "").split()).strip()[:MAX_REASON_CHARS]

    if not text:
        return ""

    return (
        f"The tool call for this request failed with: {text}. "
        "Tell the user plainly that it failed and why, in the language they wrote in. "
        "Do not invent a cause beyond that reason, do not claim anything was changed, "
        "and do not ask them to try again unless the reason says the failure was temporary."
    )
