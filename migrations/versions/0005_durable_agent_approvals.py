"""One-time, user-scoped approval claims for paused agent writes.

Revision ID: 0005_durable_agent_approvals
Revises: 0004_chat_turn_claims

The action/payload stay in the already-owned agent_runs record; only its
fingerprint and the one-time dispatch state are duplicated here. Marking an
attempt claimed *before* an external write prevents crash/retry replay.
"""
from __future__ import annotations

from alembic import op

revision = "0005_durable_agent_approvals"
down_revision = "0004_chat_turn_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_agent_approvals (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            step_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'claimed', 'completed',
                                 'needs_review', 'denied', 'expired')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_agent_approvals_user "
        "ON pending_agent_approvals(user_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_agent_approvals")
