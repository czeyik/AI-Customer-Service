from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

Channel = Literal["web", "whatsapp", "instagram", "admin_test"]
UserRole = Literal["rider", "driver", "unknown"]


class AttachmentPayload(BaseModel):
    filename: str = Field(..., max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    size_bytes: int | None = Field(default=None, ge=0)


class ChatRequest(BaseModel):
    channel: Channel = "web"
    external_user_id: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=1, max_length=4000)
    user_role: UserRole = "unknown"
    preferred_language: str | None = Field(default=None, max_length=12)
    name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None
    account_id: str | None = Field(default=None, max_length=255)
    trip_id: str | None = Field(default=None, max_length=120)
    consent_to_ticket: bool = False
    create_ticket: bool = False
    attachments: list[AttachmentPayload] = Field(default_factory=list, max_length=5)


class TicketResponse(BaseModel):
    public_id: str
    status: str
    urgency: str
    issue_type: str


class ChatResponse(BaseModel):
    answer: str
    language: str
    confidence: float = 0.0
    safety_flags: list[str] = Field(default_factory=list)
    needs_ticket_consent: bool = False
    ticket: TicketResponse | None = None
    sources: list[str] = Field(default_factory=list)


class KnowledgeIngestRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(default="manual", max_length=40)
    source_uri: str | None = Field(default=None, max_length=500)
    language: str = Field(default="en", max_length=12)
    chunks: list[str] = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    is_approved: bool = True


class KnowledgeDocumentResponse(BaseModel):
    id: str
    title: str
    source_type: str
    language: str
    chunk_count: int
    created_at: datetime


class MetaWebhookResult(BaseModel):
    ok: bool
    processed: int
    responses: list[dict[str, Any]] = Field(default_factory=list)

