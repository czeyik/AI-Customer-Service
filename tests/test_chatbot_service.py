from test_ticket_intake import handle_with_controls
from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Conversation, KnowledgeChunk, KnowledgeDocument, Message
from app.config import Settings
from app.schemas import ChatRequest
from app.services.dialogue import DialogueRunResult, load_dialogue_data
from app.services.chatbot import ChatbotService
from app.services.ticket_drafts import (
    ConsentEvidence,
    DialogueData,
    DraftFields,
    make_prompt,
    new_draft,
    update_ticket_draft,
)


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
        document_key="fares-payments",
        version=1,
        title="Fares and payments",
        source_type="test",
        source_uri="https://duducar.co/test-fares",
        language="en",
        status="active",
        effective_at=datetime(2020, 1, 1),
        content_hash="test-fares-v1",
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


def test_customer_identifiers_never_reach_agent_payload(seeded_db: Session, caplog, monkeypatch) -> None:
    captured = {}

    def agent(_request, context, dialogue, references, **kwargs):
        captured["context"] = context.model_dump()
        captured["references"] = [item.model_dump() for item in references.public]
        chunk = kwargs["initial_chunks"][0]
        return DialogueRunResult(
            answer=chunk.content,
            source_titles=[chunk.source_title],
            cited_sources=[{
                "document_key": chunk.document_key,
                "version": chunk.version,
                "language": chunk.language,
            }],
            confidence=chunk.score,
            dialogue=dialogue,
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    service = ChatbotService(model=object(), settings=Settings(_env_file=None))

    response = service.handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="private-user-id",
            text="I am Jane, jane@example.com, +60123456789. Why did my fare change?",
            user_role="rider",
        ),
    )

    sent = str(captured)
    assert response.confidence > 0
    assert "Why did my fare change?" in sent
    assert "Jane" not in sent
    assert "jane@example.com" not in sent
    assert "+60123456789" not in sent
    assert "private-user-id" not in sent
    assert "Jane" not in caplog.text
    assert "jane@example.com" not in caplog.text
    assert "+60123456789" not in caplog.text
    assert "private-user-id" not in caplog.text


@pytest.mark.parametrize("agent_fails", [False, True], ids=["provider-disabled", "agent-failure"])
def test_history_reference_fallback_does_not_use_literal_retrieval_or_mutate_draft(
    seeded_db: Session, monkeypatch, agent_fails: bool
) -> None:
    document = KnowledgeDocument(
        document_key="unrelated-ms",
        version=1,
        title="Unrelated Malay topic",
        source_type="test",
        language="ms",
        status="active",
        effective_at=datetime(2020, 1, 1),
        content_hash="unrelated-ms-v1",
    )
    document.chunks.append(
        KnowledgeChunk(
            content="Maklumat pertama mengenai keselamatan.",
            language="ms",
            tags=[],
        )
    )
    dialogue = make_prompt(
        DialogueData(draft=new_draft(expiry_minutes=60)), "consent", "turn-0"
    )
    conversation = Conversation(
        channel="whatsapp",
        external_user_id=f"history-fallback-{agent_fails}",
        preferred_language="ms",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    seeded_db.add_all([document, conversation])
    seeded_db.flush()
    seeded_db.add_all(
        [
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                content="Bagaimana saya tempah perjalanan?",
                language="ms",
            ),
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                content="Tempah perjalanan melalui aplikasi DUDU Car.",
                language="ms",
            ),
        ]
    )
    seeded_db.commit()

    if agent_fails:
        monkeypatch.setattr(
            "app.services.chatbot.run_dialogue_agent",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("agent timeout")),
        )
    else:
        monkeypatch.setattr(
            "app.services.chatbot.run_dialogue_agent",
            lambda *_args, **_kwargs: pytest.fail("provider-disabled turn reached the agent"),
        )

    response = ChatbotService(model=object(), settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id=conversation.external_user_id,
            text="Sila jawab soalan pertama saya semula.",
            preferred_language="ms",
        ),
        provider_allowed=agent_fails,
    )

    stored = load_dialogue_data(conversation)
    assert response.answer == "Boleh jelaskan perkhidmatan atau soalan DUDU Car yang anda maksudkan?"
    assert response.sources == []
    assert stored == dialogue
    assert stored.draft.status == "active"
    assert stored.pending_prompt.id == dialogue.pending_prompt.id
    assert stored.pending_offer is None


def test_safety_control_wins_over_history_reference_during_outage(
    seeded_db: Session,
) -> None:
    conversation = Conversation(
        channel="whatsapp",
        external_user_id="safety-history-reference",
        preferred_language="en",
    )
    seeded_db.add(conversation)
    seeded_db.flush()
    seeded_db.add_all(
        [
            Message(
                conversation_id=conversation.id,
                direction="inbound",
                content="How do I book a ride?",
                language="en",
            ),
            Message(
                conversation_id=conversation.id,
                direction="outbound",
                content="Use the DUDU Car app to request a ride.",
                language="en",
            ),
        ]
    )
    seeded_db.commit()

    response = ChatbotService(model=object(), settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id=conversation.external_user_id,
            text="Someone is injured. Please answer the first question again.",
            preferred_language="en",
        ),
        provider_allowed=False,
    )

    stored = load_dialogue_data(conversation)
    assert "999" in response.answer
    assert response.needs_ticket_consent
    assert stored.draft.status == "active"
    assert stored.draft.issue_type == "safety_incident"
    assert stored.draft.priority == "urgent"
    assert stored.pending_prompt.purpose == "consent"


def test_mandatory_intake_is_a_local_control(
    seeded_db: Session, monkeypatch
) -> None:
    events = []

    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("mandatory handoff reached the agent"),
    )
    service = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    )

    response = service.handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="mandatory-intake",
            text="I need a human",
            user_role="rider",
        ),
    )

    assert response.needs_ticket_consent
    assert response.prompt_id
    assert events[0]["fallbacks"] == 1
    assert events[0]["local_control"] == "mandatory_handoff"
    assert events[0]["agent_executions"] == 0
    assert events[0]["agent_failure_fallback"] == 0


def test_current_contact_consent_is_prepared_by_local_terminal(
    seeded_db: Session, monkeypatch
) -> None:
    initial = ChatbotService(settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="contact-fallback",
            text="I need a human",
            user_role="rider",
        ),
    )
    assert initial.needs_ticket_consent

    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("exact consent reached the agent"),
    )
    events = []
    response = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="contact-fallback",
            text="Yes",
            phone_number="+60123456789",
            user_role="rider",
        ),
    )

    assert response.prompt_id != initial.prompt_id
    dialogue = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="contact-fallback").one()
    )
    assert dialogue.draft.fields.phone_number == "+60123456789"
    assert events[0]["local_control"] == "consent"
    assert events[0]["agent_executions"] == 0
    assert events[0]["agent_failure_fallback"] == 0


def test_prepared_consent_survives_local_terminal_fallback(
    seeded_db: Session, monkeypatch
) -> None:
    initial = ChatbotService(settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="prepared-fallback",
            text="I need a human",
        ),
    )
    events = []
    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("exact consent reached the agent"),
    )

    response = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="prepared-fallback",
            text="Yes",
            phone_number="+60123456789",
        ),
    )

    dialogue = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="prepared-fallback").one()
    )
    assert response.prompt_id != initial.prompt_id
    assert dialogue.draft.consent is not None
    assert dialogue.draft.fields.phone_number == "+60123456789"
    assert events[0]["local_control"] == "consent"
    assert events[0]["agent_executions"] == 0
    assert events[0]["agent_failure_fallback"] == 0


def test_web_wrong_prompt_id_does_not_apply_a_local_contact_update(seeded_db: Session) -> None:
    initial = ChatbotService(settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="wrong-prompt",
            text="I need a human",
        ),
    )

    response = ChatbotService(settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="wrong-prompt",
            text="Yes",
            phone_number="+60123456789",
            prompt_id="wrong-prompt-id",
        ),
    )

    dialogue = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="wrong-prompt").one()
    )
    assert response.prompt_id == initial.prompt_id
    assert dialogue.draft.consent is None
    assert dialogue.draft.fields.phone_number is None


def test_field_update_is_agent_owned_and_preserves_pending_prompt_before_agent(
    seeded_db: Session, monkeypatch
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
    )
    draft.issue_collected = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(DialogueData(draft=draft), "field", "name-turn", field="name")
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="fulfilled-field",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    seen = {}

    def agent(_request, context, working, references, **_kwargs):
        seen["state"] = context.state
        seen["dialogue"] = working
        reference = next(
            item.id
            for item in references.public
            if item.source == "current_input" and item.field == "name"
        )
        updated = update_ticket_draft(
            working, references, {"name": reference}, expiry_minutes=60
        )
        assert updated.status == "prepared"
        return DialogueRunResult(
            answer="You can add any other relevant details.",
            dialogue=make_prompt(updated.dialogue, "details", "agent-turn"),
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    response = ChatbotService(model=object(), settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="fulfilled-field",
            text="Alex",
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    assert seen["state"]["pending_prompt"] == {"purpose": "field", "field": "name"}
    assert "name" not in seen["state"]["draft"]["fields_present"]
    assert seen["dialogue"].draft.fields.name is None
    assert response.prompt_id != dialogue.pending_prompt.id


def test_mixed_name_and_existing_email_correction_stages_email_before_agent(
    seeded_db: Session, monkeypatch
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Original Rider",
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
    )
    draft.issue_collected = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(DialogueData(draft=draft), "review", "review-turn")
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="mixed-contact-correction",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    seen = {}

    def agent(_request, context, working, references, **_kwargs):
        seen["name"] = working.draft.fields.name
        seen["email"] = working.draft.fields.email
        seen["current_input_applied"] = context.state["current_input_applied"]
        name_reference = next(
            item.id
            for item in references.public
            if item.source == "current_input" and item.field == "name"
        )
        updated = update_ticket_draft(
            working, references, {"name": name_reference}, expiry_minutes=60
        )
        assert updated.status == "prepared"
        return DialogueRunResult(
            answer="I have updated your details.",
            dialogue=make_prompt(updated.dialogue, "details", "agent-turn"),
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="mixed-contact-correction",
            text=(
                "My name is Alex and my email is corrected@example.com. "
                "What are human support hours?"
            ),
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation)
        .filter_by(external_user_id="mixed-contact-correction")
        .one()
    )
    assert seen == {
        "name": "Original Rider",
        "email": "corrected@example.com",
        "current_input_applied": True,
    }
    assert stored.draft.fields.name == "Alex"
    assert stored.draft.fields.email == "corrected@example.com"
    assert events[0]["local_control"] == "contact_correction"
    assert events[0]["agent_executions"] == 1
    assert events[0]["agent_failure_fallback"] == 0


def test_completed_field_progress_requires_a_follow_up_prompt(
    seeded_db: Session, monkeypatch
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
    )
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(
        DialogueData(draft=draft), "field", "description-turn", field="description"
    )
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="missing-next-prompt",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    def agent(_request, _context, working, references, **_kwargs):
        reference = next(
            item.id
            for item in references.public
            if item.source == "current_input" and item.field == "description"
        )
        updated = update_ticket_draft(
            working, references, {"description": reference}, expiry_minutes=60
        )
        assert updated.status == "prepared"
        return DialogueRunResult(
            answer="Would you like to add anything else?", dialogue=updated.dialogue
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    response = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="missing-next-prompt",
            text="My ride receipt is missing",
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="missing-next-prompt").one()
    )
    assert stored.draft.fields.description == "My ride receipt is missing"
    assert stored.pending_prompt.purpose == "details"
    assert response.prompt_id == stored.pending_prompt.id
    assert events[0]["agent_error_code"] == "next_prompt_required_after_field_progress"


@pytest.mark.parametrize(
    ("language", "text", "corrected_email"),
    [
        ("en", "What are human support hours?", None),
        ("ms", "Apakah waktu sokongan manusia?", None),
        ("zh", "人工客服的服务时间是什么？", None),
        ("en", "My email is corrected@example.com. What are human support hours?", "corrected@example.com"),
    ],
)
def test_side_question_does_not_preapply_pending_description(
    seeded_db: Session, monkeypatch, language: str, text: str, corrected_email: str | None
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
    )
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(
        DialogueData(draft=draft), "field", "description-turn", field="description"
    )
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id=f"side-question-{language}",
            preferred_language=language,
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    seen = {}

    def agent(_request, context, working, references, **_kwargs):
        seen["description"] = working.draft.fields.description
        seen["current_input_applied"] = context.state["current_input_applied"]
        assert context.current_question != "[FIELD_DESCRIPTION]"
        seen["reference_fields"] = {
            item.field for item in references.public if item.source == "current_input"
        }
        return DialogueRunResult(answer="Human support can help.", dialogue=working)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    response = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id=f"side-question-{language}",
            preferred_language=language,
            text=text,
            prompt_id=dialogue.pending_prompt.id,
        )
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation)
        .filter_by(external_user_id=f"side-question-{language}")
        .one()
    )
    assert seen["description"] is None
    assert seen["current_input_applied"] is bool(corrected_email)
    assert "description" in seen["reference_fields"]
    assert stored.draft.fields.description is None
    assert stored.draft.fields.email == (corrected_email or "alex@example.com")
    assert response.prompt_id == dialogue.pending_prompt.id
    assert events[0]["local_control"] == ("contact_correction" if corrected_email else None)
    assert events[0]["agent_failure_fallback"] == 0
    assert events[0]["agent_error_code"] is None


@pytest.mark.parametrize(
    ("text", "preapplied"),
    [
        ("Why was I charged twice?", False),
        ("Boleh jadi pemandu tersalah jalan.", True),
        ("Jika hujan, pemandu memandu terlalu laju.", True),
        ("如果路上堵车，司机绕路了。", True),
    ],
)
def test_description_defers_questions_but_applies_assertions(
    seeded_db: Session, monkeypatch, text: str, preapplied: bool
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
    )
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(
        DialogueData(draft=draft), "field", "description-turn", field="description"
    )
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="question-issue",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    seen = {}

    def agent(_request, _context, working, references, **_kwargs):
        seen["description"] = working.draft.fields.description
        reference = next(
            item.id
            for item in references.public
            if item.source == "current_input" and item.field == "description"
        )
        updated = update_ticket_draft(
            working, references, {"description": reference}, expiry_minutes=60
        )
        assert updated.status == "prepared"
        return DialogueRunResult(
            answer="Any other details?",
            dialogue=make_prompt(updated.dialogue, "details", "agent-turn"),
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    service = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    )
    service.handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="question-issue",
            text=text,
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="question-issue").one()
    )
    assert seen["description"] is None
    assert stored.draft.fields.description == text
    assert events[0]["local_control"] is None
    assert events[0]["agent_failure_fallback"] == 0
    assert events[0]["agent_error_code"] is None


def test_accumulated_ride_details_count_as_current_field_progress(
    seeded_db: Session, monkeypatch
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
        ride_details="The ride was yesterday",
    )
    draft.issue_collected = True
    draft.ride_details_collected = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(DialogueData(draft=draft), "details", "details-turn")
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="accumulated-details",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()

    def agent(_request, _context, working, _references, **_kwargs):
        updated = working.model_copy(deep=True)
        updated.draft.fields.ride_details += "\nThe receipt is still missing today"
        updated.draft.review_required = True
        return DialogueRunResult(
            answer="Please review the ticket.",
            dialogue=make_prompt(updated, "review", "review-turn"),
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    response = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="accumulated-details",
            text="The receipt is still missing today",
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="accumulated-details").one()
    )
    assert stored.draft.fields.ride_details.endswith("The receipt is still missing today")
    assert response.prompt_id == stored.pending_prompt.id
    assert events[0]["agent_error_code"] is None


def test_hypothetical_email_does_not_become_a_local_contact_correction(
    seeded_db: Session, monkeypatch
) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
    )
    draft.issue_collected = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(DialogueData(draft=draft), "review", "review-turn")
    seeded_db.add(
        Conversation(
            channel="web",
            external_user_id="hypothetical-contact",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    seeded_db.commit()
    seen = {}

    def agent(_request, _context, working, _references, **_kwargs):
        seen["email"] = working.draft.fields.email
        return DialogueRunResult(answer="I can explain what would happen.", dialogue=working)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    events = []
    ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    ).handle(
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="hypothetical-contact",
            text="If I changed my email to hypothetical@example.com, what would happen?",
            prompt_id=dialogue.pending_prompt.id,
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="hypothetical-contact").one()
    )
    assert seen["email"] == "alex@example.com"
    assert stored.draft.fields.email == "alex@example.com"
    assert events[0]["local_control"] is None
    assert events[0]["agent_executions"] == 1


def test_hypothetical_pause_remains_an_agent_side_question(
    seeded_db: Session, monkeypatch
) -> None:
    service = ChatbotService(settings=Settings(_env_file=None))
    service.handle(
        seeded_db,
        ChatRequest(channel="whatsapp", external_user_id="hypothetical-pause", text="I need a human"),
    )
    before = service.handle(
        seeded_db,
        ChatRequest(channel="whatsapp", external_user_id="hypothetical-pause", text="Yes"),
    )
    seen = {}

    def agent(_request, _context, dialogue, _references, **_kwargs):
        seen["status"] = dialogue.draft.status
        return DialogueRunResult(answer="I can explain that.", dialogue=dialogue)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    response = ChatbotService(model=object(), settings=Settings(_env_file=None)).handle(
        seeded_db,
        ChatRequest(
            channel="whatsapp",
            external_user_id="hypothetical-pause",
            text="What happens if I pause and continue later?",
        ),
    )

    stored = load_dialogue_data(
        seeded_db.query(Conversation).filter_by(external_user_id="hypothetical-pause").one()
    )
    assert seen["status"] == "active"
    assert stored.draft.status == "active"
    assert response.prompt_id == before.prompt_id


def test_complaint_with_consent_creates_ticket(seeded_db: Session) -> None:
    service = ChatbotService()
    response = handle_with_controls(service,
        seeded_db,
        ChatRequest(
            channel="web",
            external_user_id="rider-2",
            text="I want to complain because the driver was rude.",
            user_role="rider",
            name="Demo Rider",
            email="demo@example.com",
            phone_number="+60182935060",
            account_id="DUDU123",
            ride_details="Trip DUDU123 on 4 September",
            consent_to_ticket=True,
        ),
    )

    assert response.ticket is not None
    assert response.ticket.public_id.startswith("DUDU-")
    assert response.ticket.issue_type == "complaint"
    assert response.ticket.urgency == "normal"
