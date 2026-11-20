"""Atomic prepaid hold/capture foundation for future B2B billing.

Revision ID: 0006_prepaid_reserve
Revises: 0005_developer_api_keys

No payment, public inference, or automatic crediting is enabled by this schema.
"""
from alembic import op

revision = "0006_prepaid_reserve"
down_revision = "0005_developer_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS developer_prepaid_accounts (
            owner_id TEXT PRIMARY KEY,
            available_paise BIGINT NOT NULL DEFAULT 0 CHECK(available_paise >= 0),
            held_paise BIGINT NOT NULL DEFAULT 0 CHECK(held_paise >= 0),
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS developer_prepaid_reservations (
            reservation_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            key_id TEXT NOT NULL REFERENCES developer_api_keys(key_id),
            client_request_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            dispatch_started_at TEXT,
            model TEXT NOT NULL,
            provider TEXT NOT NULL CHECK(provider IN ('gemini', 'groq')),
            upstream_model TEXT NOT NULL,
            input_rate_paise BIGINT NOT NULL CHECK(input_rate_paise >= 0),
            output_rate_paise BIGINT NOT NULL CHECK(output_rate_paise >= 0),
            max_charge_paise BIGINT NOT NULL CHECK(max_charge_paise > 0),
            captured_paise BIGINT NOT NULL DEFAULT 0 CHECK(captured_paise >= 0),
            status TEXT NOT NULL CHECK(status IN ('reserved', 'captured', 'released')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(key_id, client_request_id)
        )
    """)
    # Provider response IDs are bound to billable evidence, preventing the same
    # upstream response from being used in two separate billable requests.
    # Earlier unbillable evidence remains nullable for compatibility.
    op.execute("ALTER TABLE developer_verified_usage ADD COLUMN upstream_response_id TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dev_usage_upstream "
        "ON developer_verified_usage(provider, upstream_response_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_dev_reservations_owner ON developer_prepaid_reservations(owner_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dev_reservations_status ON developer_prepaid_reservations(status, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dev_usage_upstream")
    # SQLite cannot drop this added column on all supported deployments;
    # retaining nullable evidence metadata is a safe downgrade.
    op.execute("DROP TABLE IF EXISTS developer_prepaid_reservations")
    op.execute("DROP TABLE IF EXISTS developer_prepaid_accounts")
