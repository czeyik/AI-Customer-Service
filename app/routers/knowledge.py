from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import KnowledgeChunk, KnowledgeDocument
from app.schemas import KnowledgeDocumentResponse, KnowledgeIngestRequest
from app.services.retrieval import embed_text

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def require_admin_api_key(x_admin_api_key: str | None = Header(default=None)) -> None:
    if x_admin_api_key != get_settings().admin_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin API key")


@router.post("/documents", response_model=KnowledgeDocumentResponse)
def ingest_document(
    payload: KnowledgeIngestRequest,
    _: None = Depends(require_admin_api_key),
    db: Session = Depends(get_db),
) -> KnowledgeDocumentResponse:
    document = KnowledgeDocument(
        title=payload.title,
        source_type=payload.source_type,
        source_uri=payload.source_uri,
        language=payload.language,
        is_approved=payload.is_approved,
    )
    db.add(document)
    db.flush()
    for chunk_text in payload.chunks:
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                content=chunk_text,
                language=payload.language,
                tags=payload.tags,
                embedding=embed_text(chunk_text),
            )
        )
    db.commit()
    db.refresh(document)
    return KnowledgeDocumentResponse(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        language=document.language,
        chunk_count=len(payload.chunks),
        created_at=document.created_at,
    )

