"""add WhatsApp transport inbox and outbox

Revision ID: 72b9b57f6d1a
Revises: bd20fbc9188d
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "72b9b57f6d1a"
down_revision: Union[str, None] = "bd20fbc9188d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_inbound_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("sender", sa.String(length=32), nullable=False),
        sa.Column("phone_number_id", sa.String(length=64), nullable=False),
        sa.Column("message_type", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_inbound_messages_provider_message_id"),
        "whatsapp_inbound_messages",
        ["provider_message_id"],
        unique=True,
    )
    op.create_table(
        "whatsapp_outbound_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("inbound_message_id", sa.String(length=36), nullable=False),
        sa.Column("recipient", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["inbound_message_id"], ["whatsapp_inbound_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbound_message_id"),
    )
    op.create_index(
        op.f("ix_whatsapp_outbound_messages_next_attempt_at"),
        "whatsapp_outbound_messages",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_outbound_messages_provider_message_id"),
        "whatsapp_outbound_messages",
        ["provider_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_whatsapp_outbound_messages_status"),
        "whatsapp_outbound_messages",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_whatsapp_outbound_messages_status"),
        table_name="whatsapp_outbound_messages",
    )
    op.drop_index(
        op.f("ix_whatsapp_outbound_messages_provider_message_id"),
        table_name="whatsapp_outbound_messages",
    )
    op.drop_index(
        op.f("ix_whatsapp_outbound_messages_next_attempt_at"),
        table_name="whatsapp_outbound_messages",
    )
    op.drop_table("whatsapp_outbound_messages")
    op.drop_index(
        op.f("ix_whatsapp_inbound_messages_provider_message_id"),
        table_name="whatsapp_inbound_messages",
    )
    op.drop_table("whatsapp_inbound_messages")
