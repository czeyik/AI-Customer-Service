"""Rename the beta total rate-limit bucket for the extended beta window."""

from hashlib import sha256

import sqlalchemy as sa
from alembic import op


revision = "f7b2c3d4e5a6"
down_revision = "e5c1a2b3d4f6"
branch_labels = None
depends_on = None

LEGACY_KEY_HASH = "82aed473500dda2781d55e2b2d321ebdcce2654eb900197eb05b1cacf2b80235"
CURRENT_KEY_HASH = sha256(
    "beta-total:2026-09-10:2026-09-30".encode("utf-8")
).hexdigest()


def _rename_bucket(old_hash: str, new_hash: str) -> None:
    # The primary-key constraint makes a legacy/current collision fail atomically.
    op.get_bind().execute(
        sa.text(
            """
            UPDATE rate_limit_buckets
            SET key_hash = :new_hash
            WHERE key_hash = :old_hash
            """
        ),
        {"old_hash": old_hash, "new_hash": new_hash},
    )


def upgrade() -> None:
    _rename_bucket(LEGACY_KEY_HASH, CURRENT_KEY_HASH)


def downgrade() -> None:
    _rename_bucket(CURRENT_KEY_HASH, LEGACY_KEY_HASH)
