import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
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
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    channel = Column(String(40), nullable=False, index=True)
    external_user_id = Column(String(255), nullable=False, index=True)
    preferred_language = Column(String(12), default="en", nullable=False)
    user_role = Column(String(40), nullable=True)
    risk_level = Column(String(40), default="normal", nullable=False)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_conversation_channel_external_user", "channel", "external_user_id"),
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


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id = Column(String(36), primary_key=True, default=new_uuid)
    public_id = Column(String(40), unique=True, nullable=False, index=True)
    status = Column(String(40), default="open", nullable=False, index=True)
    urgency = Column(String(40), default="normal", nullable=False, index=True)
    channel = Column(String(40), nullable=False, index=True)
    external_user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    account_id = Column(String(255), nullable=True)
    user_role = Column(String(40), nullable=True)
    issue_type = Column(String(80), nullable=False, index=True)
    language = Column(String(12), default="en", nullable=False)
    description = Column(Text, nullable=False)
    trip_id = Column(String(120), nullable=True)
    consent_given = Column(Boolean, default=False, nullable=False)
    attachment_count = Column(Integer, default=0, nullable=False)
    extra = Column(JSON, default=dict, nullable=False)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id = Column(String(36), primary_key=True, default=new_uuid)
    title = Column(String(255), nullable=False)
    source_type = Column(String(40), nullable=False)
    source_uri = Column(String(500), nullable=True)
    language = Column(String(12), default="en", nullable=False)
    is_approved = Column(Boolean, default=True, nullable=False)

    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id = Column(String(36), primary_key=True, default=new_uuid)
    document_id = Column(String(36), ForeignKey("knowledge_documents.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    language = Column(String(12), default="en", nullable=False, index=True)
    tags = Column(JSON, default=list, nullable=False)
    embedding = Column(JSON, default=list, nullable=False)

    document = relationship("KnowledgeDocument", back_populates="chunks")


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    actor = Column(String(255), nullable=False, index=True)
    event_type = Column(String(120), nullable=False, index=True)
    ip_address = Column(String(80), nullable=True)
    details = Column(JSON, default=dict, nullable=False)

