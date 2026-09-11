import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    id = Column(String(36), primary_key=True, default=new_uuid)
    username = Column(String(120), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone_number = Column(String(32), nullable=True)
    password_hash = Column(String(255), nullable=False)
    totp_secret_ref = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_cco = Column(Boolean, default=False, nullable=False)
    is_recovery_approver = Column(Boolean, default=False, nullable=False)
    notify_new_tickets = Column(Boolean, default=False, nullable=False)
    notify_urgent_tickets = Column(Boolean, default=False, nullable=False)
    auth_version = Column(Integer, default=1, nullable=False)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    channel = Column(String(40), nullable=False, index=True)
    external_user_id = Column(String(255), nullable=False, index=True)
    preferred_language = Column(String(12), default="en", nullable=False)
    user_role = Column(String(40), nullable=True)
    risk_level = Column(String(40), default="normal", nullable=False)
    intake_state = Column(String(40), default="idle", nullable=False)
    intake_data = Column(JSON, default=dict, nullable=False)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    attachments = relationship("MediaAttachment", back_populates="conversation")

    __table_args__ = (
        Index("ix_conversation_channel_external_user", "channel", "external_user_id", unique=True),
    )


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=new_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False, index=True)
    direction = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    language = Column(String(12), default="en", nullable=False)
    safety_flags = Column(JSON, default=list, nullable=False)
    payload = Column(JSON, default=dict, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


class WhatsAppInboundMessage(Base, TimestampMixin):
    __tablename__ = "whatsapp_inbound_messages"

    id = Column(String(36), primary_key=True, default=new_uuid)
    provider_message_id = Column(String(255), unique=True, nullable=False, index=True)
    sender = Column(String(32), nullable=False)
    phone_number_id = Column(String(64), nullable=False)
    message_type = Column(String(40), nullable=False)
    payload = Column(JSON, default=dict, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String(20), default="queued", nullable=False, index=True)
    lease_until = Column(DateTime, nullable=True)
    claim_token = Column(String(36), nullable=True)


class WhatsAppOutboundMessage(Base, TimestampMixin):
    __tablename__ = "whatsapp_outbound_messages"

    id = Column(String(36), primary_key=True, default=new_uuid)
    inbound_message_id = Column(
        String(36), ForeignKey("whatsapp_inbound_messages.id"), unique=True, nullable=False
    )
    recipient = Column(String(32), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(40), default="queued", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    provider_message_id = Column(String(255), unique=True, nullable=True, index=True)
    last_error_code = Column(String(80), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id = Column(String(36), primary_key=True, default=new_uuid)
    public_id = Column(String(40), unique=True, nullable=False, index=True)
    status = Column(String(40), default="open", nullable=False, index=True)
    assigned_admin_id = Column(String(36), ForeignKey("admin_users.id"), nullable=True, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True, index=True)
    urgency = Column(String(40), default="normal", nullable=False, index=True)
    channel = Column(String(40), nullable=False, index=True)
    external_user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    phone_number = Column(String(32), nullable=False)
    account_id = Column(String(255), nullable=True)
    user_role = Column(String(40), nullable=True)
    issue_type = Column(String(80), nullable=False, index=True)
    language = Column(String(12), default="en", nullable=False)
    description = Column(Text, nullable=False)
    trip_id = Column(String(120), nullable=True)
    ride_details = Column(Text, nullable=True)
    consent_given = Column(Boolean, default=False, nullable=False)
    attachment_count = Column(Integer, default=0, nullable=False)
    extra = Column(JSON, default=dict, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    assigned_admin = relationship("AdminUser")
    conversation = relationship("Conversation")
    notes = relationship("TicketNote", back_populates="ticket", cascade="all, delete-orphan")
    attachments = relationship("MediaAttachment", back_populates="ticket")

    __table_args__ = (
        CheckConstraint("consent_given = true", name="ck_tickets_consent_given"),
        CheckConstraint("trim(name) <> ''", name="ck_tickets_name_required"),
        CheckConstraint("trim(email) <> ''", name="ck_tickets_email_required"),
        CheckConstraint("email LIKE '%_@_%._%'", name="ck_tickets_email_format"),
        CheckConstraint("trim(phone_number) <> ''", name="ck_tickets_phone_required"),
        CheckConstraint("trim(description) <> ''", name="ck_tickets_description_required"),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'closed')", name="ck_tickets_status"
        ),
    )


class MediaAttachment(Base, TimestampMixin):
    __tablename__ = "media_attachments"

    id = Column(String(36), primary_key=True, default=new_uuid)
    provider_media_id = Column(String(255), unique=True, nullable=False, index=True)
    inbound_message_id = Column(
        String(36), ForeignKey("whatsapp_inbound_messages.id"), unique=True, nullable=True
    )
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True, index=True)
    ticket_id = Column(String(36), ForeignKey("tickets.id"), nullable=True, index=True)
    evidence_group = Column(String(36), nullable=True, index=True)
    media_type = Column(String(20), nullable=False)
    declared_mime_type = Column(String(120), nullable=False)
    detected_mime_type = Column(String(120), nullable=True)
    provider_sha256 = Column(String(128), nullable=True)
    sha256 = Column(String(64), nullable=True, index=True)
    size_bytes = Column(Integer, nullable=True)
    status = Column(String(20), default="queued", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    object_key = Column(String(500), unique=True, nullable=True)
    scan_result = Column(String(80), nullable=True)
    failure_code = Column(String(80), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="attachments")
    ticket = relationship("Ticket", back_populates="attachments")

    __table_args__ = (
        CheckConstraint("media_type IN ('image', 'video')", name="ck_media_type"),
        CheckConstraint(
            "status IN ('queued', 'approved', 'rejected', 'failed', 'deleted')",
            name="ck_media_status",
        ),
    )


class TicketNote(Base, TimestampMixin):
    __tablename__ = "ticket_notes"

    id = Column(String(36), primary_key=True, default=new_uuid)
    ticket_id = Column(String(36), ForeignKey("tickets.id"), nullable=False, index=True)
    author_admin_id = Column(String(36), ForeignKey("admin_users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)

    ticket = relationship("Ticket", back_populates="notes")
    author = relationship("AdminUser")


class SupportNotification(Base, TimestampMixin):
    __tablename__ = "support_notifications"

    id = Column(String(36), primary_key=True, default=new_uuid)
    ticket_id = Column(String(36), ForeignKey("tickets.id"), nullable=False, index=True)
    recipient_admin_id = Column(
        String(36), ForeignKey("admin_users.id"), nullable=True, index=True
    )
    channel = Column(String(20), nullable=False)
    recipient = Column(String(255), nullable=False)
    event_type = Column(String(80), nullable=False, index=True)
    status = Column(String(20), default="pending", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_error = Column(String(255), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    payload = Column(JSON, default=dict, nullable=False)

    ticket = relationship("Ticket")
    recipient_admin = relationship("AdminUser")


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id = Column(String(36), primary_key=True, default=new_uuid)
    document_key = Column(String(120), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    source_type = Column(String(40), nullable=False)
    source_uri = Column(String(500), nullable=True)
    language = Column(String(12), default="en", nullable=False)
    status = Column(String(20), default="draft", nullable=False, index=True)
    effective_at = Column(DateTime, nullable=True)
    approved_by_admin_id = Column(
        String(36), ForeignKey("admin_users.id"), nullable=True, index=True
    )
    supersedes_document_id = Column(
        String(36), ForeignKey("knowledge_documents.id"), nullable=True
    )
    content_hash = Column(String(64), nullable=False)

    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")
    approved_by = relationship("AdminUser")

    __table_args__ = (
        UniqueConstraint(
            "document_key", "language", "version", name="uq_knowledge_document_version"
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded', 'removed')",
            name="ck_knowledge_documents_status",
        ),
        Index(
            "uq_knowledge_document_active",
            "document_key",
            "language",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id = Column(String(36), primary_key=True, default=new_uuid)
    document_id = Column(String(36), ForeignKey("knowledge_documents.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    language = Column(String(12), default="en", nullable=False, index=True)
    tags = Column(JSON, default=list, nullable=False)

    document = relationship("KnowledgeDocument", back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_knowledge_chunks_content_trgm",
            func.lower(content).label("content_lower"),
            postgresql_using="gin",
            postgresql_ops={"content_lower": "gin_trgm_ops"},
        ),
    )


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    actor = Column(String(255), nullable=False, index=True)
    event_type = Column(String(120), nullable=False, index=True)
    ip_address = Column(String(80), nullable=True)
    subject_type = Column(String(40), nullable=True, index=True)
    subject_id = Column(String(36), nullable=True, index=True)
    details = Column(JSON, default=dict, nullable=False)


class LegalHold(Base, TimestampMixin):
    __tablename__ = "legal_holds"

    id = Column(String(36), primary_key=True, default=new_uuid)
    subject_type = Column(String(20), nullable=False, index=True)
    subject_id = Column(String(36), nullable=False, index=True)
    reason = Column(String(500), nullable=False)
    reference = Column(String(120), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_by_admin_id = Column(
        String(36), ForeignKey("admin_users.id"), nullable=False, index=True
    )
    released_at = Column(DateTime, nullable=True)
    released_by_admin_id = Column(String(36), ForeignKey("admin_users.id"), nullable=True)

    created_by = relationship("AdminUser", foreign_keys=[created_by_admin_id])
    released_by = relationship("AdminUser", foreign_keys=[released_by_admin_id])

    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('conversation', 'ticket')", name="ck_legal_hold_subject_type"
        ),
        UniqueConstraint("subject_type", "subject_id", name="uq_legal_hold_subject"),
    )


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key_hash = Column(String(64), primary_key=True)
    request_count = Column(Integer, nullable=False)
    window_started_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("request_count > 0", name="ck_rate_limit_request_count"),
    )
