"""Isolated developer API-key identities and verified token-usage evidence.

Revision ID: 0005_developer_api_keys
Revises: 0004_chat_turn_claims

Neither this migration nor key creation enables paid inference, billing, or a
public key-authenticated model route. Those require separately verified gates.
Only hashed key material and provider-reported token counts may be persisted.
"""
from __future__ import annotations

from alembic import op

revision = "0005_developer_api_keys"
down_revision = "0004_chat_turn_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_api_keys (
            key_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            environment TEXT NOT NULL CHECK(environment IN ('test', 'live')),
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            key_last4 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'revoked')),
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_developer_keys_owner "
        "ON developer_api_keys(owner_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_verified_usage (
            event_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            key_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            input_tokens BIGINT NOT NULL CHECK(input_tokens >= 0),
            output_tokens BIGINT NOT NULL CHECK(output_tokens >= 0),
            created_at TEXT NOT NULL,
            FOREIGN KEY(key_id) REFERENCES developer_api_keys(key_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_developer_usage_owner "
        "ON developer_verified_usage(owner_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_developer_usage_key "
        "ON developer_verified_usage(key_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS developer_verified_usage")
    op.execute("DROP TABLE IF EXISTS developer_api_keys")
