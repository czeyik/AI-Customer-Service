import importlib
import json
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Conversation, MediaAttachment, Message, Ticket, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.services.dialogue import (
    build_dialogue_context,
    complete_evidence_association,
    store_dialogue_data,
)
from app.services.ticket_drafts import DialogueData, Draft, DraftFields


migration = importlib.import_module(
    "migrations.versions.e5c1a2b3d4f6_add_dialogue_state"
)
NOW = datetime(2026, 9, 14, 12)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def legacy_row(state: str, data: dict | None = None) -> dict:
    return {
        "id": f"conversation-{state}",
        "channel": "whatsapp",
        "external_user_id": "60112223333",
        "intake_state": state,
        "intake_data": data if data is not None else {},
        "created_at": NOW,
    }


def draft_data(**updates) -> dict:
    data = {
        "description": "The fare was charged twice",
        "issue_collected": True,
        "ride_details_collected": False,
        "details_complete": False,
        "issue_type": "payment_or_fare",
        "urgency": "normal",
        "consent": False,
        "review_required": False,
        "evidence_group": "0ed17ed0-e57d-4218-8963-aa7a7ec757ba",
        "name": "Alex Tan",
        "email": "alex@example.com",
        "phone_number": "+60112223333",
        "started_at": NOW.isoformat(),
    }
    data.update(updates)
    return data


def transcript(*, current_outbound: bool = True) -> list[dict]:
    messages = [
        {
            "id": "privacy-prompt-message",
            "direction": "outbound",
            "content": "privacy-notice: Do you consent?",
            "created_at": NOW,
        },
        {
            "id": "consent-reply-message",
            "direction": "inbound",
            "content": "Yes",
            "created_at": NOW + timedelta(seconds=1),
        },
    ]
    if current_outbound:
        messages.append(
            {
                "id": "current-prompt-message",
                "direction": "outbound",
                "content": "What else should support know?",
                "created_at": NOW + timedelta(seconds=2),
            }
        )
    return messages


@pytest.mark.parametrize(
    ("state", "purpose", "field", "status"),
    [
        ("awaiting_consent", "consent", None, "active"),
        ("awaiting_name", "field", "name", "active"),
        ("awaiting_email", "field", "email", "active"),
        ("awaiting_phone", "field", "phone_number", "active"),
        ("awaiting_issue", "field", "description", "active"),
        ("awaiting_ride_details", "details", "ride_details", "active"),
        ("awaiting_additional_details", "details", None, "active"),
        ("awaiting_review", "review", None, "active"),
        ("paused", None, None, "paused"),
    ],
)
def test_every_legacy_draft_state_converts(
    state: str, purpose: str | None, field: str | None, status: str
) -> None:
    data = draft_data(consent=True, prompt_id="legacy-prompt")
    if state == "paused":
        data["resume_state"] = "awaiting_email"
    converted = migration.convert_legacy_dialogue(
        legacy_row(state, data), transcript(), {}
    )
    dialogue = DialogueData.model_validate(converted)

    assert dialogue.draft.status == status
    assert dialogue.draft.fields.email == "alex@example.com"
    assert dialogue.draft.consent.prompt_id == "privacy-prompt-message"
    assert dialogue.draft.evidence_group == data["evidence_group"]
    if purpose:
        assert (dialogue.pending_prompt.purpose, dialogue.pending_prompt.field) == (
            purpose,
            field,
        )
        assert dialogue.pending_prompt.id == "legacy-prompt"
    else:
        assert dialogue.pending_prompt is None


def test_unproven_consent_keeps_fields_but_requires_renewal() -> None:
    converted = migration.convert_legacy_dialogue(
        legacy_row("awaiting_email", draft_data(consent=True)), [], {}
    )
    dialogue = DialogueData.model_validate(converted)
    assert dialogue.draft.fields.name == "Alex Tan"
    assert dialogue.draft.consent is None


@pytest.mark.parametrize("started_at", [NOW.isoformat(), "2026-09-14T20:00:00+08:00", None])
def test_migration_binds_only_the_latest_consent_in_the_current_draft(started_at):
    prior = [dict(row, id="prior-" + row["id"], created_at=row["created_at"] - timedelta(days=1))
             for row in transcript(current_outbound=False)]
    current = transcript(current_outbound=False)
    latest = [dict(row, id="latest-" + row["id"], created_at=row["created_at"] + timedelta(seconds=10))
              for row in current]
    data = draft_data(consent=True, started_at=started_at)
    result = migration.convert_legacy_dialogue(legacy_row("awaiting_email", data), prior + current + latest, {})
    draft = DialogueData.model_validate(result).draft
    assert draft.fields.email == "alex@example.com"
    if started_at is None:
        assert draft.consent is None
    else:
        assert draft.consent.prompt_id == "latest-privacy-prompt-message"
        assert draft.consent.recorded_at == NOW + timedelta(seconds=11)
        old_only = migration.convert_legacy_dialogue(legacy_row("awaiting_email", data), prior, {})
        assert old_only["draft"]["consent"] is None


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL cutover integration")
def test_postgresql_cutover_preserves_last_legacy_write_and_queued_submission(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.config import Settings
    from app.services.chatbot import ChatbotService
    from app.services.inbound import process_next_inbound

    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    # A transaction-local schema keeps this populated migration check isolated
    # from other tests and is rolled back with all its rows at the end.
    with engine.connect() as connection, connection.begin() as transaction:
        schema = "cutover_" + uuid.uuid4().hex
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
        Base.metadata.create_all(connection, checkfirst=False)
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            started = datetime.utcnow()
            data = draft_data(consent=True, prompt_id="legacy-prompt", started_at=started.isoformat())
            conversation = Conversation(channel="whatsapp", external_user_id="60112223333",
                                        intake_state="awaiting_review", intake_data=data)
            db.add(conversation)
            db.flush()
            for index, row in enumerate(transcript()):
                db.add(Message(conversation_id=conversation.id, **{**row, "created_at": started + timedelta(seconds=index)}))
            attachment = MediaAttachment(provider_media_id="cutover-media", conversation_id=conversation.id,
                evidence_group=data["evidence_group"], media_type="image", declared_mime_type="image/png")
            inbound = WhatsAppInboundMessage(provider_message_id="cutover-submit", sender="60112223333",
                phone_number_id="synthetic", message_type="text", payload={"text": "Submit"})
            db.add_all([attachment, inbound])
            db.commit()
            # This is the last old-runtime write before writers stop.
            conversation.intake_data = {**data, "email": "corrected@example.com"}
            db.commit()
            conversation_id, attachment_id = conversation.id, attachment.id
            db.expunge_all()
            connection.exec_driver_sql("ALTER TABLE conversations DROP COLUMN dialogue_data, DROP COLUMN dialogue_revision")
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            converted = DialogueData.model_validate(db.get(Conversation, conversation_id).dialogue_data)
            assert converted.draft.fields.email == "corrected@example.com"
            assert converted.draft.evidence_group == data["evidence_group"]
            monkeypatch.setattr("app.services.inbound.chatbot_service", ChatbotService(
                settings=Settings(_env_file=None, llm_enabled=False)))
            assert process_next_inbound(db)
            assert not process_next_inbound(db)
            ticket = db.query(Ticket).one()
            assert ticket.email == "corrected@example.com"
            assert db.get(MediaAttachment, attachment_id).ticket_id == ticket.id
            assert db.query(WhatsAppOutboundMessage).count() == 1
        transaction.rollback()
    engine.dispose()


def test_idle_offer_media_and_owned_case_confirmation_convert() -> None:
    owned = {
        "ticket-1": {
            "id": "ticket-1",
            "public_id": "DUDU-20260914-ABCDE",
            "channel": "whatsapp",
            "external_user_id": "60112223333",
        }
    }
    idle = migration.convert_legacy_dialogue(
        legacy_row(
            "idle",
            {
                "pending_offer": {
                    "description": "Please ask support",
                    "issue_type": "unconfirmed_question",
                },
                "prompt_id": "offer-prompt",
                "evidence_group": "media-only",
                "last_ticket_id": "ticket-1",
            },
        ),
        transcript(),
        owned,
    )
    idle_dialogue = DialogueData.model_validate(idle)
    assert idle_dialogue.draft is None
    assert idle_dialogue.pending_offer.description == "Please ask support"
    assert idle_dialogue.evidence_group == "media-only"
    assert idle_dialogue.last_case_reference == "DUDU-20260914-ABCDE"

    pending = migration.convert_legacy_dialogue(
        legacy_row(
            "awaiting_case_confirmation",
            {
                "case_id": "ticket-1",
                "case_update": "Please add the latest receipt",
                "case_fields": {"trip_id": "TRIP-7"},
                "prompt_id": "case-prompt",
                "evidence_group": "media-only",
            },
        ),
        transcript(),
        owned,
    )
    pending_dialogue = DialogueData.model_validate(pending)
    assert pending_dialogue.pending_case_update.case_reference == "DUDU-20260914-ABCDE"
    assert pending_dialogue.pending_case_update.fields == {"trip_id": "TRIP-7"}
    assert pending_dialogue.pending_prompt.operation_id == (
        pending_dialogue.pending_case_update.operation_id
    )


def test_stale_prompt_and_unowned_case_are_not_treated_as_authority() -> None:
    data = draft_data(prompt_id="stale-prompt")
    converted = migration.convert_legacy_dialogue(
        legacy_row("awaiting_name", data), transcript(current_outbound=False), {}
    )
    assert converted["pending_prompt"] is None

    with pytest.raises(ValueError, match="not owned"):
        migration.convert_legacy_dialogue(
            legacy_row(
                "awaiting_case_confirmation",
                {"case_id": "ticket-1", "case_update": "Update this"},
            ),
            transcript(),
            {
                "ticket-1": {
                    "id": "ticket-1",
                    "public_id": "DUDU-1",
                    "channel": "web",
                    "external_user_id": "someone-else",
                }
            },
        )


def test_preflight_reports_all_invalid_rows_before_conversion() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE conversations (id TEXT, channel TEXT, external_user_id TEXT, "
                "intake_state TEXT, intake_data JSON, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE messages (id TEXT, conversation_id TEXT, direction TEXT, "
                "content TEXT, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE tickets (id TEXT, public_id TEXT, channel TEXT, external_user_id TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO conversations VALUES "
                "('bad-state', 'web', 'one', 'mystery', '{}', :now), "
                "('bad-data', 'web', 'two', 'paused', :data, :now)"
            ),
            {"now": NOW, "data": json.dumps({"resume_state": "mystery"})},
        )
        with pytest.raises(RuntimeError) as error:
            migration._converted_rows(connection)

    message = str(error.value)
    assert "bad-state: unknown intake_state" in message
    assert "bad-data: intake_data is not an object" in message
    assert "no rows were converted" in message


def test_context_keeps_three_exchanges_and_sanitizes_private_history(db: Session) -> None:
    dialogue = DialogueData(
        draft=Draft(
            expires_at=NOW + timedelta(hours=1),
            fields=DraftFields(
                name="Alex Tan",
                email="new@example.com",
                phone_number="+60112223333",
                description="My fare was charged twice",
            ),
        )
    )
    conversation = Conversation(
        channel="web",
        external_user_id="context-user",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    db.add(conversation)
    db.flush()
    contents = [
        ("inbound", "The first topic was fare policy"),
        ("outbound", "I can help with fare policy"),
        ("inbound", "My old email was old@example.com"),
        ("outbound", "What happened next?"),
        ("inbound", "My pickup at 10 Private Road"),
        ("outbound", "Please review these ticket details\nName: Alex Tan\nEmail: new@example.com"),
        ("inbound", "Why was the first topic important?"),
        ("outbound", '{"email":"new@example.com","description":"private"}'),
    ]
    for index, (direction, content) in enumerate(contents):
        db.add(
            Message(
                conversation_id=conversation.id,
                direction=direction,
                content=content,
                created_at=NOW + timedelta(seconds=index),
            )
        )
    db.commit()

    context = build_dialogue_context(
        db, conversation, "Can we return to the first topic?"
    )
    serialized = context.model_dump_json()
    assert context.fits
    assert context.history[0]["content"] == "The first topic was fare policy"
    assert "[FIELD_EMAIL]" in serialized
    assert "[TICKET_REVIEW fields_present=name,email]" in serialized
    assert "[DIALOGUE_TOOL_RESULT fields_present=email,description]" in serialized
    assert "Alex Tan" not in serialized
    assert "old@example.com" not in serialized
    assert "new@example.com" not in serialized
    assert "Private Road" not in serialized
    assert context.omitted_messages == 1


def test_context_limits_history_by_count_and_whole_items(db: Session) -> None:
    conversation = Conversation(channel="web", external_user_id="bounded-user")
    db.add(conversation)
    db.flush()
    for index in range(15):
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="inbound" if index % 2 == 0 else "outbound",
                content=f"safe message {index} with enough padding",
                created_at=NOW + timedelta(seconds=index),
            )
        )
    db.commit()

    counted = build_dialogue_context(db, conversation, "current question")
    assert len(counted.history) == 12
    assert counted.history[0]["content"].startswith("safe message 3")
    bounded = build_dialogue_context(
        db, conversation, "current question must remain whole", max_input_chars=650
    )
    assert bounded.fits
    assert len(bounded.history) < 12
    assert bounded.current_question == "current question must remain whole"
    assert bounded.omitted_messages > 0

    rejected = build_dialogue_context(
        db, conversation, "pickup at 10 Private Road", max_input_chars=650
    )
    assert not rejected.fits
    assert rejected.reason == "private_current_input"
    assert rejected.history == []


def test_revision_changes_for_state_and_evidence_events() -> None:
    conversation = Conversation(
        channel="web", external_user_id="revision-user", dialogue_revision=0
    )
    dialogue = DialogueData(evidence_group="group-1")
    assert store_dialogue_data(conversation, dialogue)
    assert conversation.dialogue_revision == 1
    assert not store_dialogue_data(conversation, dialogue)
    assert conversation.dialogue_revision == 1
    assert store_dialogue_data(conversation, dialogue, evidence_changed=True)
    assert conversation.dialogue_revision == 2

    complete_evidence_association(conversation, "group-1", "DUDU-20260914-ABCDE")
    assert conversation.dialogue_data["evidence_group"] is None
    assert conversation.dialogue_data["last_case_reference"] == "DUDU-20260914-ABCDE"
    assert conversation.dialogue_revision == 3
