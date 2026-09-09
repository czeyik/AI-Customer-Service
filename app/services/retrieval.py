import re
from dataclasses import dataclass

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "for",
    "my",
    "is",
    "are",
    "how",
    "what",
    "can",
    "i",
    "you",
    "saya",
    "dan",
    "yang",
    "untuk",
    "ini",
    "itu",
    "的",
    "了",
}


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    source_title: str
    language: str
    score: float
    source_uri: str | None = None
    document_key: str = ""
    version: int = 0


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    confidence: float


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text.lower()):
        if "\u4e00" <= token[0] <= "\u9fff":
            tokens.update(token[index : index + 2] for index in range(len(token) - 1))
        elif token not in STOPWORDS and len(token) > 1:
            tokens.add(token)
    return tokens


def score_text(query: str, content: str, tags: list[str] | None = None) -> float:
    query_tokens = tokenize(query)
    content_tokens = tokenize(content)
    tag_tokens = set(tags or [])
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    tag_overlap = len(query_tokens & tag_tokens)
    return (overlap + (tag_overlap * 1.5)) / max(len(query_tokens), 1)


def search_knowledge(db: Session, query: str, language: str, limit: int = 4) -> RetrievalResult:
    query_tokens = tokenize(query)
    if not query_tokens or limit < 1:
        return RetrievalResult(chunks=[], confidence=0.0)
    terms = sorted(query_tokens, key=len, reverse=True)[:12]
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(
            KnowledgeDocument.status == "active",
            KnowledgeDocument.language == language,
            KnowledgeChunk.language == language,
            KnowledgeDocument.effective_at.is_not(None),
            KnowledgeDocument.effective_at <= func.now(),
            or_(*(func.lower(KnowledgeChunk.content).like(f"%{term}%") for term in terms)),
        )
        .limit(max(limit * 8, 20))
        .all()
    )
    candidates: list[RetrievedChunk] = []
    for chunk, document in rows:
        score = score_text(query, chunk.content, chunk.tags) + 0.05
        if score > 0:
            candidates.append(
                RetrievedChunk(
                    content=chunk.content,
                    source_title=document.title,
                    source_uri=document.source_uri,
                    document_key=document.document_key,
                    version=document.version,
                    language=chunk.language,
                    score=score,
                )
            )
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:limit]
    confidence = selected[0].score if selected else 0.0
    return RetrievalResult(chunks=selected, confidence=round(confidence, 3))
