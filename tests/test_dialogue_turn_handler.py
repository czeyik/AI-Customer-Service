import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import (
    AuditLog,
    Base,
    Conversation,
    Message,
    Ticket,
    WhatsAppInboundMessage,
    WhatsAppOutboundMessage,
)
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.dialogue import DialogueRunResult, load_dialogue_data
from app.services.inbound import process_next_inbound
from app.services.ticket_drafts import (
    ConsentEvidence,
    DialogueData,
    DraftFields,
    make_prompt,
    new_draft,
    prepare_ticket_review,
    request_ticket_submission,
)


@pytest.fixture()
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture()
def db(sessions):
    with sessions() as value:
        yield value


def service() -> ChatbotService:
    return ChatbotService(settings=Settings(_env_file=None, llm_enabled=False))


def send(bot, db, text, *, channel="whatsapp", user="+60123456789", **values):
    return bot.handle(
        db,
        ChatRequest(channel=channel, external_user_id=user, text=text, **values),
    )


def test_exact_consent_token_is_a_local_terminal_and_skips_retrieval(db, monkeypatch):
    events = []
    bot = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    )
    initial = send(bot, db, "I need a human")
    assert initial.needs_ticket_consent
    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("exact consent reached the agent"),
    )
    monkeypatch.setattr(
        "app.services.chatbot.search_knowledge",
        lambda *_args, **_kwargs: pytest.fail("local terminal performed retrieval"),
    )

    response = send(bot, db, "Yes")

    assert response.prompt_id != initial.prompt_id
    assert events[-1]["local_control"] == "consent"
    assert events[-1]["agent_executions"] == 0


def test_standalone_email_is_a_local_terminal_but_labeled_correction_uses_agent(
    db, monkeypatch
):
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
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
    dialogue = make_prompt(DialogueData(draft=draft), "field", "email-turn", field="email")
    db.add(
        Conversation(
            channel="web",
            external_user_id="dedicated-email",
            preferred_language="en",
            dialogue_data=dialogue.model_dump(mode="json"),
        )
    )
    db.commit()
    events = []
    bot = ChatbotService(
        model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
    )
    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("standalone email reached the agent"),
    )
    monkeypatch.setattr(
        "app.services.chatbot.search_knowledge",
        lambda *_args, **_kwargs: pytest.fail("local terminal performed retrieval"),
    )

    response = send(
        bot,
        db,
        "alex@example.com",
        channel="web",
        user="dedicated-email",
        prompt_id=dialogue.pending_prompt.id,
    )

    stored = load_dialogue_data(
        db.query(Conversation).filter_by(external_user_id="dedicated-email").one()
    )
    assert stored.draft.fields.email == "alex@example.com"
    assert response.prompt_id != dialogue.pending_prompt.id
    assert events[-1]["local_control"] == "prompted_field"
    assert events[-1]["agent_executions"] == 0

    called = []

    def agent(_request, _context, working, _references, **_kwargs):
        called.append(True)
        return DialogueRunResult(answer="I will update that.", dialogue=working)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    monkeypatch.setattr(
        "app.services.chatbot.search_knowledge",
        lambda *_args, **_kwargs: SimpleNamespace(chunks=[], confidence=0.0),
    )
    send(
        bot,
        db,
        "Email is corrected@example.com",
        channel="web",
        user="dedicated-email",
        prompt_id=stored.pending_prompt.id,
    )
    assert called == [True]


def complete_review() -> DialogueData:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
    )
    draft.issue_collected = True
    draft.review_required = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    reviewed = prepare_ticket_review(DialogueData(draft=draft), originating_turn="review-turn")
    assert reviewed.status == "prepared"
    return reviewed.dialogue


def test_outage_intake_commits_one_ticket_and_typed_receipt(db):
    bot = service()
    send(bot, db, "I need a human")
    conversation = db.query(Conversation).one()
    dialogue = load_dialogue_data(conversation)
    dialogue.draft.evidence_group = "submission-evidence"
    conversation.dialogue_data = dialogue.model_dump(mode="json")
    db.commit()

    for text in ("Yes", "Alex Tan", "alex@example.com", "My ride receipt is missing"):
        send(bot, db, text)

    conversation = db.query(Conversation).one()
    revision_before_submission = conversation.dialogue_revision
    response = send(bot, db, "Done")

    assert response.ticket is not None
    assert db.query(Ticket).count() == 1
    assert db.query(Message).count() == 12
    conversation = db.query(Conversation).one()
    dialogue = load_dialogue_data(conversation)
    assert dialogue.draft.status == "submitted"
    assert dialogue.draft.evidence_group is None
    assert dialogue.evidence_group is None
    assert conversation.dialogue_revision > revision_before_submission
    assert dialogue.last_receipt.case_reference == response.ticket.public_id
    assert conversation.intake_state == "idle" and conversation.intake_data == {}

    replay = send(bot, db, "Done")
    assert replay.ticket is None
    assert response.ticket.public_id in replay.answer
    assert db.query(Ticket).count() == 1


def test_web_consent_requires_the_current_prompt_id(db):
    bot = service()
    first = send(
        bot,
        db,
        "I want to complain because the driver was rude",
        channel="web",
        user="web-user",
    )
    rejected = send(bot, db, "Yes", channel="web", user="web-user")
    assert rejected.prompt_id == first.prompt_id
    assert load_dialogue_data(db.query(Conversation).one()).draft.consent is None

    accepted = send(
        bot,
        db,
        "Yes",
        channel="web",
        user="web-user",
        prompt_id=first.prompt_id,
    )
    assert accepted.prompt_id != first.prompt_id
    assert load_dialogue_data(db.query(Conversation).one()).draft.consent is not None


def test_web_prompt_control_can_complete_valid_supplied_details(db):
    bot = service()
    consent = send(
        bot,
        db,
        "I want to complain because the driver was rude",
        channel="web",
        user="api-complete",
    )
    completed = send(
        bot,
        db,
        "The driver was rude during my ride",
        channel="web",
        user="api-complete",
        prompt_id=consent.prompt_id,
        consent_to_ticket=True,
        create_ticket=True,
        name="API User",
        email="api@example.com",
        phone_number="+60123456789",
        ride_details="Trip was this morning",
    )

    assert completed.ticket is not None
    assert db.query(Ticket).one().email == "api@example.com"


def test_outage_pause_resume_and_withdrawal_preserve_then_cancel_draft(db):
    bot = service()
    send(bot, db, "I need a human")
    send(bot, db, "Yes")
    send(bot, db, "Alex")
    paused = send(bot, db, "Let us pause and continue later")
    dialogue = load_dialogue_data(db.query(Conversation).one())
    assert "paused" in paused.answer and dialogue.draft.status == "paused"
    send(bot, db, "Continue where we stopped")
    dialogue = load_dialogue_data(db.query(Conversation).one())
    assert dialogue.draft.status == "active" and dialogue.draft.fields.name == "Alex"
    send(bot, db, "I decline")
    assert load_dialogue_data(db.query(Conversation).one()).draft.status == "cancelled"


def test_staged_ticket_rolls_back_if_atomic_outbound_write_fails(db, monkeypatch):
    dialogue = complete_review()
    conversation = Conversation(
        channel="web",
        external_user_id="atomic-user",
        preferred_language="en",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    db.add(conversation)
    db.commit()
    prompt = dialogue.pending_prompt
    prepared = request_ticket_submission(
        dialogue,
        prompt_id=prompt.id,
        originating_turn=prompt.originating_turn,
        customer_input="Submit",
        language="en",
    )

    def agent(*args, **kwargs):
        return DialogueRunResult(
            answer="prepared",
            dialogue=prepared.dialogue,
            ticket_submission=prepared.operation,
        )

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", agent)
    monkeypatch.setattr(
        ChatbotService,
        "_store_outbound",
        lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic write fault")),
    )
    bot = ChatbotService(model=object(), settings=Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="synthetic write fault"):
        send(
            bot,
            db,
            "Submit",
            channel="web",
            user="atomic-user",
            prompt_id=prompt.id,
        )

    assert db.query(Ticket).count() == 0
    assert db.query(AuditLog).count() == 0
    assert db.query(Message).count() == 0


def test_submit_control_is_local_before_the_agent(db, monkeypatch):
    dialogue = complete_review()
    conversation = Conversation(
        channel="web",
        external_user_id="ignored-submit",
        preferred_language="en",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    db.add(conversation)
    db.commit()

    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *_args, **_kwargs: pytest.fail("submit control reached the agent"),
    )
    prompt = dialogue.pending_prompt
    events = []
    response = send(
        ChatbotService(
            model=object(), settings=Settings(_env_file=None), metrics_sink=events.append
        ),
        db,
        "Submit",
        channel="web",
        user="ignored-submit",
        prompt_id=prompt.id,
    )

    assert response.ticket is not None
    assert db.query(Ticket).count() == 1
    assert events[0]["local_control"] == "submit"
    assert events[0]["agent_executions"] == 0


def test_withdrawn_source_discards_staged_mutation_but_keeps_turn(db, monkeypatch):
    dialogue = complete_review()
    conversation = Conversation(
        channel="web",
        external_user_id="source-user",
        preferred_language="en",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    db.add(conversation)
    db.commit()
    prompt = dialogue.pending_prompt
    prepared = request_ticket_submission(
        dialogue,
        prompt_id=prompt.id,
        originating_turn=prompt.originating_turn,
        customer_input="Submit",
        language="en",
    )

    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *args, **kwargs: DialogueRunResult(
            answer="prepared",
            cited_sources=[{"document_key": "removed", "version": 1, "language": "en"}],
            dialogue=prepared.dialogue,
            ticket_submission=prepared.operation,
        ),
    )
    response = send(
        ChatbotService(model=object(), settings=Settings(_env_file=None)),
        db,
        "Could you submit it?",
        channel="web",
        user="source-user",
        prompt_id=prompt.id,
    )

    assert response.ticket is None
    assert db.query(Ticket).count() == 0
    assert db.query(Message).count() == 2
    assert load_dialogue_data(db.query(Conversation).one()).pending_prompt.id == prompt.id


def test_inbound_retry_before_dispatch_remains_provider_eligible(db, monkeypatch):
    event = WhatsAppInboundMessage(
        provider_message_id="wamid.retry",
        sender="60123456789",
        phone_number_id="test",
        message_type="text",
        payload={"text": "I need a human", "timestamp": "100"},
    )
    db.add(event)
    db.commit()
    real = service()

    class FailOnce:
        calls = []

        def handle(self, *args, provider_allowed=True, **kwargs):
            self.calls.append(provider_allowed)
            if len(self.calls) == 1:
                raise RuntimeError("synthetic crash")
            return real.handle(*args, provider_allowed=provider_allowed, **kwargs)

    replacement = FailOnce()
    monkeypatch.setattr("app.services.inbound.chatbot_service", replacement)
    assert process_next_inbound(db)
    db.refresh(event)
    assert event.status == "processing"
    assert "agent_attempted" not in event.payload
    event.lease_until = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert process_next_inbound(db)
    db.refresh(event)
    assert replacement.calls == [True, True]
    assert event.status == "done"


@pytest.mark.parametrize("clock_values", [(101.0,), (99.0, 101.0)])
def test_dispatch_fence_clears_known_predispatch_timeout(db, monkeypatch, clock_values):
    event = WhatsAppInboundMessage(
        provider_message_id="wamid.fence-timeout",
        sender="60123456789",
        phone_number_id="test",
        message_type="text",
        payload={"text": "Hello"},
        status="processing",
        claim_token="fence-token",
        lease_until=datetime.utcnow() + timedelta(seconds=90),
    )
    db.add(event)
    db.commit()
    clock = iter(clock_values)
    monkeypatch.setattr("app.services.chatbot.monotonic", lambda: next(clock))
    fence = service()._before_first_model_dispatch(db, event.id, event.claim_token, event.sender)
    with pytest.raises(TimeoutError, match="agent_deadline_exceeded"):
        fence(100.0)
    db.refresh(event)
    assert "agent_attempted" not in event.payload


def test_inbound_retry_after_dispatch_uses_fallback(db, monkeypatch):
    event = WhatsAppInboundMessage(
        provider_message_id="wamid.dispatch-crash",
        sender="60123456789",
        phone_number_id="test",
        message_type="text",
        payload={"text": "Hello", "timestamp": "100"},
    )
    db.add(event)
    db.commit()
    real = service()

    class CrashAfterDispatch:
        calls = []

        def handle(self, session, request, *, provider_allowed=True, **kwargs):
            self.calls.append(provider_allowed)
            if len(self.calls) == 1:
                real._before_first_model_dispatch(
                    session, kwargs["inbound_id"], kwargs["claim_token"], request.external_user_id
                )(time.monotonic() + 60)
                raise RuntimeError("synthetic crash after dispatch")
            return real.handle(session, request, provider_allowed=provider_allowed, **kwargs)

    replacement = CrashAfterDispatch()
    monkeypatch.setattr("app.services.inbound.chatbot_service", replacement)
    assert process_next_inbound(db)
    db.refresh(event)
    assert event.payload["agent_attempted"] is True
    event.lease_until = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert process_next_inbound(db)
    db.refresh(event)
    assert replacement.calls == [True, False]
    assert event.status == "done"
    assert db.query(WhatsAppOutboundMessage).count() == 1


def test_inbound_lease_survives_sixty_second_agent_window(db, monkeypatch):
    event = WhatsAppInboundMessage(
        provider_message_id="wamid.slow-success",
        sender="60123456789",
        phone_number_id="test",
        message_type="text",
        payload={"text": "Hello", "timestamp": "100"},
    )
    db.add(event)
    db.commit()
    monkeypatch.setattr("app.services.inbound.chatbot_service", service())

    claimed_at = datetime.utcnow() - timedelta(seconds=60, milliseconds=1)
    assert process_next_inbound(db, now=claimed_at)

    db.refresh(event)
    assert event.status == "done"
    assert db.query(WhatsAppOutboundMessage).count() == 1


def test_expired_inbound_lease_cannot_write_a_reply(db):
    event = WhatsAppInboundMessage(
        provider_message_id="wamid.expired",
        sender="60123456789",
        phone_number_id="test",
        message_type="text",
        payload={"text": "Hello", "timestamp": "100"},
        status="processing",
        claim_token="expired-token",
        lease_until=datetime.utcnow() - timedelta(seconds=1),
    )
    db.add(event)
    db.commit()

    with pytest.raises(ValueError, match="lease lost"):
        service().handle(
            db,
            ChatRequest(channel="whatsapp", external_user_id=event.sender, text="Hello"),
            inbound_id=event.id,
            claim_token="expired-token",
        )
    assert db.query(Message).count() == 0
    assert db.query(WhatsAppOutboundMessage).count() == 0


def test_outage_case_update_requires_owned_case_and_confirmation(db):
    owned = Ticket(
        public_id="DUDU-20260930-ABCDE",
        status="closed",
        closed_at=datetime.utcnow(),
        urgency="normal",
        channel="web",
        external_user_id="case-user",
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
        issue_type="complaint",
        language="en",
        description="Missing receipt",
        consent_given=True,
        extra={"customer_updates": []},
    )
    db.add(owned)
    db.commit()
    bot = service()
    prompt = send(
        bot,
        db,
        "Please add to DUDU-20260930-ABCDE that the receipt is still missing",
        channel="web",
        user="case-user",
    )
    assert owned.status == "closed" and prompt.prompt_id
    receipt = send(
        bot,
        db,
        "Yes",
        channel="web",
        user="case-user",
        prompt_id=prompt.prompt_id,
    )
    db.refresh(owned)
    assert "recorded" in receipt.answer
    assert owned.status == "open"
    assert len(owned.extra["customer_updates"]) == 1

    missing = send(
        bot,
        db,
        "Please add to DUDU-20260930-ZZZZZ that this is not mine",
        channel="web",
        user="case-user",
    )
    assert missing.prompt_id != prompt.prompt_id
    assert db.query(Ticket).count() == 1


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL concurrency integration")
def test_postgres_agent_wait_does_not_hold_the_conversation_lock(monkeypatch):
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    customer = f"wave5-{uuid.uuid4().hex}"
    barrier = threading.Barrier(4)

    def waiting_agent(request, context, dialogue, *args, **kwargs):
        barrier.wait(timeout=5)
        time.sleep(0.1)
        return DialogueRunResult(answer="Which service do you mean?", dialogue=dialogue)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", waiting_agent)

    def turn(index):
        with sessions() as turn_db:
            started = time.monotonic()
            ChatbotService(model=object(), settings=Settings(_env_file=None)).handle(
                turn_db,
                ChatRequest(
                    channel="whatsapp",
                    external_user_id=customer,
                    text=f"Question number {index}",
                ),
            )
            return time.monotonic() - started

    with ThreadPoolExecutor(max_workers=4) as pool:
        elapsed = list(pool.map(turn, range(4)))
    with sessions() as check_db:
        conversation = check_db.query(Conversation).filter_by(external_user_id=customer).one()
        assert check_db.query(Message).filter_by(conversation_id=conversation.id, direction="inbound").count() == 4
    assert max(elapsed) < 5


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL fault integration")
def test_postgres_fault_rolls_back_ticket_audit_messages_and_outbox(monkeypatch):
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    customer = f"atomic-{uuid.uuid4().hex}"
    dialogue = complete_review()
    prompt = dialogue.pending_prompt
    prepared = request_ticket_submission(
        dialogue,
        prompt_id=prompt.id,
        originating_turn=prompt.originating_turn,
        customer_input="Submit",
        language="en",
    )
    with sessions() as db:
        db.add(
            Conversation(
                channel="web",
                external_user_id=customer,
                preferred_language="en",
                dialogue_data=dialogue.model_dump(mode="json"),
            )
        )
        db.commit()

    monkeypatch.setattr(
        "app.services.chatbot.run_dialogue_agent",
        lambda *args, **kwargs: DialogueRunResult(
            answer="prepared",
            dialogue=prepared.dialogue,
            ticket_submission=prepared.operation,
        ),
    )
    monkeypatch.setattr(
        ChatbotService,
        "_store_outbound",
        lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic postgres fault")),
    )
    with sessions() as db:
        with pytest.raises(RuntimeError, match="synthetic postgres fault"):
            send(
                ChatbotService(model=object(), settings=Settings(_env_file=None)),
                db,
                "Submit",
                channel="web",
                user=customer,
                prompt_id=prompt.id,
            )
    with sessions() as db:
        conversation = db.query(Conversation).filter_by(external_user_id=customer).one()
        assert db.query(Ticket).filter_by(external_user_id=customer).count() == 0
        assert db.query(Message).filter_by(conversation_id=conversation.id).count() == 0
