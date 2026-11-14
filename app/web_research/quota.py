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
    provider TEXT NOT NULL DEFAULT 'tavily',
    month    TEXT NOT NULL,
    used     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, month)
)
"""


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _limit(env_name: str, default: int) -> int:
    raw = os.getenv(env_name, "").strip()

    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


def take_credit(provider: str, env_name: str, default_limit: int) -> bool:
    """Count one request against this month's allowance for one provider.

    False when the allowance is spent or the database cannot answer. That second case is
    deliberate: a paid call is the one thing we must not make when we cannot prove we are
    under the cap.
    """

    limit = _limit(env_name, default_limit)
    month = _month()

    try:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            connection.execute(_TABLE)
            row = connection.execute(
                "SELECT used FROM search_usage WHERE provider = ? AND month = ?", (provider, month)
            ).fetchone()
            used = int(row[0]) if row else 0

            if used >= limit:
                return False

            connection.execute(
                "INSERT INTO search_usage(provider, month, used) VALUES (?, ?, 1) "
                "ON CONFLICT(provider, month) DO UPDATE SET used = used + 1",
                (provider, month),
            )
        return True
    except sqlite3.Error:
        return False


def take_tavily_credit() -> bool:
    if not os.getenv("INDOONE_TAVILY_API_KEY", "").strip():
        return False

    return take_credit("tavily", "INDOONE_TAVILY_MONTHLY_LIMIT", DEFAULT_MONTHLY_LIMIT)


def take_serp_credit() -> bool:
    """Bright Data SERP: one credit a search, 5,000 a month free, capped below that."""

    if not os.getenv("INDOONE_BRIGHTDATA_API_KEY", "").strip():
        return False
    if not os.getenv("INDOONE_BRIGHTDATA_SERP_ZONE", "").strip():
        return False

    return take_credit("brightdata_serp", "INDOONE_BRIGHTDATA_MONTHLY_LIMIT", 4_500)


def used_this_month(provider: str = "tavily") -> int:
    """For a status line or a log, never for a decision on its own."""

    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            connection.execute(_TABLE)
            row = connection.execute(
                "SELECT used FROM search_usage WHERE provider = ? AND month = ?", (provider, _month())
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
