import hmac
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, WhatsAppInboundMessage, WhatsAppOutboundMessage
from app.schemas import ChatRequest, MetaWebhookResult
from app.security import verify_meta_signature
from app.services.chatbot import chatbot_service
from app.services.pii import redact_sensitive

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])


@router.get("", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    settings = get_settings()
    if hub_mode == "subscribe" and hmac.compare_digest(
        hub_verify_token, settings.meta_verify_token
    ):
        return hub_challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("", response_model=MetaWebhookResult)
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> MetaWebhookResult:
    raw_body = await request.body()
    if not verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook object"
        )

    processed = duplicates = 0
    try:
        status_updates = _apply_status_updates(db, payload)
        for item in extract_messages(payload):
            inbound = _reserve_inbound(db, item)
            if inbound is None:
                duplicates += 1
                continue
            response = chatbot_service.handle(
                db,
                ChatRequest(
                    channel="whatsapp",
                    external_user_id=item["sender"],
                    text=item["text"],
                    user_role="unknown",
                ),
                request.client.host if request.client else None,
                commit=False,
            )
            db.add(
                WhatsAppOutboundMessage(
                    inbound_message_id=inbound.id,
                    recipient=item["sender"],
                    body=_whatsapp_text(response.answer),
                )
            )
            processed += 1

        db.add(
            AuditLog(
                actor="meta-webhook",
                event_type="meta_webhook_received",
                ip_address=request.client.host if request.client else None,
                details={
                    "message_count": processed,
                    "duplicate_count": duplicates,
                    "status_update_count": status_updates,
                },
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return MetaWebhookResult(
        ok=True,
        processed=processed,
        duplicates=duplicates,
        status_updates=status_updates,
    )


def extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    expected_phone_number_id = get_settings().meta_phone_number_id
    messages: list[dict[str, str]] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value", {})
            phone_number_id = str(value.get("metadata", {}).get("phone_number_id", ""))
            if expected_phone_number_id and phone_number_id != expected_phone_number_id:
                continue
            for message in value.get("messages", []):
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue
                text = message.get("text", {}).get("body")
                sender = message.get("from")
                message_id = message.get("id")
                if text and sender and message_id and phone_number_id:
                    messages.append(
                        {
                            "id": str(message_id),
                            "sender": str(sender),
                            "phone_number_id": phone_number_id,
                            "text": str(text),
                            "type": "text",
                            "timestamp": str(message.get("timestamp", "")),
                            "context_message_id": str(
                                message.get("context", {}).get("id", "")
                            ),
                        }
                    )
    return messages


def _reserve_inbound(db: Session, item: dict[str, str]) -> WhatsAppInboundMessage | None:
    if (
        db.query(WhatsAppInboundMessage)
        .filter_by(provider_message_id=item["id"])
        .one_or_none()
    ):
        return None
    inbound = WhatsAppInboundMessage(
        provider_message_id=item["id"],
        sender=item["sender"],
        phone_number_id=item["phone_number_id"],
        message_type=item["type"],
        payload={
            "timestamp": item["timestamp"],
            "context_message_id": item["context_message_id"],
            "text": redact_sensitive(item["text"]).text,
        },
    )
    db.add(inbound)
    db.flush()
    return inbound


def _apply_status_updates(db: Session, payload: dict[str, Any]) -> int:
    updated = 0
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value", {})
            for item in value.get("statuses", []):
                if not isinstance(item, dict):
                    continue
                message = (
                    db.query(WhatsAppOutboundMessage)
                    .filter_by(provider_message_id=str(item.get("id", "")))
                    .one_or_none()
                )
                delivery_status = item.get("status")
                if not message or delivery_status not in {"sent", "delivered", "read", "failed"}:
                    continue
                event_time = _provider_timestamp(item.get("timestamp"))
                if delivery_status == "sent":
                    message.sent_at = message.sent_at or event_time
                elif delivery_status == "delivered" and message.status not in {
                    "read",
                    "failed",
                }:
                    message.status = "delivered"
                    message.delivered_at = event_time
                elif delivery_status == "read" and message.status != "failed":
                    message.status = "read"
                    message.read_at = event_time
                elif delivery_status == "failed" and message.status not in {
                    "delivered",
                    "read",
                }:
                    message.status = "failed"
                    message.failed_at = event_time
                    errors = item.get("errors") or []
                    if errors and isinstance(errors[0], dict):
                        message.last_error_code = str(errors[0].get("code", "unknown"))[:80]
                updated += 1
    return updated


def _provider_timestamp(value: Any) -> datetime:
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError, OverflowError):
        return datetime.utcnow()


def _whatsapp_text(text: str) -> str:
    # ponytail: one queued reply preserves the one-inbound/one-outbound contract. If approved
    # answers can exceed Meta's 4,096-character text limit, replace this with ordered segments.
    return text if len(text) <= 4096 else f"{text[:4093]}..."
