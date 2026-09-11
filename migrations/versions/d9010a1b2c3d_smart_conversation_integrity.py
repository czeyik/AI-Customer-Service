"""Bind evidence ownership and add durable inbound claims and conversation uniqueness."""
from alembic import op
import sqlalchemy as sa

revision = "d9010a1b2c3d"
down_revision = "c81d4e2a7f10"
branch_labels = None
depends_on = None


def upgrade():
    duplicate = op.get_bind().execute(sa.text(
        "SELECT 1 FROM conversations GROUP BY channel, external_user_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("Reconcile duplicate conversation ownership before applying this migration")
    op.drop_index("ix_conversation_channel_external_user", table_name="conversations")
    op.create_index("ix_conversation_channel_external_user", "conversations", ["channel", "external_user_id"], unique=True)
    op.add_column("media_attachments", sa.Column("evidence_group", sa.String(36), nullable=True))
    op.create_index("ix_media_attachments_evidence_group", "media_attachments", ["evidence_group"])
    op.add_column("whatsapp_inbound_messages", sa.Column("status", sa.String(20), nullable=False, server_default="done"))
    op.add_column("whatsapp_inbound_messages", sa.Column("lease_until", sa.DateTime(), nullable=True))
    op.add_column("whatsapp_inbound_messages", sa.Column("claim_token", sa.String(36), nullable=True))
    op.create_index("ix_whatsapp_inbound_messages_status", "whatsapp_inbound_messages", ["status"])


def downgrade():
    op.drop_index("ix_whatsapp_inbound_messages_status", table_name="whatsapp_inbound_messages")
    for name in ("claim_token", "lease_until", "status"):
        op.drop_column("whatsapp_inbound_messages", name)
    op.drop_index("ix_media_attachments_evidence_group", table_name="media_attachments")
    op.drop_column("media_attachments", "evidence_group")
    op.drop_index("ix_conversation_channel_external_user", table_name="conversations")
    op.create_index("ix_conversation_channel_external_user", "conversations", ["channel", "external_user_id"])
