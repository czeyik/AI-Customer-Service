from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from datetime import datetime
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser, KnowledgeDocument
from app.routers.admin import get_current_admin, require_csrf
from app.schemas import KnowledgeDocumentResponse, KnowledgeIngestRequest
from app.security import read_session_token
from app.services.knowledge import (
    activate_knowledge,
    ingest_knowledge,
    remove_knowledge,
    rollback_knowledge,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/session")
def knowledge_session(
    request: Request, admin: AdminUser = Depends(get_current_admin)
) -> dict[str, str]:
    if not admin.is_cco:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CCO account required")
    session = read_session_token(request.cookies["dudu_admin_session"])
    return {"csrf_token": session["csrf"]}


def _response(document: KnowledgeDocument) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document.id,
        document_key=document.document_key,
        version=document.version,
        title=document.title,
        source_type=document.source_type,
        source_uri=document.source_uri,
        language=document.language,
        status=document.status,
        chunk_count=len(document.chunks),
        effective_at=document.effective_at,
        created_at=document.created_at,
    )


def _document_or_404(db: Session, document_id: str) -> KnowledgeDocument:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge not found")
    return document


def _mutate(operation, *args, **kwargs) -> KnowledgeDocumentResponse:
    try:
        return _response(operation(*args, **kwargs))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/documents", response_model=list[KnowledgeDocumentResponse])
def list_documents(
    db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> list[KnowledgeDocumentResponse]:
    if not admin.is_cco:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CCO account required")
    documents = (
        db.query(KnowledgeDocument)
        .order_by(
            KnowledgeDocument.document_key,
            KnowledgeDocument.language,
            KnowledgeDocument.version.desc(),
        )
        .limit(500)
        .all()
    )
    return [_response(document) for document in documents]


@router.post("/documents", response_model=KnowledgeDocumentResponse)
def ingest_document(
    payload: KnowledgeIngestRequest,
    request: Request,
    x_csrf_token: str = Header(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeDocumentResponse:
    require_csrf(request, x_csrf_token)
    return _mutate(
        ingest_knowledge,
        db,
        actor=admin,
        **payload.model_dump(),
    )


@router.post("/documents/{document_id}/activate", response_model=KnowledgeDocumentResponse)
def activate_document(
    document_id: str,
    request: Request,
    effective_at: datetime | None = Body(default=None, embed=True),
    x_csrf_token: str = Header(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeDocumentResponse:
    require_csrf(request, x_csrf_token)
    return _mutate(
        activate_knowledge, db, actor=admin, document=_document_or_404(db, document_id), effective_at=effective_at
    )


@router.post("/documents/{document_id}/remove", response_model=KnowledgeDocumentResponse)
def remove_document(
    document_id: str,
    request: Request,
    x_csrf_token: str = Header(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeDocumentResponse:
    require_csrf(request, x_csrf_token)
    return _mutate(
        remove_knowledge, db, actor=admin, document=_document_or_404(db, document_id)
    )


@router.post("/documents/{document_id}/rollback", response_model=KnowledgeDocumentResponse)
def rollback_document(
    document_id: str,
    request: Request,
    x_csrf_token: str = Header(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> KnowledgeDocumentResponse:
    require_csrf(request, x_csrf_token)
    return _mutate(
        rollback_knowledge, db, actor=admin, document=_document_or_404(db, document_id)
    )
