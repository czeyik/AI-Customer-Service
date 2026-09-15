import hmac
import json
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import get_db
from app.models import (
    AuditLog,
    Conversation,
    MediaAttachment,
    SupportNotification,
    WhatsAppInboundMessage,
    WhatsAppOutboundMessage,
)
from app.schemas import AttachmentPayload, MetaWebhookResult
from app.security import verify_meta_signature
from app.services.dialogue import ensure_evidence_group, store_dialogue_data
from app.services.guardrails import assess_message
from app.services.pii import redact_sensitive
from app.services.rate_limit import rate_limiter

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])
MAX_WEBHOOK_BYTES = 1_000_000


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
    raw_body = await _bounded_body(request)
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

    processed = duplicates = limited = 0
    try:
        status_updates = _apply_status_updates(db, payload)
        for item in extract_messages(payload):
            inbound = _reserve_inbound(db, item)
            if inbound is None:
                duplicates += 1
                continue
            beta_counts = _consume_public_beta_limits(db, item["sender"])
            if beta_counts is None:
                limited += 1
                inbound.status = "done"
                if _allow_capacity_notice(db, item["sender"]):
                    db.add(
                        WhatsAppOutboundMessage(
                            inbound_message_id=inbound.id,
                            recipient=item["sender"],
                            body=_capacity_message(),
                        )
                    )
                processed += 1
                continue
            _record_volume_warnings(db, beta_counts)
            # Persist first; the existing worker interprets each sender's queue in order.
            processed += 1

        db.add(
            AuditLog(
                actor="meta-webhook",
                event_type="meta_webhook_received",
                ip_address=request.client.host if request.client else None,
                details={
                    "message_count": processed,
                    "duplicate_count": duplicates,
                    "limited_count": limited,
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


def _consume_public_beta_limits(
    db: Session, sender: str, now: datetime | None = None
) -> list[int] | None:
    settings = get_settings()
    if not settings.public_beta_enabled:
        return []
    local_now = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo("Asia/Kuala_Lumpur")
    )
    if not settings.public_beta_start_date <= local_now.date() <= settings.public_beta_end_date:
        return None
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, local_now.tzinfo
    )
    beta_end = datetime.combine(
        settings.public_beta_end_date + timedelta(days=1), time.min, local_now.tzinfo
    )
    day_seconds = max(1, int((next_midnight - local_now).total_seconds()))
    beta_seconds = max(1, int((beta_end - local_now).total_seconds()))
    day = local_now.date().isoformat()
    return rate_limiter.allow_all(
        db,
        [
            (
                f"beta-minute:{sender}",
                settings.rate_limit_messages_per_minute,
                60,
            ),
            (
                f"beta-user-day:{day}:{sender}",
                settings.public_beta_messages_per_user_day,
                day_seconds,
            ),
            (
                f"beta-global-day:{day}",
                settings.public_beta_messages_per_day,
                day_seconds,
            ),
            (
                "beta-total:2026-09-10:2026-09-30",
                settings.public_beta_messages_total,
                beta_seconds,
            ),
        ],
        max_keys=settings.rate_limit_max_keys,
        now=local_now.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _allow_capacity_notice(db: Session, sender: str, now: datetime | None = None) -> bool:
    local_now = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo("Asia/Kuala_Lumpur")
    )
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, local_now.tzinfo
    )
    return rate_limiter.allow_all(
        db,
        [
            (
                f"beta-capacity-notice:{local_now.date().isoformat()}:{sender}",
                1,
                max(1, int((next_midnight - local_now).total_seconds())),
            )
        ],
        max_keys=get_settings().rate_limit_max_keys,
        now=local_now.astimezone(timezone.utc).replace(tzinfo=None),
    ) is not None


def _record_volume_warnings(db: Session, counts: list[int]) -> None:
    if not counts:
        return
    settings = get_settings()
    for scope, count, limit in (
        ("daily", counts[2], settings.public_beta_messages_per_day),
        ("total", counts[3], settings.public_beta_messages_total),
    ):
        for percentage in (80, 90):
            if count == (limit * percentage + 99) // 100:
                db.add(
                    AuditLog(
                        actor="system",
                        event_type="public_beta_volume_warning",
                        details={
                            "scope": scope,
                            "count": count,
                            "limit": limit,
                            "percentage": percentage,
                        },
                    )
                )


def _capacity_message() -> str:
    return (
        "The public beta message limit has been reached. Please try again after midnight "
        "Malaysia time. If the beta has ended, please wait for the next service update.\n\n"
        "Had mesej beta awam telah dicapai. Sila cuba lagi selepas tengah malam waktu Malaysia. "
        "Jika beta telah tamat, sila tunggu kemas kini perkhidmatan seterusnya.\n\n"
        "公测消息限额已满。请在马来西亚时间午夜后重试；如果公测已经结束，请等待下一次服务通知。"
    )


async def _bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Webhook payload too large",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length"
            ) from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook payload too large",
            )
    return bytes(body)


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
                if not isinstance(message, dict) or message.get("type") not in {
                    "text",
                    "image",
                    "video",
                }:
                    continue
                message_type = message["type"]
                content = message.get(message_type, {})
                text = content.get("body") if message_type == "text" else content.get("caption", "")
                sender = message.get("from")
                message_id = message.get("id")
                media_id = content.get("id") if message_type != "text" else ""
                if sender and message_id and phone_number_id and (text or media_id) and (
                    str(sender).isdigit() and 8 <= len(str(sender)) <= 15
                    and len(str(message_id)) <= 255 and len(str(text or "")) <= 4096
                    and len(str(media_id or "")) <= 255
                    and len(str(content.get("mime_type", ""))) <= 120
                    and len(str(content.get("sha256", ""))) <= 128
                ):
                    messages.append(
                        {
                            "id": str(message_id),
                            "sender": str(sender),
                            "phone_number_id": phone_number_id,
                            "text": str(text or ""),
                            "type": str(message_type),
                            "media_id": str(media_id or ""),
                            "mime_type": str(content.get("mime_type", "")),
                            "sha256": str(content.get("sha256", "")),
                            "timestamp": str(message.get("timestamp", "")) if str(message.get("timestamp", "")).isdigit() else "",
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
            "mime_type": item.get("mime_type", ""),
            "sha256": item.get("sha256", ""),
            "timestamp": item["timestamp"],
            "context_message_id": item["context_message_id"],
            "text": redact_sensitive(item["text"]).text,
            "media_id": item.get("media_id", ""),
        },
    )
    try:
        with db.begin_nested():
            db.add(inbound)
            db.flush()
    except IntegrityError:
        return None
    return inbound


def _queue_media(
    db: Session, inbound: WhatsAppInboundMessage, item: dict[str, str]
) -> str:
    conversation = (
        db.query(Conversation)
        .filter_by(channel="whatsapp", external_user_id=item["sender"])
        .order_by(Conversation.created_at.desc())
        .with_for_update()
        .first()
    )
    if not conversation:
        conversation = Conversation(channel="whatsapp", external_user_id=item["sender"])
        db.add(conversation)
        db.flush()
    dialogue, evidence_group = ensure_evidence_group(conversation)
    caption = redact_sensitive(item["text"]).text
    assessment = assess_message(
        caption,
        [
            AttachmentPayload(
                filename=f"whatsapp-{item['type']}",
                mime_type=item["mime_type"],
                description=caption,
            )
        ],
    )
    rejected = "sensitive_attachment_rejected" in assessment.flags
    attachment = MediaAttachment(
        provider_media_id=item["media_id"],
        inbound_message_id=inbound.id,
        conversation_id=conversation.id,
        evidence_group=evidence_group,
        media_type=item["type"],
        declared_mime_type=item["mime_type"],
        provider_sha256=item["sha256"] or None,
        status="rejected" if rejected else "queued",
        failure_code="sensitive_content" if rejected else None,
    )
    db.add(attachment)
    db.flush()
    store_dialogue_data(conversation, dialogue, evidence_changed=True)
    if rejected:
        status = {
            "en": "I can’t accept sensitive documents or payment information. "
            "Please send only relevant ride pictures or videos.",
            "ms": "Saya tidak boleh menerima dokumen sensitif atau maklumat pembayaran. "
            "Sila hantar gambar atau video perjalanan yang berkaitan sahaja.",
            "zh": "我无法接收敏感证件或付款资料。"
            "请仅发送与行程有关的图片或视频。",
        }[conversation.preferred_language]
    else:
        status = {
            "en": "Thanks — your attachment is quarantined for security checks. "
            "It will be added to your ticket only if it passes.",
            "ms": "Terima kasih — lampiran anda dikuarantin untuk pemeriksaan keselamatan. "
            "Ia hanya akan ditambah pada tiket jika lulus.",
            "zh": "谢谢——您的附件已隔离并接受安全检查。"
            "只有通过检查后才会加入工单。",
        }[conversation.preferred_language]
    return status


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
                provider_message_id = str(item.get("id", ""))
                delivery_status = item.get("status")
                if not provider_message_id or delivery_status not in {
                    "sent",
                    "delivered",
                    "read",
                    "failed",
                }:
                    continue
                callback_data = str(item.get("biz_opaque_callback_data", ""))
                message = (
                    db.query(WhatsAppOutboundMessage)
                    .filter_by(provider_message_id=provider_message_id)
                    .with_for_update()
                    .populate_existing()
                    .one_or_none()
                )
                notification = None
                if callback_data:
                    recipient_id = str(item.get("recipient_id", ""))
                    if not recipient_id:
                        continue
                    kind, separator, record_id = callback_data.partition(":")
                    if not separator or kind not in {"outbox", "notification"}:
                        continue
                    if kind == "outbox":
                        callback_message = (
                            db.query(WhatsAppOutboundMessage)
                            .filter_by(id=record_id)
                            .with_for_update()
                            .populate_existing()
                            .one_or_none()
                        )
                        if (
                            not callback_message
                            or callback_message.attempts < 1
                            or callback_message.status
                            not in {"uncertain", "sent", "delivered", "read", "failed"}
                            or recipient_id.lstrip("+")
                            != callback_message.recipient.lstrip("+")
                            or (message and message.id != callback_message.id)
                            or (
                                callback_message.provider_message_id
                                and callback_message.provider_message_id
                                != provider_message_id
                            )
                        ):
                            continue
                        message = callback_message
                    else:
                        notification = (
                            db.query(SupportNotification)
                            .filter_by(id=record_id, channel="whatsapp")
                            .with_for_update()
                            .populate_existing()
                            .one_or_none()
                        )
                        stored_provider_id = (
                            str(notification.payload.get("provider_message_id", ""))
                            if notification
                            else ""
                        )
                        if (
                            not notification
                            or notification.attempts < 1
                            or notification.status
                            not in {"uncertain", "sent", "delivered", "read", "failed"}
                            or recipient_id.lstrip("+")
                            != notification.recipient.lstrip("+")
                            or message
                            or (
                                stored_provider_id
                                and stored_provider_id != provider_message_id
                            )
                        ):
                            continue
                target = message or notification
                if not target:
                    continue
                if callback_data:
                    if message:
                        message.provider_message_id = provider_message_id
                    else:
                        notification.payload = dict(notification.payload) | {
                            "provider_message_id": provider_message_id
                        }
                event_time = _provider_timestamp(item.get("timestamp"))
                if delivery_status == "sent":
                    if target.status == "uncertain":
                        target.status = "sent"
                    target.sent_at = target.sent_at or event_time
                elif delivery_status == "delivered" and target.status not in {
                    "read",
                    "failed",
                }:
                    target.status = "delivered"
                    if message:
                        message.delivered_at = event_time
                elif delivery_status == "read" and target.status != "failed":
                    target.status = "read"
                    if message:
                        message.read_at = event_time
                elif delivery_status == "failed" and target.status not in {
                    "delivered",
                    "read",
                }:
                    target.status = "failed"
                    if message:
                        message.failed_at = event_time
                    errors = item.get("errors") or []
                    if errors and isinstance(errors[0], dict):
                        error_code = str(errors[0].get("code", "unknown"))[:80]
                        if message:
                            message.last_error_code = error_code
                        else:
                            notification.last_error = error_code
                db.flush()
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
