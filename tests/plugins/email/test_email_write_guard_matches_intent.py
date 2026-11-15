"""Two guards decide whether a mail sentence is a write. They must agree.

The intent agent and the mailbox fast path each keep their own list of write verbs. The fast
path's list was missing "unsubscribe" and "archive", so a request the assistant cannot perform
was answered with a list of the inbox - and the two guards disagreed about the same sentence,
which is how one of them ends up answering the other's question.
"""

from __future__ import annotations

import pytest

from app.agents import load
from app.plugins.email import email_tools


WRITES = (
    "delete that email",
    "send a mail to Ravi",
    "reply to that mail",
    "create a draft",
    "unsubscribe from that newsletter",
    "archive these mails",
    "move it to trash",
)

READS = (
    "how many unread emails",
    "read my last email from today",
    "show my inbox",
)


@pytest.mark.parametrize("message", WRITES)
def test_a_write_is_never_labelled_a_read(message: str) -> None:
    """The dangerous direction only.

    The intent agent may call an ambiguous sentence GENERAL_CHAT and let the model handle it.
    What it must never do is call it READ_EMAIL, because a read is a fast path and a fast path
    answers instead of holding the action for confirmation.
    """

    intent = load("05_intent_purity_extractor")

    assert email_tools._EMAIL_WRITE_INTENT.search(message), message
    assert intent.extract(message).action != "READ_EMAIL", message


@pytest.mark.parametrize("message", READS)
def test_a_read_is_not_called_a_write(message: str) -> None:
    assert not email_tools._EMAIL_WRITE_INTENT.search(message), message
