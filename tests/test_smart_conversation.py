import json
from datetime import datetime, timedelta

import pytest

from test_ticket_intake import db_session
from app.config import Settings
from app.models import Conversation, Ticket, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.schemas import ChatRequest
from app.services.answer_generation import ApprovedKnowledgeResponder, ProviderResponse
from app.services.chatbot import ChatbotService
from app.services.inbound import process_next_inbound
from app.services.pii import provider_question


class Provider:
    name = "fake"
    model = "synthetic"

    def __init__(self, *results):
        self.results = iter(results)
        self.inputs = []

    def generate(self, messages, **kwargs):
        self.inputs.append(json.loads(messages[-1]["content"]))
        return ProviderResponse(json.dumps(next(self.results)), 100, 30)


def service_with(*results):
    provider = Provider(*results)
    service = ChatbotService()
    service.answer_generator = ApprovedKnowledgeResponder(Settings(
        _env_file=None, llm_customer_context_enabled=True,
    ), provider)
    return service, provider


def result(disposition="clarify", answer="Which service do you mean?", **kwargs):
    return dict(disposition=disposition, answer=answer, **({"citations": []} | kwargs))


def send(service, db, text, **kwargs):
    return service.handle(db, ChatRequest(external_user_id="synthetic-user", text=text, **kwargs))


def test_provider_receives_minimized_question_and_bounded_context(db_session):
    service, provider = service_with(result(topic="booking"), result())
    send(service, db_session, "My name is Jane, jane@example.com, +60123456789. How do I book a car?", account_id="ACCT-42", trip_id="TRIP-87")
    send(service, db_session, "What about tomorrow?")
    captured = json.dumps(provider.inputs)
    for secret in ("Jane", "jane@example.com", "+60123456789", "synthetic-user", "ACCT-42", "TRIP-87"):
        assert secret not in captured
    assert "How do I book a car?" in captured
    assert provider.inputs[1]["context"]["topic"] == "booking"
    assert len(provider.inputs) == 2


@pytest.mark.parametrize("text", ["Are you human?", "Are you AI?", "你是人工吗？", "Adakah anda manusia?"])
def test_identity_does_not_call_provider_or_offer_ticket(db_session, text):
    service, provider = service_with()
    response = send(service, db_session, text)
    assert not provider.inputs and not response.needs_ticket_consent
    assert db_session.query(Conversation).one().intake_state == "idle"


def test_mixed_identity_and_business_question_calls_interpreter(db_session):
    service, provider = service_with(result())
    send(service, db_session, "Hi, are you human? How do I book a car?")
    assert len(provider.inputs) == 1


def test_offer_does_not_start_intake_and_acknowledgement_is_not_consent(db_session):
    service, provider = service_with(result("offer_ticket", "Would you like a support ticket?"), result("smalltalk", "You're welcome."))
    send(service, db_session, "Does DUDU support ferry transfers?")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_state == "idle"
    assert conversation.intake_data["pending_offer"]
    send(service, db_session, "Thanks")
    assert conversation.intake_state == "idle"
    assert not db_session.query(Ticket).count()


def test_fields_any_order_and_corrections_remain_local(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes")
    send(service, db_session, "My name is Alex, alex@example.com, +60198765432")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_data["name"] == "Alex"
    assert conversation.intake_data["email"] == "alex@example.com"
    assert conversation.intake_data["phone_number"] == "+60198765432"
    assert conversation.intake_state == "awaiting_issue"
    send(service, db_session, "The receipt for my ride is missing")
    send(service, db_session, "My email is corrected@example.com")
    assert conversation.intake_data["email"] == "corrected@example.com"
    send(service, db_session, "Skip")
    assert conversation.intake_state == "awaiting_review"
    assert not db_session.query(Ticket).count()
    completed = send(service, db_session, "Submit")
    assert completed.ticket
    assert db_session.query(Ticket).one().email == "corrected@example.com"


def test_expired_intake_returns_to_idle_without_mutation(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    conversation = db_session.query(Conversation).one()
    conversation.intake_data = {**conversation.intake_data, "started_at": (datetime.utcnow() - timedelta(days=1)).isoformat()}
    db_session.commit()
    send(service, db_session, "Hello")
    assert conversation.intake_state == "idle"
    assert not db_session.query(Ticket).count()


def test_unbound_and_stale_api_flags_do_not_create_ticket(db_session):
    service = ChatbotService()
    send(service, db_session, "Hello", create_ticket=True, consent_to_ticket=True)
    assert db_session.query(Conversation).one().intake_state == "idle"
    prompt = send(service, db_session, "I need a human")
    send(service, db_session, "Hello", create_ticket=True, consent_to_ticket=True, prompt_id="stale")
    assert not db_session.query(Conversation).one().intake_data.get("consent")
    current = db_session.query(Conversation).one().intake_data["prompt_id"]
    send(service, db_session, "Yes", consent_to_ticket=True, prompt_id=current)
    assert db_session.query(Conversation).one().intake_data["consent"]


def test_uncertain_identity_prose_is_not_forwarded():
    assert provider_question("My driver is Jane and lives at 42 Main Street") is None
    assert "password" not in provider_question("password: secret123")


def test_durable_inbound_is_processed_once(db_session, monkeypatch):
    service = ChatbotService()
    monkeypatch.setattr("app.services.inbound.chatbot_service", service)
    db_session.add(WhatsAppInboundMessage(provider_message_id="synthetic-1", sender="60123456789", phone_number_id="test", message_type="text", payload={"text": "Hello", "timestamp": "100"}))
    db_session.commit()
    assert process_next_inbound(db_session)
    assert not process_next_inbound(db_session)
    assert db_session.query(WhatsAppOutboundMessage).count() == 1
    assert db_session.query(WhatsAppInboundMessage).one().status == "done"


def test_citation_string_normalization_is_bounded():
    from app.services.answer_generation import ConversationResult
    from pydantic import ValidationError
    assert ConversationResult.model_validate(result(citations=["1"])).citations == [1]
    with pytest.raises(ValidationError):
        ConversationResult.model_validate(result(citations=[True]))
    with pytest.raises(ValidationError):
        ConversationResult.model_validate(result(citations=["99"]))


def test_side_question_can_correct_email_without_erasing_case(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes")
    send(service, db_session, "My name is Alex, alex@example.com, +60123456789")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_state == "awaiting_issue"
    send(service, db_session, "The receipt for my ride is missing")
    # A supplied field is locally extracted even when the turn is interpreted as a correction.
    interpreted, provider = service_with(result(intake_action="correct", fields=["email"]))
    send(interpreted, db_session, "My email is new@example.com")
    assert conversation.intake_data["email"] == "new@example.com"
    assert conversation.intake_data["description"] == "The receipt for my ride is missing"
    assert "new@example.com" not in json.dumps(provider.inputs)


def test_fields_supplied_before_consent_are_not_requested_again(db_session):
    service, provider = service_with(result("explicit_handoff", "May we arrange support?", issue_type="human_escalation"), result(intake_action="continue"))
    send(service, db_session, "I need a human. My name is Alex, alex@example.com, +60123456789")
    send(service, db_session, "Yes")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_state == "awaiting_issue"
    assert conversation.intake_data["email"] == "alex@example.com"


def test_refund_request_keeps_financial_priority_and_action_boundary(db_session):
    service = ChatbotService()
    response = send(service, db_session, "Refund me")
    assert "can’t perform refunds" in response.answer
    assert db_session.query(Conversation).one().intake_data["urgency"] == "high"
    assert "account_action_blocked" in response.safety_flags
    assert response.ticket is None


def test_issue_reply_is_collected_even_when_model_also_troubleshoots(db_session, monkeypatch):
    from app.services.answer_generation import ConversationResult
    service = ChatbotService()
    for text in ("I need a human", "Yes", "My name is Alex, alex@example.com, +60123456789"):
        send(service, db_session, text)
    monkeypatch.setattr(service.answer_generator, "generate", lambda *args: ConversationResult(
        disposition="troubleshoot", answer="Check the receipt section of your app.", citations=[],
    ))
    send(service, db_session, "The receipt for my ride is missing")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_data["description"] == "The receipt for my ride is missing"
    assert conversation.intake_state == "awaiting_ride_details"


def test_model_handoff_proposal_cannot_start_intake_on_hypothetical(db_session):
    service, provider = service_with(result("explicit_handoff", "May we arrange support?", issue_type="safety_incident"))
    response = send(service, db_session, "如果有人受伤，应该怎么做？")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_state == "idle"
    assert conversation.risk_level == "normal"
    assert response.ticket is None and not response.needs_ticket_consent


def test_submission_controls_cannot_become_missing_name(db_session):
    service = ChatbotService()
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes")
    for control in ("Skip", "Done", "Submit"):
        send(service, db_session, control)
        conversation = db_session.query(Conversation).one()
        assert conversation.intake_state == "awaiting_name"
        assert not conversation.intake_data.get("name")
        assert db_session.query(Ticket).count() == 0


def test_pause_and_resume_preserve_supplied_fields(db_session):
    service, _ = service_with(result(), result(), result(intake_action="pause"), result(intake_action="resume"))
    send(service, db_session, "I need a human")
    send(service, db_session, "Yes", name="Alex", email="alex@example.com")
    send(service, db_session, "Let us pick this up later")
    conversation = db_session.query(Conversation).one()
    assert conversation.intake_state == "paused"
    assert conversation.intake_data["email"] == "alex@example.com"
    send(service, db_session, "Continue where we stopped")
    assert conversation.intake_state == "awaiting_phone"
    assert conversation.intake_data["name"] == "Alex"


@pytest.mark.parametrize("proposal", ["pause", "cancel", "resume", "continue_case", "correct", "submit"])
def test_unfounded_model_proposals_cannot_interrupt_local_controls(db_session, proposal):
    service, _ = service_with(*(result(intake_action=action) for action in (proposal, proposal, proposal, proposal, "continue", proposal)))
    for message in ("I need a human", "Yes", "My name is Alex", "alex@example.com", "My ride receipt is missing", "Skip"):
        response = send(service, db_session, message, phone_number="+60123456789")
    assert response.ticket is not None
    ticket = db_session.query(Ticket).one()
    assert ticket.name == "Alex" and ticket.email == "alex@example.com"
