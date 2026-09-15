from datetime import datetime, timedelta

import pytest

from test_ticket_intake import db_session
from app.models import Conversation, Ticket
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.dialogue import load_dialogue_data
from app.services.pii import provider_question


def send(service, db, text, **kwargs):
    return service.handle(
        db,
        ChatRequest(
            channel="whatsapp",
            external_user_id=kwargs.pop("external_user_id", "60123456789"),
            text=text,
            **kwargs,
        ),
    )


@pytest.mark.parametrize(
    "text", ["Are you human?", "Are you AI?", "你是人工吗？", "Adakah anda manusia?"]
)
def test_identity_does_not_offer_ticket(db_session, text):
    response = send(ChatbotService(), db_session, text)
    dialogue = load_dialogue_data(db_session.query(Conversation).one())
    assert response.ticket is None and not response.needs_ticket_consent
    assert dialogue.draft is None


def test_fields_any_order_and_corrections_remain_local(db_session):
    service = ChatbotService()
    for text in (
        "I need a human",
        "Yes",
        "My name is Alex, alex@example.com, +60198765432",
        "The receipt for my ride is missing",
        "My email is corrected@example.com",
        "Skip",
    ):
        response = send(service, db_session, text)

    dialogue = load_dialogue_data(db_session.query(Conversation).one())
    assert dialogue.draft.fields.name == "Alex"
    assert dialogue.draft.fields.email == "corrected@example.com"
    assert dialogue.draft.fields.phone_number == "+60198765432"
    assert dialogue.draft.fields.description == "The receipt for my ride is missing"
    if response.ticket is None:
        response = send(service, db_session, "Submit")
    assert response.ticket is not None
    assert db_session.query(Ticket).one().email == "corrected@example.com"


def test_expired_draft_is_cancelled_without_mutation(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    conversation = db_session.query(Conversation).one()
    dialogue = load_dialogue_data(conversation)
    dialogue.draft.expires_at = datetime.utcnow() - timedelta(seconds=1)
    conversation.dialogue_data = dialogue.model_dump(mode="json")
    db_session.commit()

    send(service, db_session, "Hello")
    db_session.refresh(conversation)
    assert load_dialogue_data(conversation).draft.status == "cancelled"
    assert db_session.query(Ticket).count() == 0


def test_unbound_and_stale_api_flags_do_not_create_ticket(db_session):
    service = ChatbotService()
    service.handle(
        db_session,
        ChatRequest(
            external_user_id="web-user",
            text="Hello",
            create_ticket=True,
            consent_to_ticket=True,
        ),
    )
    prompt = service.handle(
        db_session, ChatRequest(external_user_id="web-user", text="I need a human")
    )
    service.handle(
        db_session,
        ChatRequest(
            external_user_id="web-user",
            text="Yes",
            create_ticket=True,
            consent_to_ticket=True,
            prompt_id="stale",
        ),
    )
    conversation = db_session.query(Conversation).filter_by(external_user_id="web-user").one()
    assert load_dialogue_data(conversation).draft.consent is None

    service.handle(
        db_session,
        ChatRequest(
            external_user_id="web-user",
            text="Yes",
            consent_to_ticket=True,
            prompt_id=prompt.prompt_id,
        ),
    )
    assert load_dialogue_data(conversation).draft.consent
    assert db_session.query(Ticket).count() == 0


def test_refund_request_keeps_financial_priority_and_action_boundary(db_session):
    response = send(ChatbotService(), db_session, "Refund me")
    dialogue = load_dialogue_data(db_session.query(Conversation).one())
    assert "can’t perform refunds" in response.answer
    assert dialogue.draft.priority == "high"
    assert "account_action_blocked" in response.safety_flags
    assert response.ticket is None


def test_submission_controls_cannot_become_missing_name(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes")
    for control in ("Skip", "Done", "Submit"):
        response = send(service, db_session, control)
        dialogue = load_dialogue_data(db_session.query(Conversation).one())
        assert response.ticket is None
        assert dialogue.pending_prompt.field == "name"
        assert dialogue.draft.fields.name is None
        assert db_session.query(Ticket).count() == 0


def test_pause_and_resume_preserve_supplied_fields(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes", name="Alex", email="alex@example.com")
    send(service, db_session, "Let us pick this up later")
    dialogue = load_dialogue_data(db_session.query(Conversation).one())
    assert dialogue.draft.status == "paused"
    assert dialogue.draft.fields.email == "alex@example.com"
    send(service, db_session, "Continue where we stopped")
    dialogue = load_dialogue_data(db_session.query(Conversation).one())
    assert dialogue.draft.status == "active"
    assert dialogue.draft.fields.name == "Alex"


def test_uncertain_identity_prose_is_not_forwarded():
    assert provider_question("My driver is Jane and lives at 42 Main Street") is None
    assert "password" not in provider_question("password: secret123")
