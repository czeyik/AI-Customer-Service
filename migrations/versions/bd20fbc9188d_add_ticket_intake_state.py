"""add ticket intake state and enforce ticket contact fields

Revision ID: bd20fbc9188d
Revises: f371a5ab9b0b
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bd20fbc9188d"
down_revision: Union[str, None] = "f371a5ab9b0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("intake_state", sa.String(length=40), nullable=False, server_default="idle"),
    )
    op.add_column(
        "conversations",
        sa.Column("intake_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("conversations", "intake_state", server_default=None)
    op.alter_column("conversations", "intake_data", server_default=None)

    op.alter_column("tickets", "name", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("tickets", "email", existing_type=sa.String(length=255), nullable=False)
    op.add_column("tickets", sa.Column("phone_number", sa.String(length=32), nullable=True))
    op.execute("UPDATE tickets SET phone_number = external_user_id WHERE phone_number IS NULL")
    op.alter_column("tickets", "phone_number", nullable=False)
    op.add_column("tickets", sa.Column("ride_details", sa.Text(), nullable=True))
    op.create_check_constraint("ck_tickets_consent_given", "tickets", "consent_given = true")
    op.create_check_constraint("ck_tickets_name_required", "tickets", "trim(name) <> ''")
    op.create_check_constraint("ck_tickets_email_required", "tickets", "trim(email) <> ''")
    op.create_check_constraint("ck_tickets_email_format", "tickets", "email LIKE '%_@_%._%'")
    op.create_check_constraint(
        "ck_tickets_phone_required", "tickets", "trim(phone_number) <> ''"
    )
    op.create_check_constraint(
        "ck_tickets_description_required", "tickets", "trim(description) <> ''"
    )


def downgrade() -> None:
    op.drop_constraint("ck_tickets_description_required", "tickets", type_="check")
    op.drop_constraint("ck_tickets_phone_required", "tickets", type_="check")
    op.drop_constraint("ck_tickets_email_format", "tickets", type_="check")
    op.drop_constraint("ck_tickets_email_required", "tickets", type_="check")
    op.drop_constraint("ck_tickets_name_required", "tickets", type_="check")
    op.drop_constraint("ck_tickets_consent_given", "tickets", type_="check")
    op.alter_column("tickets", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("tickets", "name", existing_type=sa.String(length=255), nullable=True)
    op.drop_column("tickets", "ride_details")
    op.drop_column("tickets", "phone_number")
    op.drop_column("conversations", "intake_data")
    op.drop_column("conversations", "intake_state")
