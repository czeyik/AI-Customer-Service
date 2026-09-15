import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog, KnowledgeChunk, KnowledgeDocument


DOCUMENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,119}$")
LANGUAGES = {"en", "ms", "zh"}


def _require_cco(actor: AdminUser) -> None:
    if not actor.is_active or not actor.is_cco:
        raise ValueError("an active CCO account is required")


def _normalized_chunks(chunks: list[str]) -> list[str]:
    normalized = [
        re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", " ".join(chunk.split()))
        for chunk in chunks
        if chunk.strip()
    ]
    if not normalized or len(normalized) > 100 or any(len(chunk) > 4000 for chunk in normalized):
        raise ValueError("1-100 non-empty chunks of at most 4000 characters are required")
    return normalized


def _audit(
    db: Session,
    actor: AdminUser,
    event_type: str,
    document: KnowledgeDocument,
    **details: str | int,
) -> None:
    db.add(
        AuditLog(
            actor=actor.username,
            event_type=event_type,
            details={
                "actor_admin_id": actor.id,
                "document_id": document.id,
                "document_key": document.document_key,
                "language": document.language,
                "version": document.version,
                "source_uri": document.source_uri,
                **details,
            },
        )
    )


def _active_document(db: Session, document_key: str, language: str) -> KnowledgeDocument | None:
    return (
        db.query(KnowledgeDocument)
        .filter_by(document_key=document_key, language=language, status="active")
        .first()
    )


def activate_knowledge(
    db: Session, *, actor: AdminUser, document: KnowledgeDocument, effective_at: datetime | None = None
) -> KnowledgeDocument:
    _require_cco(actor)
    if document.status not in {"draft", "superseded", "removed"}:
        raise ValueError("only an inactive knowledge version can be activated")
    if effective_at is not None:
        document.effective_at = effective_at
    if not document.effective_at:
        raise ValueError("an effective date is required before activation")
    previous = _active_document(db, document.document_key, document.language)
    if previous and previous.id != document.id:
        previous.status = "superseded"
        document.supersedes_document_id = previous.id
        db.flush()
    document.status = "active"
    document.approved_by_admin_id = actor.id
    _audit(
        db,
        actor,
        "knowledge_published",
        document,
        effective_at=document.effective_at.isoformat(),
        replaced_document_id=previous.id if previous else "",
        replaced_version=previous.version if previous else 0,
    )
    db.commit()
    db.refresh(document)
    return document


def ingest_knowledge(
    db: Session,
    *,
    actor: AdminUser,
    document_key: str,
    title: str,
    source_type: str,
    source_uri: str,
    language: str,
    chunks: list[str],
    tags: list[str] | None = None,
    effective_at: datetime | None = None,
    activate: bool = False,
) -> KnowledgeDocument:
    _require_cco(actor)
    document_key = document_key.strip().lower()
    title = title.strip()
    source_type = source_type.strip()
    source_uri = source_uri.strip()
    tags = sorted({tag.strip().lower() for tag in tags or [] if tag.strip()})
    chunks = _normalized_chunks(chunks)
    if not DOCUMENT_KEY_RE.fullmatch(document_key):
        raise ValueError("document key must be a lowercase URL-safe identifier")
    if not title or not source_type or not source_uri:
        raise ValueError("title, source type, and source URI are required")
    if language not in LANGUAGES:
        raise ValueError("language must be en, ms, or zh")
    if len(tags) > 20:
        raise ValueError("at most 20 tags are allowed")

    digest = hashlib.sha256(
        json.dumps(
            {
                "title": title,
                "source_type": source_type,
                "source_uri": source_uri,
                "language": language,
                "chunks": chunks,
                "tags": tags,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = (
        db.query(KnowledgeDocument)
        .filter_by(document_key=document_key, language=language, content_hash=digest)
        .first()
    )
    if existing:
        if activate and existing.status != "active":
            return activate_knowledge(db, actor=actor, document=existing)
        return existing

    version = (
        db.query(func.max(KnowledgeDocument.version))
        .filter_by(document_key=document_key, language=language)
        .scalar()
        or 0
    ) + 1
    document = KnowledgeDocument(
        document_key=document_key,
        version=version,
        title=title,
        source_type=source_type,
        source_uri=source_uri,
        language=language,
        status="draft",
        effective_at=effective_at,
        content_hash=digest,
    )
    db.add(document)
    db.flush()
    db.add_all(
        KnowledgeChunk(
            document_id=document.id,
            content=chunk,
            language=language,
            tags=tags,
        )
        for chunk in chunks
    )
    _audit(db, actor, "knowledge_drafted", document)
    db.commit()
    db.refresh(document)
    return activate_knowledge(db, actor=actor, document=document) if activate else document


def remove_knowledge(
    db: Session, *, actor: AdminUser, document: KnowledgeDocument
) -> KnowledgeDocument:
    _require_cco(actor)
    if document.status != "active":
        raise ValueError("only active knowledge can be removed")
    document.status = "removed"
    _audit(db, actor, "knowledge_removed", document)
    db.commit()
    db.refresh(document)
    return document


def rollback_knowledge(
    db: Session, *, actor: AdminUser, document: KnowledgeDocument
) -> KnowledgeDocument:
    _require_cco(actor)
    if document.status == "active":
        raise ValueError("the requested version is already active")
    current = _active_document(db, document.document_key, document.language)
    if current:
        current.status = "superseded"
        db.flush()
    document.status = "active"
    document.approved_by_admin_id = actor.id
    _audit(
        db,
        actor,
        "knowledge_rolled_back",
        document,
        replaced_document_id=current.id if current else "",
        replaced_version=current.version if current else 0,
    )
    db.commit()
    db.refresh(document)
    return document
