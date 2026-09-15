"""add shared bounded rate limits

Revision ID: 49b1f7a0c2de
Revises: 69f51c2de537
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "49b1f7a0c2de"
down_revision: Union[str, None] = "69f51c2de537"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("request_count > 0", name="ck_rate_limit_request_count"),
        sa.PrimaryKeyConstraint("key_hash"),
    )
    op.create_index(
        op.f("ix_rate_limit_buckets_expires_at"), "rate_limit_buckets", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
