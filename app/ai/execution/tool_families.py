"""Which family of tools a message is about, decided by its words.

This is not the decision to call a tool. It is the cheap filter that decides which twelve
tools the model gets to choose from. Sending all forty-two costs about 4,800 tokens on every
round of every message, which is real money and real latency for tools that cannot possibly be
relevant.

The rules are deliberately loose. A false positive costs a few hundred tokens; a false negative
costs the user their answer, because the model then answers from memory. So the words are broad,
and anything that matches nothing at all still reaches the catalogue through find_tools.

Families, and what puts a message in one:

    email      mail, inbox, draft, reply, zoho, gmail, unsubscribe
    github     repo, branch, pull request, commit, merge, issue, github
    instagram  instagram, reel, media, follower, ig
    code       code, function, refactor, bug, traceback, python, compile, test
    data       csv, tsv, dataset, spreadsheet, analyse, analyze, chart
    schedule   schedule, reminder, calendar, appointment, task

Words are matched on word boundaries and case-folded, so "pr" does not fire inside "prep".
"""

from __future__ import annotations

import re
from typing import Iterable

FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "email": (
        "mail", "mails", "email", "emails", "inbox", "gmail", "zoho", "draft", "drafts",
        "unread", "reply", "forward", "unsubscribe", "newsletter", "attachment",
    ),
    "github": (
        "github", "repo", "repos", "repository", "repositories", "branch", "branches",
        "commit", "commits", "merge", "merged", "pull", "request", "requests", "pr", "prs",
        "issue", "issues", "ci", "workflow", "workflows", "push", "pushed", "diff",
    ),
    "instagram": (
        "instagram", "insta", "ig", "reel", "reels", "media", "follower", "followers",
        "caption", "post", "posts",
    ),
    "code": (
        "code", "function", "method", "class", "refactor", "bug", "error", "traceback",
        "exception", "python", "javascript", "kotlin", "java", "compile", "lint", "test",
        "tests", "regex",
    ),
    "data": (
        "csv", "tsv", "dataset", "spreadsheet", "excel", "analyse", "analyze", "analysis",
        "chart", "graph", "statistics", "rows", "columns",
    ),
    "schedule": (
        "schedule", "scheduled", "reminder", "reminders", "calendar", "appointment",
        "meeting", "task", "tasks", "todo", "alarm",
    ),
}

# Tool name prefixes and exact names that belong to each family.
FAMILY_TOOL_PREFIXES: dict[str, tuple[str, ...]] = {
    "github": ("github_",),
    "instagram": ("instagram_",),
    "code": ("code_",),
}

FAMILY_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "code": ("sandbox_execution",),
    "data": ("data_analysis",),
    "schedule": ("task_schedule_build",),
}

EMAIL_FAMILY = "email"

_WORD = re.compile(r"[a-z0-9]+")


def _words(message: str) -> set[str]:
    return set(_WORD.findall((message or "").casefold()))


def families_for(message: str) -> frozenset[str]:
    """Every family this message could belong to. Empty means "no idea, use find_tools"."""

    words = _words(message)

    if not words:
        return frozenset()

    matched = {
        family
        for family, keywords in FAMILY_KEYWORDS.items()
        if words.intersection(keywords)
    }
    return frozenset(matched)


def tool_names_for(families: Iterable[str], catalogue: Iterable[str]) -> frozenset[str]:
    """The tools in the catalogue that belong to any of these families."""

    wanted: set[str] = set()
    families = set(families)

    for name in catalogue:
        for family in families:
            if name.startswith(FAMILY_TOOL_PREFIXES.get(family, ())):
                wanted.add(name)
                break
            if name in FAMILY_TOOL_NAMES.get(family, ()):
                wanted.add(name)
                break

    return frozenset(wanted)
