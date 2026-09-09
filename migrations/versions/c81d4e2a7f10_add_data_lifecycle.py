"""add data lifecycle and legal holds

Revision ID: c81d4e2a7f10
Revises: 49b1f7a0c2de
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c81d4e2a7f10"
down_revision: Union[str, None] = "49b1f7a0c2de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("subject_type", sa.String(40)))
    op.add_column("audit_logs", sa.Column("subject_id", sa.String(36)))
    op.create_index(op.f("ix_audit_logs_subject_type"), "audit_logs", ["subject_type"])
    op.create_index(op.f("ix_audit_logs_subject_id"), "audit_logs", ["subject_id"])

    op.alter_column("media_attachments", "inbound_message_id", nullable=True)
    op.alter_column("media_attachments", "conversation_id", nullable=True)

    op.create_table(
        "legal_holds",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_admin_id", sa.String(36), nullable=False),
        sa.Column("released_at", sa.DateTime()),
        sa.Column("released_by_admin_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('conversation', 'ticket')", name="ck_legal_hold_subject_type"
        ),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["released_by_admin_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_type", "subject_id", name="uq_legal_hold_subject"),
    )
    for column in ("subject_type", "subject_id", "expires_at", "created_by_admin_id"):
        op.create_index(op.f(f"ix_legal_holds_{column}"), "legal_holds", [column])


def downgrade() -> None:
    op.drop_table("legal_holds")
    op.alter_column("media_attachments", "conversation_id", nullable=False)
    op.alter_column("media_attachments", "inbound_message_id", nullable=False)
    op.drop_index(op.f("ix_audit_logs_subject_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_subject_type"), table_name="audit_logs")
    op.drop_column("audit_logs", "subject_id")
    op.drop_column("audit_logs", "subject_type")
