import json
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

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
    def __init__(self, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class MetaWhatsAppClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send_text(self, recipient: str, body: str) -> str:
        return self._send(
            recipient,
            {"type": "text", "text": {"preview_url": False, "body": body}},
        )

    def send_template(
        self, recipient: str, template_name: str, language: str, parameters: list[str]
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
        )

    def _send(self, recipient: str, message: dict[str, Any]) -> str:
        url = (
            f"https://graph.facebook.com/{self.settings.meta_graph_api_version}/"
            f"{self.settings.meta_phone_number_id}/messages"
        )
        payload = json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                **message,
            }
        ).encode()
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
            code = _meta_error_code(exc)
            retryable = exc.code == 429 or exc.code >= 500 or code in RETRYABLE_META_ERROR_CODES
            raise MetaSendError(code, retryable) from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise MetaSendError(type(exc).__name__, True) from exc

        try:
            return str(result["messages"][0]["id"])
        except (KeyError, IndexError, TypeError) as exc:
            raise MetaSendError("invalid_meta_response", True) from exc


def _meta_error_code(exc: HTTPError) -> str:
    try:
        payload: dict[str, Any] = json.loads(exc.read())
        return str(payload.get("error", {}).get("code", f"http_{exc.code}"))[:80]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return f"http_{exc.code}"


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
    message = (
        db.query(WhatsAppOutboundMessage)
        .filter(
            WhatsAppOutboundMessage.status.in_(("queued", "retry")),
            WhatsAppOutboundMessage.next_attempt_at <= now,
        )
        .order_by(WhatsAppOutboundMessage.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not message:
        return False

    message.attempts += 1
    try:
        message.provider_message_id = (client or MetaWhatsAppClient(settings)).send_text(
            message.recipient, message.body
        )
        message.status = "sent"
        message.sent_at = now
        message.last_error_code = None
        event_type = "whatsapp_message_sent"
    except MetaSendError as exc:
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
