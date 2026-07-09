import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog
from app.schemas import ChatRequest, MetaWebhookResult
from app.security import verify_meta_signature
from app.services.chatbot import chatbot_service

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])


@router.get("")
def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        return hub_challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("", response_model=MetaWebhookResult)
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> MetaWebhookResult:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_meta_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    extracted = extract_messages(payload)
    responses: list[dict[str, Any]] = []
    for item in extracted:
        chat_request = ChatRequest(
            channel=item["channel"],
            external_user_id=item["external_user_id"],
            text=item["text"],
            user_role="unknown",
        )
        response = chatbot_service.handle(db, chat_request, request.client.host if request.client else None)
        responses.append(
            {
                "channel": item["channel"],
                "external_user_id": item["external_user_id"],
                "answer": response.answer,
                "ticket": response.ticket.model_dump() if response.ticket else None,
            }
        )

    db.add(
        AuditLog(
            actor="meta-webhook",
            event_type="meta_webhook_received",
            ip_address=request.client.host if request.client else None,
            details={"message_count": len(extracted), "dry_run_responses": responses},
        )
    )
    db.commit()
    return MetaWebhookResult(ok=True, processed=len(extracted), responses=responses)


def extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                text = message.get("text", {}).get("body")
                sender = message.get("from")
                if text and sender:
                    messages.append(
                        {"channel": "whatsapp", "external_user_id": str(sender), "text": str(text)}
                    )
        for messaging_event in entry.get("messaging", []):
            sender = messaging_event.get("sender", {}).get("id")
            text = messaging_event.get("message", {}).get("text")
            if sender and text:
                messages.append(
                    {"channel": "instagram", "external_user_id": str(sender), "text": str(text)}
                )
    return messages

