"""Store developer API keys, hashed.

Revision ID: 0005_api_keys
Revises: 0004_chat_turn_claims

Only a SHA-256 of the secret is stored. The plaintext is shown to the person who created
the key, once, and is never readable again - a key table that can be read back is a
credential store, and a database backup then holds every developer's key.
"""
from __future__ import annotations

from alembic import op

revision = "0005_api_keys"
down_revision = "0004_chat_turn_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT,
            revoked_at TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_api_keys_user ON api_keys(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_api_keys_hash ON api_keys(key_hash)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_api_keys_hash")
    op.execute("DROP INDEX IF EXISTS ix_api_keys_user")
    op.execute("DROP TABLE IF EXISTS api_keys")
