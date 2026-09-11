"""Durable WhatsApp interpretation using short claims and the existing worker."""
import uuid
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import aliased

from app.models import WhatsAppInboundMessage, MediaAttachment
from app.schemas import ChatRequest
from app.services.chatbot import chatbot_service


def process_next_inbound(db, *, now=None):
    now = now or datetime.utcnow()
    older = aliased(WhatsAppInboundMessage)
    event = db.query(WhatsAppInboundMessage).filter(
        or_(WhatsAppInboundMessage.status == "queued",
            (WhatsAppInboundMessage.status == "processing") & (WhatsAppInboundMessage.lease_until < now)),
        ~db.query(older.id).filter(
            older.sender == WhatsAppInboundMessage.sender,
            older.status.in_(("queued", "processing")),
            (older.created_at < WhatsAppInboundMessage.created_at) |
            ((older.created_at == WhatsAppInboundMessage.created_at) & (older.id < WhatsAppInboundMessage.id)),
        ).exists(),
    ).order_by(WhatsAppInboundMessage.created_at, WhatsAppInboundMessage.id).with_for_update(skip_locked=True).first()
    if event is None:
        db.rollback()
        return False
    token = str(uuid.uuid4())
    event.status = "processing"
    event.claim_token = token
    event.lease_until = now + timedelta(seconds=60)
    event_id, sender, kind, payload = event.id, event.sender, event.message_type, dict(event.payload)
    provider_allowed = not payload.get("provider_attempted", False)
    event.payload = {**payload, "provider_attempted": True}
    db.commit()
    try:
        attachment_status = None
        if kind in {"image", "video"}:
            from app.routers.webhooks_meta import _queue_media
            if not db.query(MediaAttachment).filter_by(inbound_message_id=event_id).first():
                attachment_status = _queue_media(db, db.get(WhatsAppInboundMessage, event_id), {
                    "sender": sender, "type": kind, "text": payload.get("text", ""),
                    "media_id": payload.get("media_id", ""), "mime_type": payload.get("mime_type", ""),
                    "sha256": payload.get("sha256", ""),
                })
                db.commit()
        chatbot_service.handle(db, ChatRequest(
            channel="whatsapp", external_user_id=sender,
            text=payload.get("text") or "[attachment]",
        ), inbound_id=event_id, claim_token=token, attachment_status=attachment_status, provider_allowed=provider_allowed)
    except Exception as exc:
        db.rollback()
        # The persisted lease expires; other senders keep progressing. Never log SQL
        # exception text because it can include locally stored contact values.
        logging.getLogger(__name__).error("inbound_processing_failed error_type=%s", type(exc).__name__)
    return True


def process_inbox(session_factory, workers=4):
    from concurrent.futures import ThreadPoolExecutor

    def process(_):
        with session_factory() as db:
            return process_next_inbound(db)

    # Four concurrent provider requests bound memory and keep burst queue time below
    # the response budget; the SQL claim still serializes each individual sender.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(process, range(workers)))
