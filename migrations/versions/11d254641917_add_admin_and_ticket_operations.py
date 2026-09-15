"""add named admin and ticket operations

Revision ID: 11d254641917
Revises: 72b9b57f6d1a
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "11d254641917"
down_revision: Union[str, None] = "72b9b57f6d1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("admin_users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("admin_users", sa.Column("phone_number", sa.String(32), nullable=True))
    op.add_column("admin_users", sa.Column("totp_secret_ref", sa.String(255), nullable=True))
    op.add_column("admin_users", sa.Column("is_cco", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("admin_users", sa.Column("is_recovery_approver", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("admin_users", sa.Column("notify_new_tickets", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("admin_users", sa.Column("notify_urgent_tickets", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("admin_users", sa.Column("auth_version", sa.Integer(), server_default="1", nullable=False))
    op.execute(
        "UPDATE admin_users SET display_name = username, "
        "email = username || '@legacy.invalid', totp_secret_ref = 'legacy/' || id, is_active = false"
    )
    op.alter_column("admin_users", "display_name", nullable=False)
    op.alter_column("admin_users", "email", nullable=False)
    op.alter_column("admin_users", "totp_secret_ref", nullable=False)
    op.create_unique_constraint("uq_admin_users_email", "admin_users", ["email"])
    op.create_unique_constraint("uq_admin_users_totp_secret_ref", "admin_users", ["totp_secret_ref"])

    op.add_column("tickets", sa.Column("assigned_admin_id", sa.String(36), nullable=True))
    op.add_column("tickets", sa.Column("closed_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_tickets_assigned_admin", "tickets", "admin_users", ["assigned_admin_id"], ["id"])
    op.create_index(op.f("ix_tickets_assigned_admin_id"), "tickets", ["assigned_admin_id"])
    op.create_check_constraint("ck_tickets_status", "tickets", "status IN ('open', 'in_progress', 'closed')")

    op.create_table(
        "ticket_notes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("author_admin_id", sa.String(36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["author_admin_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ticket_notes_ticket_id"), "ticket_notes", ["ticket_id"])
    op.create_index(op.f("ix_ticket_notes_author_admin_id"), "ticket_notes", ["author_admin_id"])

    op.create_table(
        "support_notifications",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("recipient_admin_id", sa.String(36), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["recipient_admin_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_support_notifications_ticket_id"), "support_notifications", ["ticket_id"])
    op.create_index(op.f("ix_support_notifications_recipient_admin_id"), "support_notifications", ["recipient_admin_id"])
    op.create_index(op.f("ix_support_notifications_event_type"), "support_notifications", ["event_type"])
    op.create_index(op.f("ix_support_notifications_status"), "support_notifications", ["status"])
    op.create_index(op.f("ix_support_notifications_next_attempt_at"), "support_notifications", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_table("support_notifications")
    op.drop_table("ticket_notes")
    op.drop_constraint("ck_tickets_status", "tickets", type_="check")
    op.drop_index(op.f("ix_tickets_assigned_admin_id"), table_name="tickets")
    op.drop_constraint("fk_tickets_assigned_admin", "tickets", type_="foreignkey")
    op.drop_column("tickets", "closed_at")
    op.drop_column("tickets", "assigned_admin_id")
    op.drop_constraint("uq_admin_users_totp_secret_ref", "admin_users", type_="unique")
    op.drop_constraint("uq_admin_users_email", "admin_users", type_="unique")
    for column in (
        "auth_version", "notify_urgent_tickets", "notify_new_tickets", "is_recovery_approver",
        "is_cco", "totp_secret_ref", "phone_number", "email", "display_name",
    ):
        op.drop_column("admin_users", column)
