"""add secure media pipeline

Revision ID: 69f51c2de537
Revises: 8b61c2f2a8d7
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "69f51c2de537"
down_revision: Union[str, None] = "8b61c2f2a8d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("conversation_id", sa.String(36)))
    op.create_index(op.f("ix_tickets_conversation_id"), "tickets", ["conversation_id"])
    op.create_foreign_key(
        "fk_tickets_conversation", "tickets", "conversations", ["conversation_id"], ["id"]
    )
    op.create_table(
        "media_attachments",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_media_id", sa.String(255), nullable=False),
        sa.Column("inbound_message_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("ticket_id", sa.String(36)),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("declared_mime_type", sa.String(120), nullable=False),
        sa.Column("detected_mime_type", sa.String(120)),
        sa.Column("provider_sha256", sa.String(128)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("object_key", sa.String(500)),
        sa.Column("scan_result", sa.String(80)),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("deleted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("media_type IN ('image', 'video')", name="ck_media_type"),
        sa.CheckConstraint(
            "status IN ('queued', 'approved', 'rejected', 'failed', 'deleted')",
            name="ck_media_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_media_conversation"
        ),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"],
            ["whatsapp_inbound_messages.id"],
            name="fk_media_inbound_message",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_media_ticket"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbound_message_id"),
        sa.UniqueConstraint("object_key"),
    )
    for column in (
        "conversation_id",
        "next_attempt_at",
        "sha256",
        "status",
        "ticket_id",
    ):
        op.create_index(op.f(f"ix_media_attachments_{column}"), "media_attachments", [column])
    op.create_index(
        op.f("ix_media_attachments_provider_media_id"),
        "media_attachments",
        ["provider_media_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("media_attachments")
    op.drop_constraint("fk_tickets_conversation", "tickets", type_="foreignkey")
    op.drop_index(op.f("ix_tickets_conversation_id"), table_name="tickets")
    op.drop_column("tickets", "conversation_id")
