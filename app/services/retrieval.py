import re
from dataclasses import dataclass

from sqlalchemy import func
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
    "policy",
    "can",
    "i",
    "you",
    "saya",
    "dan",
    "yang",
    "untuk",
    "ini",
    "itu",
    "polisi",
    "的",
    "了",
}
CHINESE_STOPWORDS = {"政策", "是什么"}


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
            tokens.update(
                bigram
                for index in range(len(token) - 1)
                if (bigram := token[index : index + 2]) not in CHINESE_STOPWORDS
            )
        elif token not in STOPWORDS and len(token) > 1:
            tokens.add(token)
    aliases = {
        "order": "book", "booking": "book", "bookings": "book", "tempah": "book",
        "kereta": "ride", "car": "ride", "预订": "book", "叫车": "book", "订车": "book",
        "price": "fare", "cost": "fare", "tambang": "fare", "车费": "fare",
        "signin": "login", "log": "login", "登录": "login", "masuk": "login",
        "voucher": "promotion", "promo": "promotion", "discount": "promotion",
        "promosi": "promotion", "优惠": "promotion", "客服": "support", "hours": "support",
        "sokongan": "support", "minggu": "hours", "weekend": "hours", "weekends": "hours",
    }
    return tokens | {aliases[token] for token in tokens if token in aliases}


def score_text(query: str, content: str, tags: list[str] | None = None) -> float:
    query_tokens = tokenize(query)
    content_tokens = tokenize(content)
    tag_tokens = tokenize(" ".join(tags or []))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    tag_overlap = len(query_tokens & tag_tokens)
    return (overlap + (tag_overlap * 1.5)) / max(min(len(query_tokens), 8), 1)


def search_knowledge(db: Session, query: str, language: str, limit: int = 4) -> RetrievalResult:
    query_tokens = tokenize(query)
    if not query_tokens or limit < 1:
        return RetrievalResult(chunks=[], confidence=0.0)
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(
            KnowledgeDocument.status == "active",
            KnowledgeDocument.effective_at.is_not(None),
            KnowledgeDocument.effective_at <= func.now(),
        )
        .order_by(KnowledgeDocument.document_key, KnowledgeDocument.language, KnowledgeChunk.id)
        .limit(501)
        .all()
    )
    # ponytail: rank the complete small approved corpus up to 500 chunks. If it grows
    # past this ceiling, clarify instead of silently dropping candidates; add SQL ranking then.
    if len(rows) > 500:
        return RetrievalResult(chunks=[], confidence=0.0)
    candidates: list[RetrievedChunk] = []
    for chunk, document in rows:
        score = score_text(query, chunk.content + " " + document.title, chunk.tags)
        if score > 0 or len(rows) <= 32:
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
    candidates.sort(key=lambda item: (item.score, item.language == language), reverse=True)
    selected = []
    seen = {}
    for candidate in candidates:
        key = candidate.document_key
        if key in seen and seen[key] != candidate.language:
            continue
        seen[key] = candidate.language
        selected.append(candidate)
        if len(selected) == limit:
            break
    confidence = selected[0].score if selected else 0.0
    return RetrievalResult(chunks=selected, confidence=round(confidence, 3))
