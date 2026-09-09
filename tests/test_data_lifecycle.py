import logging
import os
from collections.abc import Generator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import (
    AdminUser,
    AuditLog,
    Base,
    Conversation,
    LegalHold,
    MediaAttachment,
    Message,
    SupportNotification,
    Ticket,
    TicketNote,
    WhatsAppInboundMessage,
    WhatsAppOutboundMessage,
)
from app.services.retention import create_legal_hold, release_legal_hold, run_retention


NOW = datetime(2026, 9, 9, 12)
SETTINGS = Settings(retention_batch_size=100)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.close()


class ObjectStore:
    def __init__(self, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        if self.fail_once:
            self.fail_once = False
            raise OSError("object store unavailable")
        self.deleted.append(key)


def admin(username: str, *, active: bool = True) -> AdminUser:
    return AdminUser(
        username=username,
        display_name=username,
        email=f"{username}@example.com",
        password_hash="hash",
        totp_secret_ref=f"admin/{username}/totp",
        is_active=active,
    )


def ticket(conversation: Conversation, *, public_id: str, closed_at: datetime) -> Ticket:
    return Ticket(
        public_id=public_id,
        status="closed",
        closed_at=closed_at,
        conversation=conversation,
        urgency="normal",
        channel="whatsapp",
        external_user_id=conversation.external_user_id,
        name="Old Customer",
        email="old@example.com",
        phone_number="+60111111111",
        issue_type="support",
        description="Old issue",
        consent_given=True,
    )


def test_dry_run_then_deletes_all_chat_copies_and_detaches_retained_ticket(
    db_session: Session,
) -> None:
    expired = NOW - timedelta(days=91)
    boundary = NOW - timedelta(days=90)
    old = Conversation(
        channel="whatsapp", external_user_id="60110000001", created_at=expired, updated_at=expired
    )
    current = Conversation(
        channel="whatsapp", external_user_id="60110000002", created_at=expired, updated_at=boundary
    )
    db_session.add_all([old, current])
    db_session.flush()
    old_message = Message(
        conversation_id=old.id,
        direction="inbound",
        content="delete me",
        created_at=expired,
        updated_at=expired,
    )
    boundary_message = Message(
        conversation_id=current.id,
        direction="inbound",
        content="keep me",
        created_at=boundary,
        updated_at=boundary,
    )
    inbound = WhatsAppInboundMessage(
        provider_message_id="wamid.old",
        sender=old.external_user_id,
        phone_number_id="123",
        message_type="text",
        payload={"text": "delete me"},
        created_at=expired,
        updated_at=expired,
    )
    db_session.add_all([old_message, boundary_message, inbound])
    db_session.flush()
    unlinked_media = MediaAttachment(
        provider_media_id="media-chat-old",
        inbound_message_id=inbound.id,
        conversation_id=old.id,
        media_type="image",
        declared_mime_type="image/png",
        status="approved",
        object_key="approved/chat-old.png",
        created_at=expired,
        updated_at=expired,
    )
    outbound = WhatsAppOutboundMessage(
        inbound_message_id=inbound.id,
        recipient=old.external_user_id,
        body="old reply",
        created_at=expired,
        updated_at=expired,
    )
    retained_ticket = ticket(old, public_id="DUDU-RETAIN", closed_at=NOW - timedelta(days=100))
    db_session.add_all([unlinked_media, outbound, retained_ticket])
    db_session.commit()
    old_id = old.id

    dry = run_retention(db_session, now=NOW, dry_run=True, settings=SETTINGS)
    assert (dry.messages, dry.transport_messages, dry.attachments, dry.objects, dry.tickets) == (
        1,
        2,
        1,
        1,
        0,
    )
    assert db_session.get(Message, old_message.id)
    assert db_session.query(AuditLog).filter_by(event_type="retention_dry_run").one()

    storage = ObjectStore()
    result = run_retention(db_session, now=NOW, settings=SETTINGS, storage=storage)
    assert (result.messages, result.transport_messages, result.conversations) == (1, 2, 1)
    assert storage.deleted == ["approved/chat-old.png"]
    assert db_session.query(Message).all() == [boundary_message]
    assert db_session.query(WhatsAppInboundMessage).count() == 0
    assert db_session.query(WhatsAppOutboundMessage).count() == 0
    assert db_session.get(Conversation, old_id) is None
    db_session.refresh(retained_ticket)
    assert retained_ticket.conversation_id is None
    assert db_session.query(Conversation).filter_by(external_user_id="60110000002").one()


def test_ticket_media_dependencies_and_indexes_expire_at_36_months(
    db_session: Session,
) -> None:
    owner = admin("czeyik")
    conversation = Conversation(channel="whatsapp", external_user_id="60112223333")
    expired_ticket = ticket(conversation, public_id="DUDU-OLD", closed_at=datetime(2023, 9, 9, 12))
    db_session.add_all([owner, conversation, expired_ticket])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        provider_message_id="wamid.media",
        sender=conversation.external_user_id,
        phone_number_id="123",
        message_type="image",
    )
    db_session.add(inbound)
    db_session.flush()
    attachment = MediaAttachment(
        provider_media_id="media-old",
        inbound_message_id=inbound.id,
        conversation_id=conversation.id,
        ticket_id=expired_ticket.id,
        media_type="image",
        declared_mime_type="image/png",
        status="approved",
        object_key="approved/old.png",
    )
    note = TicketNote(ticket_id=expired_ticket.id, author_admin_id=owner.id, body="private note")
    notification = SupportNotification(
        ticket_id=expired_ticket.id,
        recipient="old@example.com",
        channel="email",
        event_type="ticket_created",
        payload={"public_id": expired_ticket.public_id},
    )
    audit = AuditLog(
        actor="czeyik",
        event_type="ticket_note_added",
        details={"ticket_id": expired_ticket.id},
    )
    db_session.add_all([attachment, note, notification, audit])
    db_session.commit()

    storage = ObjectStore()
    result = run_retention(db_session, now=NOW, storage=storage, settings=SETTINGS)

    assert (result.tickets, result.attachments, result.objects, result.failures) == (1, 1, 1, 0)
    assert storage.deleted == ["approved/old.png"]
    assert db_session.query(Ticket).filter_by(public_id="DUDU-OLD").count() == 0
    assert db_session.query(MediaAttachment).filter_by(provider_media_id="media-old").count() == 0
    assert db_session.query(TicketNote).count() == 0
    assert db_session.query(SupportNotification).count() == 0
    assert db_session.query(AuditLog).filter_by(event_type="ticket_note_added").count() == 0


def test_privacy_owner_hold_blocks_expiry_until_audited_release(db_session: Session) -> None:
    owner, other = admin("czeyik"), admin("jane")
    conversation = Conversation(
        channel="whatsapp",
        external_user_id="held-user",
        created_at=NOW - timedelta(days=100),
        updated_at=NOW - timedelta(days=100),
    )
    held_ticket = ticket(conversation, public_id="DUDU-HELD", closed_at=datetime(2020, 1, 1))
    db_session.add_all([owner, other, conversation, held_ticket])
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        direction="inbound",
        content="held message",
        created_at=NOW - timedelta(days=100),
        updated_at=NOW - timedelta(days=100),
    )
    db_session.add(message)
    db_session.commit()
    conversation_id, ticket_id = conversation.id, held_ticket.id

    with pytest.raises(PermissionError):
        create_legal_hold(
            db_session,
            actor=other,
            subject_type="ticket",
            subject_id=held_ticket.id,
            reason="Litigation",
            reference="CASE-1",
            expires_at=NOW + timedelta(days=30),
            now=NOW,
            settings=SETTINGS,
        )
    ticket_hold = create_legal_hold(
        db_session,
        actor=owner,
        subject_type="ticket",
        subject_id=held_ticket.id,
        reason="Litigation",
        reference="CASE-1",
        expires_at=NOW + timedelta(days=30),
        now=NOW,
        settings=SETTINGS,
    )
    conversation_hold = create_legal_hold(
        db_session,
        actor=owner,
        subject_type="conversation",
        subject_id=conversation.id,
        reason="Regulatory request",
        reference="CASE-2",
        expires_at=NOW + timedelta(days=30),
        now=NOW,
        settings=SETTINGS,
    )

    held = run_retention(db_session, now=NOW, settings=SETTINGS)
    assert (held.messages, held.tickets) == (0, 0)
    release_legal_hold(db_session, actor=owner, hold=ticket_hold, now=NOW, settings=SETTINGS)
    release_legal_hold(db_session, actor=owner, hold=conversation_hold, now=NOW, settings=SETTINGS)
    assert db_session.query(AuditLog).filter_by(event_type="legal_hold_created").count() == 2
    assert db_session.query(AuditLog).filter_by(event_type="legal_hold_released").count() == 2

    expired = run_retention(db_session, now=NOW, settings=SETTINGS)
    assert (expired.messages, expired.tickets) == (1, 1)
    assert db_session.get(Ticket, ticket_id) is None
    assert db_session.get(Conversation, conversation_id) is None
    assert db_session.query(LegalHold).count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.event_type.like("legal_hold_%")).count() == 0


def test_object_failure_alerts_and_next_run_retries(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    conversation = Conversation(channel="whatsapp", external_user_id="retry-user")
    expired_ticket = ticket(conversation, public_id="DUDU-RETRY", closed_at=datetime(2020, 1, 1))
    db_session.add_all([conversation, expired_ticket])
    db_session.flush()
    inbound = WhatsAppInboundMessage(
        provider_message_id="wamid.retry",
        sender="retry-user",
        phone_number_id="123",
        message_type="image",
    )
    db_session.add(inbound)
    db_session.flush()
    db_session.add(
        MediaAttachment(
            provider_media_id="media-retry",
            inbound_message_id=inbound.id,
            conversation_id=conversation.id,
            ticket_id=expired_ticket.id,
            media_type="image",
            declared_mime_type="image/png",
            status="approved",
            object_key="approved/retry.png",
        )
    )
    db_session.commit()
    storage = ObjectStore(fail_once=True)

    with caplog.at_level(logging.ERROR):
        failed = run_retention(db_session, now=NOW, storage=storage, settings=SETTINGS)
    assert failed.failures == 1 and db_session.get(Ticket, expired_ticket.id)
    assert db_session.query(AuditLog).filter_by(event_type="retention_deletion_failed").one()
    assert "retention object deletion failed" in caplog.text

    retried = run_retention(db_session, now=NOW, storage=storage, settings=SETTINGS)
    assert retried.failures == 0 and retried.tickets == 1
    assert storage.deleted == ["approved/retry.png"]
    assert db_session.get(Ticket, expired_ticket.id) is None
    repeated = run_retention(db_session, now=NOW, storage=storage, settings=SETTINGS)
    assert (repeated.tickets, repeated.attachments, repeated.objects, repeated.failures) == (
        0,
        0,
        0,
        0,
    )


def test_postgresql_migration_and_lifecycle_boundary() -> None:
    database_url = os.getenv("LIFECYCLE_INTEGRATION_DATABASE_URL")
    if not database_url:
        pytest.skip("set LIFECYCLE_INTEGRATION_DATABASE_URL to a migrated disposable database")
    db = sessionmaker(bind=create_engine(database_url), autocommit=False, autoflush=False)()
    expired = NOW - timedelta(days=100)
    try:
        conversation = Conversation(
            channel="whatsapp",
            external_user_id="postgres-retention-check",
            created_at=expired,
            updated_at=expired,
        )
        expired_ticket = ticket(
            conversation, public_id="DUDU-PG-RETENTION", closed_at=datetime(2020, 1, 1)
        )
        db.add_all([conversation, expired_ticket])
        db.flush()
        inbound = WhatsAppInboundMessage(
            provider_message_id="wamid.pg-retention",
            sender=conversation.external_user_id,
            phone_number_id="123",
            message_type="image",
            created_at=expired,
            updated_at=expired,
        )
        message = Message(
            conversation_id=conversation.id,
            direction="inbound",
            content="expired",
            created_at=expired,
            updated_at=expired,
        )
        db.add_all([inbound, message])
        db.flush()
        attachment = MediaAttachment(
            provider_media_id="media-pg-retention",
            inbound_message_id=inbound.id,
            conversation_id=conversation.id,
            ticket_id=expired_ticket.id,
            media_type="image",
            declared_mime_type="image/png",
            status="approved",
            object_key="approved/postgres-check.png",
        )
        db.add_all(
            [
                attachment,
                AuditLog(
                    actor="legacy",
                    event_type="ticket_note_added",
                    details={"ticket_id": expired_ticket.id},
                ),
            ]
        )
        db.commit()
        ticket_id = expired_ticket.id

        result = run_retention(db, now=NOW, storage=ObjectStore(), settings=SETTINGS)

        assert (result.messages, result.tickets, result.objects, result.failures) == (1, 1, 1, 0)
        assert db.query(Ticket).filter_by(public_id="DUDU-PG-RETENTION").count() == 0
        assert (
            db.query(MediaAttachment).filter_by(provider_media_id="media-pg-retention").count()
            == 0
        )
        assert db.query(AuditLog).filter_by(subject_id=ticket_id).count() == 0
        assert db.query(AuditLog).filter_by(event_type="ticket_note_added").count() == 0
    finally:
        db.close()
