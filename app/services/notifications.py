import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr

from sqlalchemy.orm import Session, aliased

from app.config import Settings, get_settings
from app.models import AuditLog, SupportNotification
from app.services.whatsapp import MetaSendError, MetaWhatsAppClient


LANGUAGE_CODES = {"en": "en", "ms": "ms", "zh": "zh_CN"}


class EmailSendError(Exception):
    def __init__(self, code: str, *, uncertain: bool) -> None:
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self, recipient: str, event_type: str, payload: dict, *, message_id: str
    ) -> None:
        public_id = str(payload["public_id"])
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_address
        message["To"] = recipient
        message["Subject"] = f"DUDU support: {event_type.replace('_', ' ')} — {public_id}"
        domain = parseaddr(self.settings.smtp_from_address)[1].rpartition("@")[2] or "duducar.local"
        message["Message-ID"] = f"<support-notification.{message_id}@{domain}>"
        message.set_content(
            f"Ticket {public_id}\nPriority: {payload.get('urgency', 'normal')}\n"
            "Sign in to the support inbox for details."
        )
        send_started = False
        try:
            with smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=10,
                context=ssl.create_default_context(),
            ) as smtp:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                send_started = True
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailSendError(type(exc).__name__, uncertain=send_started) from exc


def process_next_notification(
    db: Session,
    *,
    settings: Settings | None = None,
    email_client: EmailClient | None = None,
    whatsapp_client: MetaWhatsAppClient | None = None,
    now: datetime | None = None,
) -> bool:
    settings = settings or get_settings()
    if not settings.notification_send_enabled:
        return False
    now = now or datetime.utcnow()
    older = aliased(SupportNotification)
    notification = (
        db.query(SupportNotification)
        .filter(
            SupportNotification.status.in_(("pending", "retry")),
            SupportNotification.next_attempt_at <= now,
            ~db.query(older.id).filter(
                older.recipient == SupportNotification.recipient,
                older.status.in_(("pending", "retry", "uncertain")),
                (older.created_at < SupportNotification.created_at)
                | (
                    (older.created_at == SupportNotification.created_at)
                    & (older.id < SupportNotification.id)
                ),
            ).exists(),
        )
        .order_by(SupportNotification.created_at, SupportNotification.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not notification:
        return False

    notification_id = notification.id
    recipient = notification.recipient
    channel = notification.channel
    event_name = notification.event_type
    payload = dict(notification.payload)
    notification.attempts += 1
    notification.status = "uncertain"
    db.add(
        AuditLog(
            actor="notification-worker",
            event_type="support_notification_send_started",
            subject_type="ticket",
            subject_id=notification.ticket_id,
            details={
                "notification_id": notification_id,
                "ticket_id": notification.ticket_id,
                "channel": channel,
                "attempt": notification.attempts,
            },
        )
    )
    db.commit()

    try:
        provider_message_id = None
        if channel == "email":
            (email_client or EmailClient(settings)).send(
                recipient, event_name, payload, message_id=notification_id
            )
        elif channel == "whatsapp":
            if not settings.meta_send_enabled:
                raise RuntimeError("Meta send kill switch is disabled")
            language = str(payload.get("language", "en"))
            template = settings.notification_template_names[
                f"{event_name}.{language}"
            ]
            parameters = [str(payload["public_id"])]
            parameters.append(
                str(payload.get("status") or payload.get("urgency"))
            )
            provider_message_id = (
                whatsapp_client or MetaWhatsAppClient(settings)
            ).send_template(
                recipient,
                template,
                LANGUAGE_CODES[language],
                parameters,
                callback_data=f"notification:{notification_id}",
            )
        else:
            raise ValueError("unsupported notification channel")
        notification = (
            db.query(SupportNotification)
            .filter_by(id=notification_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        if provider_message_id:
            current_payload = dict(notification.payload)
            if not current_payload.get("provider_message_id"):
                notification.payload = current_payload | {
                    "provider_message_id": provider_message_id
                }
        if notification.status == "uncertain":
            notification.status = "sent"
            notification.sent_at = now
            notification.last_error = None
            event_type = "support_notification_sent"
        else:
            event_type = "support_notification_status_reconciled"
    except (EmailSendError, MetaSendError) as exc:
        notification = (
            db.query(SupportNotification)
            .filter_by(id=notification_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        uncertain = exc.uncertain
        retryable = not isinstance(exc, MetaSendError) or exc.retryable
        if notification.status != "uncertain":
            event_type = "support_notification_status_reconciled"
        else:
            notification.last_error = getattr(exc, "code", type(exc).__name__)
            if uncertain:
                event_type = "support_notification_send_uncertain"
            elif retryable and notification.attempts < settings.notification_max_attempts:
                notification.status = "retry"
                notification.next_attempt_at = now + timedelta(seconds=2**notification.attempts)
                event_type = "support_notification_retry_scheduled"
            else:
                notification.status = "dead_letter"
                event_type = "support_notification_dead_lettered"
    except (
        KeyError,
        ValueError,
        RuntimeError,
        OSError,
        smtplib.SMTPException,
    ) as exc:
        notification = (
            db.query(SupportNotification)
            .filter_by(id=notification_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        if notification.status != "uncertain":
            event_type = "support_notification_status_reconciled"
        else:
            notification.last_error = type(exc).__name__
            if isinstance(exc, (OSError, smtplib.SMTPException)):
                event_type = "support_notification_send_uncertain"
            elif notification.attempts < settings.notification_max_attempts:
                notification.status = "retry"
                notification.next_attempt_at = now + timedelta(seconds=2**notification.attempts)
                event_type = "support_notification_retry_scheduled"
            else:
                notification.status = "dead_letter"
                event_type = "support_notification_dead_lettered"

    db.add(
        AuditLog(
            actor="notification-worker",
            event_type=event_type,
            subject_type="ticket",
            subject_id=notification.ticket_id,
            details={
                "notification_id": notification.id,
                "ticket_id": notification.ticket_id,
                "channel": notification.channel,
                "attempt": notification.attempts,
            },
        )
    )
    db.commit()
    return True


def process_notifications(db: Session, limit: int = 20) -> int:
    processed = 0
    while processed < limit and process_next_notification(db):
        processed += 1
    return processed
