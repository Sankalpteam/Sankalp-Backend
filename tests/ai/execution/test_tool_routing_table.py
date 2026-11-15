"""Twenty real messages, and the tools each must be offered.

This is the table we agreed on. It is not a unit test of a helper: it is the contract, written
down, so that a change to a keyword list, a cap or a family shows up here as a failure instead
of as a user saying "it does not work" a week later.

Each row says: this message, these families, this much web research, and this tool must be in
the declarations. A row with no family must still get the always-on set and must not get a
family's tools by accident.
"""

from __future__ import annotations

import pytest

from app.ai.execution.tool_families import families_for
from app.ai.execution.tool_loop import _MAX_DECLARATIONS, model_tool_declarations
from app.web_research.tools import wants_web_research

# message, families, wants_web, a tool that must be offered
TABLE: tuple[tuple[str, frozenset[str], bool, str | None], ...] = (
    ("Hi", frozenset(), False, None),
    ("Hello, how are you?", frozenset(), False, None),
    ("thanks!", frozenset(), False, None),
    ("how many unread mails do I have", frozenset({"email"}), False, "count_inbox_emails"),
    ("read my last email from Ravi", frozenset({"email"}), False, "read_email"),
    ("send a mail to Ravi about the meeting", frozenset({"email", "schedule"}), False, "prepare_email_to_send"),
    ("create a draft reply to the invoice mail", frozenset({"email"}), False, "create_email_draft"),
    ("delete those spam emails", frozenset({"email"}), False, "prepare_email_deletion"),
    ("list my repositories", frozenset({"github"}), False, "github_list_repositories"),
    ("open a pull request from this branch", frozenset({"github"}), False, "github_create_pull_request"),
    ("merge the pull request once checks pass", frozenset({"github"}), False, "github_merge_pull_request"),
    ("what commits are on main", frozenset({"github"}), False, "github_list_repository_commits"),
    ("show me the diff of PR 42", frozenset({"github"}), False, "github_list_pull_request_files"),
    ("list my instagram media", frozenset({"instagram"}), False, "instagram_list_media"),
    ("download this instagram reel", frozenset({"instagram"}), False, "instagram_get_media_details"),
    ("fix this bug in my python function", frozenset({"code"}), False, "code_analysis"),
    ("refactor this kotlin class", frozenset({"code"}), False, "code_transform"),
    ("analyse this csv file", frozenset({"data"}), False, "data_analysis"),
    ("schedule a meeting tomorrow at 5", frozenset({"schedule"}), False, "task_schedule_build"),
    ("remind me to call amma at 8", frozenset({"schedule"}), False, "task_schedule_build"),
    ("ivattina bangalore weather hegide antha web alli huduku", frozenset(), True, "search_web"),
    ("latest news about ISRO", frozenset(), True, "search_web"),
)

_ALWAYS_ON = {"find_tools", "calculator", "text_stats"}


@pytest.mark.parametrize("message, families, wants_web, must_have", TABLE)
def test_the_message_gets_the_tools_it_is_about(
    message: str, families: frozenset[str], wants_web: bool, must_have: str | None
) -> None:
    assert families_for(message) == families
    assert wants_web_research(message) is wants_web

    names = {
        item["name"]
        for item in model_tool_declarations("", families=families, wants_web=wants_web)
    }

    assert _ALWAYS_ON <= names, "the always-on set is never dropped"
    assert len(names) <= _MAX_DECLARATIONS, "the cap holds for every message"

    if must_have is not None:
        assert must_have in names, f"{message!r} must be offered {must_have}"


@pytest.mark.parametrize(
    "message",
    ["Hi", "Hello, how are you?", "thanks!"],
)
def test_a_greeting_is_offered_no_family_tools(message: str) -> None:
    """The failure this replaces: a greeting that reached for a tool."""

    names = {
        item["name"]
        for item in model_tool_declarations("", families=families_for(message))
    }

    assert names == _ALWAYS_ON


def test_the_whole_catalogue_is_still_available_to_voice() -> None:
    """The cap must not shrink the full list: voice asks for everything and filters itself."""

    assert len(model_tool_declarations("", full=True)) > _MAX_DECLARATIONS
