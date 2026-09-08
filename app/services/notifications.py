import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AuditLog, SupportNotification
from app.services.whatsapp import MetaSendError, MetaWhatsAppClient


LANGUAGE_CODES = {"en": "en", "ms": "ms", "zh": "zh_CN"}


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, recipient: str, event_type: str, payload: dict) -> None:
        public_id = str(payload["public_id"])
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_address
        message["To"] = recipient
        message["Subject"] = f"DUDU support: {event_type.replace('_', ' ')} — {public_id}"
        message.set_content(
            f"Ticket {public_id}\nPriority: {payload.get('urgency', 'normal')}\n"
            "Sign in to the support inbox for details."
        )
        with smtplib.SMTP_SSL(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=10,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)


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
    notification = (
        db.query(SupportNotification)
        .filter(
            SupportNotification.status.in_(("pending", "retry")),
            SupportNotification.next_attempt_at <= now,
        )
        .order_by(SupportNotification.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not notification:
        return False

    notification.attempts += 1
    try:
        if notification.channel == "email":
            (email_client or EmailClient(settings)).send(
                notification.recipient, notification.event_type, notification.payload
            )
        elif notification.channel == "whatsapp":
            if not settings.meta_send_enabled:
                raise RuntimeError("Meta send kill switch is disabled")
            language = str(notification.payload.get("language", "en"))
            template = settings.notification_template_names[
                f"{notification.event_type}.{language}"
            ]
            parameters = [str(notification.payload["public_id"])]
            parameters.append(
                str(notification.payload.get("status") or notification.payload.get("urgency"))
            )
            (whatsapp_client or MetaWhatsAppClient(settings)).send_template(
                notification.recipient,
                template,
                LANGUAGE_CODES[language],
                parameters,
            )
        else:
            raise ValueError("unsupported notification channel")
        notification.status = "sent"
        notification.sent_at = now
        notification.last_error = None
        event_type = "support_notification_sent"
    except (
        KeyError,
        ValueError,
        RuntimeError,
        OSError,
        smtplib.SMTPException,
        MetaSendError,
    ) as exc:
        notification.last_error = type(exc).__name__
        if notification.attempts < settings.notification_max_attempts:
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
