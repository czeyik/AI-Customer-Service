from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import AdminUser, AuditLog, Base, KnowledgeDocument
from app.routers.knowledge import ingest_document
from app.schemas import KnowledgeIngestRequest
from app.security import make_session_token
from app.services.knowledge import (
    activate_knowledge,
    ingest_knowledge,
    remove_knowledge,
    rollback_knowledge,
)
from app.services.retrieval import search_knowledge
from app.services.website_knowledge import ContentParser
from scripts.ingest_seed import corpus_records, ingest_seed


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


def admin(db: Session, username: str, *, is_cco: bool) -> AdminUser:
    value = AdminUser(
        username=username,
        display_name=username.title(),
        email=f"{username}@example.com",
        password_hash="unused",
        totp_secret_ref=f"admin/{username}/totp",
        is_cco=is_cco,
    )
    db.add(value)
    db.commit()
    return value


def test_cco_publish_update_remove_and_rollback_are_attributable(
    db_session: Session,
) -> None:
    jane = admin(db_session, "jane", is_cco=True)
    cze = admin(db_session, "czeyik", is_cco=False)
    values = {
        "document_key": "fare-policy",
        "title": "Fare policy",
        "source_type": "website",
        "source_uri": "https://duducar.co/policy",
        "language": "en",
        "tags": ["fare"],
        "effective_at": datetime(2020, 1, 1),
    }
    with pytest.raises(ValueError, match="CCO"):
        ingest_knowledge(db_session, actor=cze, chunks=["Unapproved fare text."], **values)

    version_one = ingest_knowledge(
        db_session, actor=jane, chunks=["The original fare policy."], activate=True, **values
    )
    version_two = ingest_knowledge(
        db_session, actor=jane, chunks=["The corrected fare policy."], **values
    )
    assert version_one.version == 1 and version_one.status == "active"
    assert version_two.version == 2 and version_two.status == "draft"
    assert "original" in search_knowledge(db_session, "fare policy", "en").chunks[0].content

    activate_knowledge(db_session, actor=jane, document=version_two)
    assert version_one.status == "superseded" and version_two.status == "active"
    result = search_knowledge(db_session, "fare policy", "en")
    assert len(result.chunks) == 1 and result.chunks[0].version == 2
    assert result.chunks[0].source_uri == "https://duducar.co/policy"

    remove_knowledge(db_session, actor=jane, document=version_two)
    assert not search_knowledge(db_session, "fare policy", "en").chunks
    rollback_knowledge(db_session, actor=jane, document=version_one)
    assert search_knowledge(db_session, "fare policy", "en").chunks[0].version == 1

    events = db_session.query(AuditLog).filter(AuditLog.event_type.like("knowledge_%")).all()
    assert {event.actor for event in events} == {"jane"}
    assert {event.event_type for event in events} >= {
        "knowledge_drafted",
        "knowledge_published",
        "knowledge_removed",
        "knowledge_rolled_back",
    }
    assert all(event.details["source_uri"] == "https://duducar.co/policy" for event in events)
    assert all(event.details["actor_admin_id"] == jane.id for event in events)


def test_approved_corpus_has_complete_trilingual_search_coverage(db_session: Session) -> None:
    jane = admin(db_session, "jane", is_cco=True)
    records = corpus_records()
    assert len(records) == 24
    assert {record["language"] for record in records} == {"en", "ms", "zh"}
    assert all(
        {record["language"] for record in records if record["document_key"] == key}
        == {"en", "ms", "zh"}
        for key in {record["document_key"] for record in records}
    )

    assert ingest_seed(db_session, jane) == 24
    assert db_session.query(KnowledgeDocument).filter_by(status="active").count() == 24
    checks = (
        ("fare payment", "en", "fares-payments"),
        ("tambang pembayaran", "ms", "fares-payments"),
        ("车费付款", "zh", "fares-payments"),
        ("emergency danger", "en", "safety-incidents"),
        ("kerjasama organisasi", "ms", "business-collaboration-inquiries"),
        ("普通工单优先级", "zh", "support-ticket-response-targets"),
    )
    for query, language, expected_key in checks:
        result = search_knowledge(db_session, query, language)
        assert result.confidence > 0 and result.chunks[0].document_key == expected_key
    assert not search_knowledge(db_session, "quantum submarine bakery", "en").chunks


def test_website_extraction_ignores_scripts_navigation_and_footer() -> None:
    parser = ContentParser()
    parser.feed(
        '<html lang="ms"><head><title>Dasar Tempahan</title><script>secret noise</script></head>'
        '<body><nav><p>Menu noise</p></nav><main><h1>Dasar Tempahan</h1>'
        '<p>Maklumat tempahan yang berguna.</p></main><footer><p>Footer noise</p></footer></body>'
        "</html>"
    )
    assert parser.title == "Dasar Tempahan" and parser.language == "ms"
    assert parser.parts == ["Dasar Tempahan", "Maklumat tempahan yang berguna."]


def test_knowledge_api_requires_named_cco_session_and_csrf(db_session: Session) -> None:
    jane = admin(db_session, "jane", is_cco=True)
    cze = admin(db_session, "czeyik", is_cco=False)
    payload = KnowledgeIngestRequest(
        document_key="api-policy",
        title="API policy",
        source_type="manual",
        source_uri="https://duducar.co/policy",
        language="en",
        chunks=["Approved API knowledge."],
        effective_at=datetime(2020, 1, 1),
        activate=True,
    )
    token = make_session_token(jane.id, jane.auth_version, "csrf")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/knowledge/documents",
            "headers": [(b"cookie", f"dudu_admin_session={token}".encode())],
        }
    )
    with pytest.raises(HTTPException) as denied:
        ingest_document(payload, request, "csrf", db_session, cze)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as bad_csrf:
        ingest_document(payload, request, "wrong", db_session, jane)
    assert bad_csrf.value.status_code == 403
    assert ingest_document(payload, request, "csrf", db_session, jane).status == "active"
