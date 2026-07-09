import math
import re
from dataclasses import dataclass

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
}


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    source_title: str
    language: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    confidence: float


def tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", text)}
    return {token for token in tokens if token not in STOPWORDS and len(token) > 1}


def embed_text(text: str, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        vector[hash(token) % dimensions] += 1.0
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / magnitude, 6) for value in vector]


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
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(KnowledgeDocument.is_approved.is_(True))
        .all()
    )
    candidates: list[RetrievedChunk] = []
    for chunk, document in rows:
        if chunk.language not in {language, "en"}:
            continue
        language_bonus = 0.05 if chunk.language == language else 0.0
        score = score_text(query, chunk.content, chunk.tags) + language_bonus
        if score > 0:
            candidates.append(
                RetrievedChunk(
                    content=chunk.content,
                    source_title=document.title,
                    language=chunk.language,
                    score=score,
                )
            )
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:limit]
    confidence = selected[0].score if selected else 0.0
    return RetrievalResult(chunks=selected, confidence=round(confidence, 3))

