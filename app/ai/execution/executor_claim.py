"""Who answered this message, and was it only one of them.

One message can reach seven places that are willing to answer it: a pending GitHub write, a
question about the thread, the mailbox read path, the GitHub router, web research, the agent
planner, and the model's tool loop. They are chained with `if tool_reply is None`, so in
practice one of them answers — but "in practice" is not the same as "by construction", and
nothing in the logs said which one it was. A message that behaved strangely could not be traced
back to the path that handled it.

So each executor claims the turn when it produces an answer. The first claim wins; a second
claim is a bug and is logged as one, with both names, because that is the case where two paths
answer the same message and the user gets whichever finished last.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("indoone.executor")

_CLAIM: dict[str, str] = {}


def reset(turn_id: str) -> None:
    _CLAIM.pop(turn_id, None)


def claim(turn_id: str, executor: str) -> None:
    """Record that this executor answered the turn. A second claim is logged, not raised."""

    if not turn_id:
        return

    previous = _CLAIM.get(turn_id)

    if previous is None:
        _CLAIM[turn_id] = executor
        logger.info("executor=%s turn=%s", executor, turn_id)
        return

    if previous != executor:
        logger.error(
            "two executors answered one turn: %s then %s (turn=%s). The later answer wins.",
            previous,
            executor,
            turn_id,
        )
        # The later answer is the one the user saw, so the claim follows it. Recording the
        # first would name a path whose answer was replaced.
        _CLAIM[turn_id] = executor


def claimed_by(turn_id: str) -> str | None:
    return _CLAIM.get(turn_id)


def forget(turn_id: str) -> None:
    _CLAIM.pop(turn_id, None)
