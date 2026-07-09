from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, KnowledgeChunk, KnowledgeDocument
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.retrieval import embed_text


@pytest.fixture()
def db_session(tmp_path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = testing_session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def seeded_db(db_session: Session) -> Session:
    document = KnowledgeDocument(
        title="Fares and payments",
        source_type="test",
        language="en",
        is_approved=True,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        KnowledgeChunk(
            document_id=document.id,
            content=(
                "Fare estimates can change because of distance, traffic, tolls, "
                "waiting time, route changes, or promotions."
            ),
            language="en",
            tags=["fare", "payment", "refund"],
            embedding=embed_text("fare payment refund"),
        )
    )
    db_session.commit()
    return db_session


def test_chat_answers_faq_from_knowledge(seeded_db: Session) -> None:
    service = ChatbotService()
    response = service.handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="rider-1",
            text="Why did my fare change?",
            user_role="rider",
        ),
    )

    assert response.ticket is None
    assert response.confidence > 0
    assert "fare" in response.answer.lower()
    assert response.sources == ["Fares and payments"]


def test_complaint_with_consent_creates_ticket(seeded_db: Session) -> None:
    service = ChatbotService()
    response = service.handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="rider-2",
            text="I want to complain because the driver was rude.",
            user_role="rider",
            name="Demo Rider",
            email="demo@example.com",
            account_id="DUDU123",
            consent_to_ticket=True,
        ),
    )

    assert response.ticket is not None
    assert response.ticket.public_id.startswith("DUDU-")
    assert response.ticket.issue_type == "complaint"
    assert response.ticket.urgency == "normal"

