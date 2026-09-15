import json
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session, aliased

from app.config import Settings, get_settings
from app.models import AuditLog, WhatsAppOutboundMessage

RETRYABLE_META_ERROR_CODES = {
    "1",
    "2",
    "4",
    "17",
    "80007",
    "130429",
    "131000",
    "131016",
    "131056",
    "133004",
}


class MetaSendError(Exception):
    def __init__(self, code: str, retryable: bool, *, uncertain: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


class MetaWhatsAppClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send_text(
        self, recipient: str, body: str, *, callback_data: str | None = None
    ) -> str:
        return self._send(
            recipient,
            {"type": "text", "text": {"preview_url": False, "body": body}},
            callback_data=callback_data,
        )

    def send_template(
        self,
        recipient: str,
        template_name: str,
        language: str,
        parameters: list[str],
        *,
        callback_data: str | None = None,
    ) -> str:
        return self._send(
            recipient,
            {
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": parameter} for parameter in parameters
                            ],
                        }
                    ],
                },
            },
            callback_data=callback_data,
        )

    def _send(
        self,
        recipient: str,
        message: dict[str, Any],
        *,
        callback_data: str | None = None,
    ) -> str:
        url = (
            f"https://graph.facebook.com/{self.settings.meta_graph_api_version}/"
            f"{self.settings.meta_phone_number_id}/messages"
        )
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            **message,
        }
        if callback_data:
            body["biz_opaque_callback_data"] = callback_data
        payload = json.dumps(body).encode()
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.meta_access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.settings.meta_send_timeout_seconds) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            code, explicit_rejection = _meta_error_code(exc)
            if exc.code >= 500 and not explicit_rejection:
                raise MetaSendError(code, False, uncertain=True) from exc
            retryable = exc.code == 429 or exc.code >= 500 or code in RETRYABLE_META_ERROR_CODES
            raise MetaSendError(code, retryable) from exc
        except (
            TimeoutError,
            URLError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise MetaSendError(type(exc).__name__, False, uncertain=True) from exc

        try:
            provider_message_id = result["messages"][0]["id"]
            if not isinstance(provider_message_id, str) or not provider_message_id:
                raise TypeError("invalid message id")
            return provider_message_id
        except (KeyError, IndexError, TypeError) as exc:
            raise MetaSendError("invalid_meta_response", False, uncertain=True) from exc


def _meta_error_code(exc: HTTPError) -> tuple[str, bool]:
    try:
        payload = json.loads(exc.read())
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("code") is not None:
                return str(error["code"])[:80], True
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        pass
    return f"http_{exc.code}", False


def process_next_outbound(
    db: Session,
    client: MetaWhatsAppClient | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    if not settings.meta_send_enabled:
        return False

    now = now or datetime.utcnow()
    older = aliased(WhatsAppOutboundMessage)
    message = (
        db.query(WhatsAppOutboundMessage)
        .filter(
            WhatsAppOutboundMessage.status.in_(("queued", "retry")),
            WhatsAppOutboundMessage.next_attempt_at <= now,
            ~db.query(older.id).filter(
                older.recipient == WhatsAppOutboundMessage.recipient,
                older.status.in_(("queued", "retry", "uncertain")),
                (older.created_at < WhatsAppOutboundMessage.created_at) |
                ((older.created_at == WhatsAppOutboundMessage.created_at) & (older.id < WhatsAppOutboundMessage.id)),
            ).exists(),
        )
        .order_by(WhatsAppOutboundMessage.created_at, WhatsAppOutboundMessage.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not message:
        return False

    outbound_id = message.id
    recipient = message.recipient
    body = message.body
    message.attempts += 1
    message.status = "uncertain"
    db.add(
        AuditLog(
            actor="whatsapp-worker",
            event_type="whatsapp_message_send_started",
            details={"attempt": message.attempts, "outbound_id": outbound_id},
        )
    )
    db.commit()

    try:
        provider_message_id = (client or MetaWhatsAppClient(settings)).send_text(
            recipient,
            body,
            callback_data=f"outbox:{outbound_id}",
        )
        message = (
            db.query(WhatsAppOutboundMessage)
            .filter_by(id=outbound_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        if not message.provider_message_id:
            message.provider_message_id = provider_message_id
        if message.status == "uncertain":
            message.status = "sent"
            message.sent_at = now
            message.last_error_code = None
            event_type = "whatsapp_message_sent"
        else:
            event_type = "whatsapp_message_status_reconciled"
    except MetaSendError as exc:
        message = (
            db.query(WhatsAppOutboundMessage)
            .filter_by(id=outbound_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        if message.status != "uncertain":
            event_type = "whatsapp_message_status_reconciled"
        elif exc.uncertain:
            message.last_error_code = exc.code
            event_type = "whatsapp_message_send_uncertain"
        else:
            message.last_error_code = exc.code
            if exc.retryable and message.attempts < settings.meta_send_max_attempts:
                message.status = "retry"
                message.next_attempt_at = now + timedelta(seconds=2**message.attempts)
                event_type = "whatsapp_message_retry_scheduled"
            else:
                message.status = "dead_letter"
                message.failed_at = now
                event_type = "whatsapp_message_dead_lettered"

    db.add(
        AuditLog(
            actor="whatsapp-worker",
            event_type=event_type,
            details={"attempt": message.attempts, "error_code": message.last_error_code},
        )
    )
    db.commit()
    return True


def process_outbox(db: Session, limit: int = 20) -> int:
    processed = 0
    while processed < limit and process_next_outbound(db):
        processed += 1
    return processed
