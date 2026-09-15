import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AdminUser,
    AuditLog,
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
from app.services.media import PrivateObjectStore
from app.services.dialogue import scrub_dialogue_private_data


logger = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    messages: int = 0
    transport_messages: int = 0
    conversations: int = 0
    tickets: int = 0
    attachments: int = 0
    objects: int = 0
    failures: int = 0


def _active_hold(subject_type: str, subject_id, now: datetime):
    return exists().where(
        LegalHold.subject_type == subject_type,
        LegalHold.subject_id == subject_id,
        LegalHold.released_at.is_(None),
        LegalHold.expires_at > now,
    )


def _three_year_cutoff(now: datetime) -> datetime:
    try:
        return now.replace(year=now.year - 3)
    except ValueError:
        return now.replace(year=now.year - 3, day=28)


def _privacy_owner(actor: AdminUser, settings: Settings) -> None:
    if not actor.is_active or actor.username != settings.privacy_owner_username:
        raise PermissionError("only the active privacy owner may manage legal holds")


def create_legal_hold(
    db: Session,
    *,
    actor: AdminUser,
    subject_type: str,
    subject_id: str,
    reason: str,
    reference: str,
    expires_at: datetime,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> LegalHold:
    settings = settings or get_settings()
    now = now or datetime.utcnow()
    _privacy_owner(actor, settings)
    reason, reference = reason.strip(), reference.strip()
    model = {"conversation": Conversation, "ticket": Ticket}.get(subject_type)
    if not model or not db.get(model, subject_id):
        raise ValueError("the held conversation or ticket does not exist")
    if not reason or len(reason) > 500 or not reference or len(reference) > 120:
        raise ValueError("reason and reference are required and must fit their limits")
    if expires_at <= now:
        raise ValueError("legal hold expiry must be in the future")

    hold = db.execute(
        select(LegalHold).where(
            LegalHold.subject_type == subject_type, LegalHold.subject_id == subject_id
        )
    ).scalar_one_or_none()
    if hold and hold.released_at is None and hold.expires_at > now:
        if (
            hold.reason == reason
            and hold.reference == reference
            and hold.expires_at == expires_at
        ):
            return hold
        raise ValueError("an active legal hold already exists for this subject")
    if hold:
        hold.reason = reason
        hold.reference = reference
        hold.expires_at = expires_at
        hold.created_by_admin_id = actor.id
        hold.released_at = None
        hold.released_by_admin_id = None
    else:
        hold = LegalHold(
            subject_type=subject_type,
            subject_id=subject_id,
            reason=reason,
            reference=reference,
            expires_at=expires_at,
            created_by_admin_id=actor.id,
        )
        db.add(hold)
    db.flush()
    db.add(
        AuditLog(
            actor=actor.username,
            event_type="legal_hold_created",
            subject_type="legal_hold",
            subject_id=hold.id,
            details={
                "subject_type": subject_type,
                "subject_id": subject_id,
                "reference": reference,
                "expires_at": expires_at.isoformat(),
            },
        )
    )
    db.commit()
    return hold


def release_legal_hold(
    db: Session,
    *,
    actor: AdminUser,
    hold: LegalHold,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    now = now or datetime.utcnow()
    _privacy_owner(actor, settings)
    if hold.released_at is not None:
        return
    hold.released_at = now
    hold.released_by_admin_id = actor.id
    db.add(
        AuditLog(
            actor=actor.username,
            event_type="legal_hold_released",
            subject_type="legal_hold",
            subject_id=hold.id,
            details={"reference": hold.reference},
        )
    )
    db.commit()


def _delete_attachment_object(
    db: Session,
    attachment: MediaAttachment,
    storage: PrivateObjectStore | None,
    result: RetentionResult,
    *,
    subject_type: str,
    subject_id: str,
) -> bool:
    try:
        if attachment.object_key:
            (storage or PrivateObjectStore()).delete(attachment.object_key)
            result.objects += 1
        return True
    except Exception as exc:
        db.rollback()
        result.failures += 1
        logger.error(
            "retention object deletion failed",
            extra={"attachment_id": attachment.id, "subject_type": subject_type},
        )
        db.add(
            AuditLog(
                actor="retention-worker",
                event_type="retention_deletion_failed",
                subject_type=subject_type,
                subject_id=subject_id,
                details={"attachment_id": attachment.id, "error": type(exc).__name__},
            )
        )
        db.commit()
        return False


def _expire_unlinked_media(
    db: Session,
    cutoff: datetime,
    now: datetime,
    limit: int,
    dry_run: bool,
    storage: PrivateObjectStore | None,
    result: RetentionResult,
) -> None:
    attachments = db.execute(
        select(MediaAttachment)
        .where(
            MediaAttachment.ticket_id.is_(None),
            MediaAttachment.created_at < cutoff,
            ~_active_hold("conversation", MediaAttachment.conversation_id, now),
        )
        .order_by(MediaAttachment.created_at)
        .limit(limit)
    ).scalars().all()
    for attachment in attachments:
        result.attachments += 1
        result.objects += int(bool(dry_run and attachment.object_key))
        if dry_run:
            continue
        if not _delete_attachment_object(
            db,
            attachment,
            storage,
            result,
            subject_type="conversation",
            subject_id=attachment.conversation_id or attachment.id,
        ):
            continue
        db.execute(
            delete(AuditLog).where(
                or_(
                    (AuditLog.subject_type == "attachment")
                    & (AuditLog.subject_id == attachment.id),
                    AuditLog.details["attachment_id"].as_string() == attachment.id,
                )
            )
        )
        db.delete(attachment)
        db.commit()


def _expire_chats(
    db: Session,
    cutoff: datetime,
    now: datetime,
    limit: int,
    dry_run: bool,
    result: RetentionResult,
) -> None:
    message_ids = db.execute(
        select(Message.id)
        .where(
            Message.created_at < cutoff,
            ~_active_hold("conversation", Message.conversation_id, now),
        )
        .order_by(Message.created_at)
        .limit(limit)
    ).scalars().all()
    result.messages += len(message_ids)

    inbound_ids = db.execute(
        select(WhatsAppInboundMessage.id)
        .where(
            WhatsAppInboundMessage.created_at < cutoff,
            ~exists().where(
                Conversation.channel == "whatsapp",
                Conversation.external_user_id == WhatsAppInboundMessage.sender,
                _active_hold("conversation", Conversation.id, now),
            ),
        )
        .order_by(WhatsAppInboundMessage.created_at)
        .limit(limit)
    ).scalars().all()
    old_outbound_ids = db.execute(
        select(WhatsAppOutboundMessage.id).join(
            WhatsAppInboundMessage,
            WhatsAppInboundMessage.id == WhatsAppOutboundMessage.inbound_message_id,
        )
        .where(
            or_(
                WhatsAppOutboundMessage.created_at < cutoff,
                WhatsAppOutboundMessage.inbound_message_id.in_(inbound_ids),
            ),
            ~exists().where(
                Conversation.channel == "whatsapp",
                Conversation.external_user_id == WhatsAppInboundMessage.sender,
                _active_hold("conversation", Conversation.id, now),
            ),
        )
        # Each inbound has at most one reply; include its dependency before aging other rows.
        .order_by(
            WhatsAppOutboundMessage.inbound_message_id.in_(inbound_ids).desc(),
            WhatsAppOutboundMessage.created_at,
        )
        .limit(limit)
    ).scalars().all()
    result.transport_messages += len(inbound_ids) + len(old_outbound_ids)
    webhook_audit_ids = db.execute(
        select(AuditLog.id)
        .where(
            AuditLog.event_type == "meta_webhook_received", AuditLog.created_at < cutoff
        )
        .order_by(AuditLog.created_at)
        .limit(limit)
    ).scalars().all()
    if not dry_run:
        if message_ids:
            db.execute(delete(Message).where(Message.id.in_(message_ids)))
        if old_outbound_ids:
            db.execute(
                delete(WhatsAppOutboundMessage).where(
                    WhatsAppOutboundMessage.id.in_(old_outbound_ids)
                )
            )
        if inbound_ids:
            db.execute(
                update(MediaAttachment)
                .where(MediaAttachment.inbound_message_id.in_(inbound_ids))
                .values(inbound_message_id=None)
            )
            db.execute(
                delete(WhatsAppInboundMessage).where(WhatsAppInboundMessage.id.in_(inbound_ids))
            )
        if webhook_audit_ids:
            db.execute(delete(AuditLog).where(AuditLog.id.in_(webhook_audit_ids)))
        db.commit()

    retained_conversations = db.execute(
        select(Conversation)
        .where(
            Conversation.created_at < cutoff,
            ~_active_hold("conversation", Conversation.id, now),
            ~exists().where(Message.conversation_id == Conversation.id),
            exists().where(
                MediaAttachment.conversation_id == Conversation.id,
                MediaAttachment.ticket_id.is_(None),
            ),
        )
        .order_by(Conversation.created_at)
        .limit(limit)
    ).scalars().all()
    if not dry_run:
        for conversation in retained_conversations:
            evidence_group = db.execute(
                select(MediaAttachment.evidence_group)
                .where(
                    MediaAttachment.conversation_id == conversation.id,
                    MediaAttachment.ticket_id.is_(None),
                )
                .order_by(MediaAttachment.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            scrub_dialogue_private_data(conversation, evidence_group)
        db.commit()

    conversation_ids = db.execute(
        select(Conversation.id)
        .where(
            Conversation.created_at < cutoff,
            ~_active_hold("conversation", Conversation.id, now),
            ~exists().where(Message.conversation_id == Conversation.id),
            ~exists().where(
                MediaAttachment.conversation_id == Conversation.id,
                MediaAttachment.ticket_id.is_(None),
            ),
        )
        .order_by(Conversation.created_at)
        .limit(limit)
    ).scalars().all()
    result.conversations += len(conversation_ids)
    if dry_run or not conversation_ids:
        return
    actors = db.execute(
        select(Conversation.channel, Conversation.external_user_id).where(
            Conversation.id.in_(conversation_ids)
        )
    ).all()
    hold_ids = db.execute(
        select(LegalHold.id).where(
            LegalHold.subject_type == "conversation", LegalHold.subject_id.in_(conversation_ids)
        )
    ).scalars().all()
    db.execute(
        update(Ticket)
        .where(Ticket.conversation_id.in_(conversation_ids))
        .values(conversation_id=None)
    )
    db.execute(
        update(MediaAttachment)
        .where(MediaAttachment.conversation_id.in_(conversation_ids))
        .values(conversation_id=None)
    )
    db.execute(
        delete(AuditLog).where(
            or_(
                (AuditLog.subject_type == "conversation")
                & AuditLog.subject_id.in_(conversation_ids),
                (AuditLog.subject_type == "legal_hold") & AuditLog.subject_id.in_(hold_ids),
                AuditLog.actor.in_([f"{channel}:{external_id}" for channel, external_id in actors]),
            ),
        )
    )
    db.execute(
        delete(LegalHold).where(
            LegalHold.subject_type == "conversation", LegalHold.subject_id.in_(conversation_ids)
        )
    )
    db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
    db.commit()


def _expire_tickets(
    db: Session,
    cutoff: datetime,
    now: datetime,
    limit: int,
    dry_run: bool,
    storage: PrivateObjectStore | None,
    result: RetentionResult,
) -> None:
    tickets = db.execute(
        select(Ticket)
        .where(
            Ticket.status == "closed",
            Ticket.closed_at.is_not(None),
            Ticket.closed_at <= cutoff,
            ~_active_hold("ticket", Ticket.id, now),
        )
        .order_by(Ticket.closed_at)
        .limit(limit)
    ).scalars().all()
    for ticket in tickets:
        attachments = list(ticket.attachments)
        result.tickets += 1
        result.attachments += len(attachments)
        result.objects += sum(bool(item.object_key) for item in attachments) if dry_run else 0
        if dry_run:
            continue
        failed = False
        for attachment in attachments:
            if not _delete_attachment_object(
                db,
                attachment,
                storage,
                result,
                subject_type="ticket",
                subject_id=ticket.id,
            ):
                failed = True
                break
        if failed:
            continue
        attachment_ids = [item.id for item in attachments]
        notification_ids = db.execute(
            select(SupportNotification.id).where(SupportNotification.ticket_id == ticket.id)
        ).scalars().all()
        hold_ids = db.execute(
            select(LegalHold.id).where(
                LegalHold.subject_type == "ticket", LegalHold.subject_id == ticket.id
            )
        ).scalars().all()
        db.execute(delete(TicketNote).where(TicketNote.ticket_id == ticket.id))
        db.execute(delete(SupportNotification).where(SupportNotification.ticket_id == ticket.id))
        if attachment_ids:
            db.execute(delete(MediaAttachment).where(MediaAttachment.id.in_(attachment_ids)))
        db.execute(
            delete(AuditLog).where(
                or_(
                    (AuditLog.subject_type == "ticket") & (AuditLog.subject_id == ticket.id),
                    AuditLog.details["ticket_id"].as_string() == ticket.id,
                    AuditLog.details["ticket"].as_string() == ticket.public_id,
                    AuditLog.details["public_id"].as_string() == ticket.public_id,
                    (AuditLog.subject_type == "attachment")
                    & AuditLog.subject_id.in_(attachment_ids),
                    AuditLog.details["attachment_id"].as_string().in_(attachment_ids),
                    (AuditLog.subject_type == "notification")
                    & AuditLog.subject_id.in_(notification_ids),
                    AuditLog.details["notification_id"].as_string().in_(notification_ids),
                    (AuditLog.subject_type == "legal_hold") & AuditLog.subject_id.in_(hold_ids),
                )
            )
        )
        db.execute(
            delete(LegalHold).where(
                LegalHold.subject_type == "ticket", LegalHold.subject_id == ticket.id
            )
        )
        db.execute(delete(Ticket).where(Ticket.id == ticket.id))
        db.commit()


def run_retention(
    db: Session,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    storage: PrivateObjectStore | None = None,
    settings: Settings | None = None,
) -> RetentionResult:
    settings = settings or get_settings()
    now = now or datetime.utcnow()
    result = RetentionResult()
    chat_cutoff = now - timedelta(days=settings.chat_log_retention_days)
    ticket_cutoff = _three_year_cutoff(now)

    _expire_unlinked_media(
        db, chat_cutoff, now, settings.retention_batch_size, dry_run, storage, result
    )
    _expire_chats(db, chat_cutoff, now, settings.retention_batch_size, dry_run, result)
    _expire_tickets(
        db, ticket_cutoff, now, settings.retention_batch_size, dry_run, storage, result
    )
    db.add(
        AuditLog(
            actor="retention-worker",
            event_type="retention_dry_run" if dry_run else "retention_run_completed",
            details=asdict(result),
        )
    )
    db.commit()
    if result.failures:
        logger.error("retention run completed with failures", extra={"failures": result.failures})
    return result
