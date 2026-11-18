"""Persist cross-endpoint chat turn admission for safe retries.

Revision ID: 0004_chat_turn_claims
Revises: 0003_title_and_shares

The ledger stores only the authenticated user ID, a client-chosen turn ID, a
SHA-256 request fingerprint and lifecycle metadata (no prompts or credentials).
"""
from __future__ import annotations

from alembic import op

revision = "0004_chat_turn_claims"
down_revision = "0003_title_and_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_turn_claims (
            user_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'received'
                CHECK(status IN ('received', 'replied')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, turn_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_turn_claims")
