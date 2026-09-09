"""version knowledge governance

Revision ID: 8b61c2f2a8d7
Revises: 11d254641917
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8b61c2f2a8d7"
down_revision: Union[str, None] = "11d254641917"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("document_key", sa.String(120)))
    op.add_column("knowledge_documents", sa.Column("version", sa.Integer(), server_default="1"))
    op.add_column(
        "knowledge_documents", sa.Column("status", sa.String(20), server_default="draft")
    )
    op.add_column("knowledge_documents", sa.Column("effective_at", sa.DateTime()))
    op.add_column("knowledge_documents", sa.Column("approved_by_admin_id", sa.String(36)))
    op.add_column("knowledge_documents", sa.Column("supersedes_document_id", sa.String(36)))
    op.add_column("knowledge_documents", sa.Column("content_hash", sa.String(64)))
    op.execute(
        "UPDATE knowledge_documents SET document_key = id, content_hash = "
        "'legacy-' || id, status = 'draft'"
    )
    for column in ("document_key", "version", "status", "content_hash"):
        op.alter_column("knowledge_documents", column, nullable=False)
    op.create_index(
        op.f("ix_knowledge_documents_document_key"), "knowledge_documents", ["document_key"]
    )
    op.create_index(op.f("ix_knowledge_documents_status"), "knowledge_documents", ["status"])
    op.create_index(
        op.f("ix_knowledge_documents_approved_by_admin_id"),
        "knowledge_documents",
        ["approved_by_admin_id"],
    )
    op.create_unique_constraint(
        "uq_knowledge_document_version",
        "knowledge_documents",
        ["document_key", "language", "version"],
    )
    op.create_check_constraint(
        "ck_knowledge_documents_status",
        "knowledge_documents",
        "status IN ('draft', 'active', 'superseded', 'removed')",
    )
    op.create_index(
        "uq_knowledge_document_active",
        "knowledge_documents",
        ["document_key", "language"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_foreign_key(
        "fk_knowledge_documents_approver",
        "knowledge_documents",
        "admin_users",
        ["approved_by_admin_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_knowledge_documents_supersedes",
        "knowledge_documents",
        "knowledge_documents",
        ["supersedes_document_id"],
        ["id"],
    )
    op.drop_column("knowledge_documents", "is_approved")
    op.drop_column("knowledge_chunks", "embedding")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_content_trgm ON knowledge_chunks "
        "USING gin (lower(content) gin_trgm_ops)"
    )
    op.execute("DROP EXTENSION IF EXISTS vector")


def downgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_trgm")
    op.add_column(
        "knowledge_chunks", sa.Column("embedding", sa.JSON(), server_default="[]", nullable=False)
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("is_approved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute("UPDATE knowledge_documents SET is_approved = (status = 'active')")
    op.drop_constraint(
        "fk_knowledge_documents_supersedes", "knowledge_documents", type_="foreignkey"
    )
    op.drop_constraint("fk_knowledge_documents_approver", "knowledge_documents", type_="foreignkey")
    op.drop_index("uq_knowledge_document_active", table_name="knowledge_documents")
    op.drop_constraint("ck_knowledge_documents_status", "knowledge_documents", type_="check")
    op.drop_constraint("uq_knowledge_document_version", "knowledge_documents", type_="unique")
    op.drop_index(
        op.f("ix_knowledge_documents_approved_by_admin_id"),
        table_name="knowledge_documents",
    )
    op.drop_index(op.f("ix_knowledge_documents_status"), table_name="knowledge_documents")
    op.drop_index(op.f("ix_knowledge_documents_document_key"), table_name="knowledge_documents")
    for column in (
        "content_hash",
        "supersedes_document_id",
        "approved_by_admin_id",
        "effective_at",
        "status",
        "version",
        "document_key",
    ):
        op.drop_column("knowledge_documents", column)
