import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from test_ticket_intake import db_session
from app.config import Settings
from app.models import Base, Conversation, MediaAttachment, Message, Ticket, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.inbound import process_inbox, process_next_inbound
from app.services.dialogue import DialogueRunResult, load_dialogue_data
from app.services.ticket_drafts import ConsentEvidence, DialogueData, DraftFields, make_prompt, new_draft
from app.services.website_knowledge import ContentParser, extract_page


def send(service, db, text, **kwargs):
    return service.handle(
        db, ChatRequest(external_user_id="synthetic-user", text=text, **kwargs)
    )


def test_website_nested_lists_tables_and_conditions_survive_extraction():
    parser = ContentParser()
    parser.feed('<title>Support</title><main><ul><li>Only <strong>approved</strong> drivers<ul><li>after checks</li></ul>may accept rides.</li></ul><table><tr><th>Condition</th><th>Amount</th></tr><tr><td>If eligible</td><td>RM 5</td></tr></table><p>Read <a href="/terms">terms</a> first.</p></main>')
    output = " ".join(parser.parts)
    assert all(part in output for part in ("Only approved drivers", "after checks", "may accept rides", "If eligible", "RM 5", "https://duducar.co/terms"))


def test_idle_inbox_uses_one_query_without_thread_fanout(db_session, monkeypatch):
    from sqlalchemy import event

    engine = db_session.get_bind()
    statements = []
    listener = lambda *args: statements.append(args[2])
    event.listen(engine, "before_cursor_execute", listener)
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", lambda **_: pytest.fail("idle fan-out"))
    try:
        assert process_inbox(sessionmaker(bind=engine)) == 0
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) == 1


def test_website_requires_allowlist_before_fetch(monkeypatch):
    monkeypatch.setattr("app.services.website_knowledge._read_url", lambda *_: pytest.fail("must not fetch"))
    with pytest.raises(ValueError, match="allowlist"):
        extract_page("https://duducar.co/unreviewed")


def test_evidence_binding_includes_pending_and_approved_but_not_other_cases(db_session):
    service = ChatbotService()
    draft = new_draft(expiry_minutes=60, evidence_group="case-a")
    draft.fields = DraftFields(name="Alex", email="alex@example.com", phone_number="+60123456789", description="My ride receipt is missing")
    draft.issue_collected = True
    draft.review_required = True
    draft.consent = ConsentEvidence(prompt_id="consent", draft_id=draft.id, draft_version=draft.version, originating_turn="consent-turn", customer_input="Yes", recorded_at=datetime.utcnow())
    dialogue = make_prompt(DialogueData(draft=draft), "review", "review-turn")
    conversation = Conversation(channel="whatsapp", external_user_id="synthetic", preferred_language="en", user_role="rider", dialogue_data=dialogue.model_dump(mode="json"))
    db_session.add(conversation)
    db_session.flush()
    for label, status, group in (("before", "approved", "case-a"), ("during", "queued", "case-a"), ("rejected", "rejected", "case-a"), ("other", "approved", "case-b")):
        db_session.add(MediaAttachment(provider_media_id=label, conversation_id=conversation.id, evidence_group=group, media_type="image", declared_mime_type="image/png", status=status))
    db_session.flush()
    response = service.handle(db_session, ChatRequest(channel="whatsapp", external_user_id="synthetic", text="Submit"))
    assert response.ticket is not None
    ticket = db_session.query(Ticket).one()
    assert ticket.attachment_count == 1
    media = {a.provider_media_id: a for a in db_session.query(MediaAttachment)}
    assert media["before"].ticket_id == media["during"].ticket_id == ticket.id
    assert media["rejected"].ticket_id is None and media["other"].ticket_id is None


def test_stale_whatsapp_yes_does_not_record_consent(db_session, monkeypatch):
    monkeypatch.setattr("app.services.inbound.chatbot_service", ChatbotService())
    def inbound(identifier, body, timestamp, quoted=""):
        db_session.add(WhatsAppInboundMessage(provider_message_id=identifier, sender="60123456789", phone_number_id="test", message_type="text", payload={"text": body, "timestamp": timestamp, "context_message_id": quoted}))
        db_session.commit()
        assert process_next_inbound(db_session)
    inbound("first", "I need a human", "100")
    inbound("stale", "Yes", "90", "unrelated-message")
    conversation = db_session.query(Conversation).one()
    dialogue = load_dialogue_data(conversation)
    assert dialogue.pending_prompt.purpose == "consent"
    assert dialogue.draft.consent is None
    assert db_session.query(WhatsAppOutboundMessage).count() == 2


def test_only_confirmed_owned_case_reopens(db_session):
    ticket = Ticket(public_id="DUDU-20260910-ABCDE", channel="web", external_user_id="synthetic-user", name="Alex", email="alex@example.com", phone_number="+60123456789", issue_type="complaint", description="Missing receipt", consent_given=True, status="closed", closed_at=datetime.utcnow())
    db_session.add(ticket)
    db_session.commit()
    service = ChatbotService()
    send(service, db_session, "Hello")
    assert ticket.status == "closed"
    confirmation = send(service, db_session, "Please add more information to DUDU-20260910-ABCDE: I still need the receipt")
    assert ticket.status == "closed"
    send(service, db_session, "Yes", prompt_id=confirmation.prompt_id)
    db_session.refresh(ticket)
    assert ticket.status == "open" and len(ticket.extra["customer_updates"]) == 1
    assert db_session.query(Ticket).count() == 1


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL concurrency integration")
def test_agent_burst_does_not_hold_conversation_locks_or_duplicate_conversations(monkeypatch):
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    sessions = sessionmaker(bind=engine, autoflush=False)
    user = "burst-" + uuid.uuid4().hex
    barrier = threading.Barrier(8)
    def waiting_agent(request, context, dialogue, *args, **kwargs):
        barrier.wait(timeout=5)
        time.sleep(.1)
        return DialogueRunResult(answer="Which service do you mean?", dialogue=dialogue)

    monkeypatch.setattr("app.services.chatbot.run_dialogue_agent", waiting_agent)

    def turn(index):
        service = ChatbotService(model=object(), settings=Settings(_env_file=None))
        with sessions() as db:
            started = time.monotonic()
            response = service.handle(db, ChatRequest(channel="whatsapp", external_user_id=user, text="Which services are available?"))
            assert response.ticket is None
            return time.monotonic() - started
    with ThreadPoolExecutor(max_workers=8) as pool:
        latencies = list(pool.map(turn, range(8)))
    with sessions() as db:
        conversation = db.query(Conversation).filter_by(external_user_id=user).one()
        assert db.query(Message).filter_by(conversation_id=conversation.id, direction="inbound").count() == 8
    assert max(latencies) < 5


def test_website_draft_can_receive_effective_date_on_cco_activation(db_session):
    from app.models import AdminUser
    from app.services.knowledge import ingest_knowledge, activate_knowledge
    from app.services.retrieval import search_knowledge
    cco = AdminUser(username="synthetic-cco", display_name="Synthetic fixture", email="cco@example.invalid", password_hash="unused", totp_secret_ref="unused", is_cco=True)
    db_session.add(cco)
    db_session.commit()
    document = ingest_knowledge(db_session, actor=cco, document_key="website-example", title="Website booking", source_type="website", source_uri="https://duducar.co/example", language="en", chunks=["Confirm pickup before requesting a ride."], tags=["booking"])
    assert document.effective_at is None
    assert not search_knowledge(db_session, "booking", "en").chunks
    activate_knowledge(db_session, actor=cco, document=document, effective_at=datetime(2020,1,1))
    assert search_knowledge(db_session, "booking", "ms").chunks[0].language == "en"
    same = ingest_knowledge(db_session, actor=cco, document_key="website-example", title="Website booking", source_type="website", source_uri="https://duducar.co/example", language="en", chunks=["Confirm pickup before requesting a ride."], tags=["booking"])
    assert same.id == document.id
