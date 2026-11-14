"""A monthly cap on the paid search provider, counted where the calls are made.

Tavily's free plan stops at its own limit and does not charge unless pay-as-you-go is
switched on. That is a good backstop and a bad guarantee: one toggle in a dashboard would turn
a free month into a bill, and nothing in our code would notice.

So we keep our own count, below theirs, in the same SQLite file the conversation store uses.
When the month's allowance is gone, the research path simply stops offering that provider and
the keyless ones answer instead. Nothing fails; the answer gets a little less fresh.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(os.getenv("INDOONE_CONVERSATION_DB", "data/indoone_conversations.sqlite3"))

# Below the provider's own 1,000 so a burst of retries cannot tip us over the edge.
DEFAULT_MONTHLY_LIMIT = 900

_TABLE = """
CREATE TABLE IF NOT EXISTS search_usage (
    month TEXT PRIMARY KEY,
    used  INTEGER NOT NULL DEFAULT 0
)
"""


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _limit() -> int:
    raw = os.getenv("INDOONE_TAVILY_MONTHLY_LIMIT", "").strip()

    if not raw:
        return DEFAULT_MONTHLY_LIMIT

    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MONTHLY_LIMIT

    return value if value > 0 else DEFAULT_MONTHLY_LIMIT


def take_tavily_credit() -> bool:
    """Count one search against this month's allowance. False when it is spent.

    A database that cannot answer is treated as "spent": a paid call is the one thing we must
    not make when we cannot prove we are under the cap.
    """

    if not os.getenv("INDOONE_TAVILY_API_KEY", "").strip():
        return False

    limit = _limit()
    month = _month()

    try:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            connection.execute(_TABLE)
            row = connection.execute(
                "SELECT used FROM search_usage WHERE month = ?", (month,)
            ).fetchone()
            used = int(row[0]) if row else 0

            if used >= limit:
                return False

            connection.execute(
                "INSERT INTO search_usage(month, used) VALUES (?, 1) "
                "ON CONFLICT(month) DO UPDATE SET used = used + 1",
                (month,),
            )
        return True
    except sqlite3.Error:
        return False


def tavily_used_this_month() -> int:
    """For a status line or a log, never for a decision on its own."""

    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            connection.execute(_TABLE)
            row = connection.execute(
                "SELECT used FROM search_usage WHERE month = ?", (_month(),)
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
