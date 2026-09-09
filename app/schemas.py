from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Channel = Literal["web", "whatsapp", "admin_test"]
UserRole = Literal["rider", "driver", "business_partner", "unknown"]
LaunchLanguage = Literal["en", "ms", "zh"]


class AttachmentPayload(BaseModel):
    filename: str = Field(..., max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    size_bytes: int | None = Field(default=None, ge=0)


class ChatRequest(BaseModel):
    channel: Channel = "web"
    external_user_id: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=1, max_length=4096)
    user_role: UserRole = "unknown"
    preferred_language: LaunchLanguage | None = None
    name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None
    phone_number: str | None = Field(
        default=None, max_length=32, pattern=r"^[+\d][\d ()-]{7,24}$"
    )
    account_id: str | None = Field(default=None, max_length=255)
    trip_id: str | None = Field(default=None, max_length=120)
    ride_details: str | None = Field(default=None, max_length=2000)
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
    document_key: str = Field(..., min_length=3, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]+$")
    title: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(default="manual", max_length=40)
    source_uri: str = Field(..., min_length=1, max_length=500)
    language: LaunchLanguage = "en"
    chunks: list[str] = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    effective_at: datetime | None = None
    activate: bool = False


class KnowledgeDocumentResponse(BaseModel):
    id: str
    document_key: str
    version: int
    title: str
    source_type: str
    source_uri: str | None
    language: str
    status: str
    chunk_count: int
    effective_at: datetime | None
    created_at: datetime


class MetaWebhookResult(BaseModel):
    ok: bool
    processed: int
    duplicates: int = 0
    status_updates: int = 0
