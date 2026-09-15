import asyncio
import hashlib
import hmac
import io
import json
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    Base,
    Message,
    RateLimitBucket,
    SupportNotification,
    Ticket,
    WhatsAppInboundMessage,
    WhatsAppOutboundMessage,
)
from app.services.inbound import process_next_inbound
from app.routers.webhooks_meta import (
    MAX_WEBHOOK_BYTES,
    _bounded_body,
    _consume_public_beta_limits,
    receive_webhook,
    router as webhook_router,
)
from app.security import verify_meta_signature
from app.services import whatsapp as whatsapp_module
from app.services.whatsapp import MetaSendError, process_next_outbound


APP_SECRET = "test-meta-app-secret-at-least-24"
PHONE_NUMBER_ID = "123456789"


@pytest.fixture()
def db_session(monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_PHONE_NUMBER_ID", PHONE_NUMBER_ID)
    monkeypatch.setenv("META_VERIFY_TOKEN", "test-verify-token-at-least-32-characters")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.close()
        get_settings.cache_clear()


def payload(message_id: str, text: str, sender: str = "60108865432") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-id",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "messages": [
                                {
                                    "from": sender,
                                    "id": message_id,
                                    "timestamp": "1788596000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def call_webhook(db: Session, data: dict, signature: str | None = None):
    body = json.dumps(data, separators=(",", ":")).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = [(b"x-hub-signature-256", (signature or f"sha256={digest}").encode())]
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {"type": "http", "method": "POST", "path": "/webhooks/meta", "headers": headers},
        receive,
    )
    result = asyncio.run(receive_webhook(request, db))
    while process_next_inbound(db):
        pass
    return result


def test_webhook_rejects_oversized_body_before_reading_it() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/meta",
            "headers": [(b"content-length", str(MAX_WEBHOOK_BYTES + 1).encode())],
        }
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_bounded_body(request))
    assert exc.value.status_code == 413


def test_invalid_signature_is_rejected_before_writes(db_session: Session) -> None:
    with pytest.raises(HTTPException) as exc:
        call_webhook(db_session, payload("wamid.invalid", "Hello"), "sha256=bad")

    assert exc.value.status_code == 403
    assert db_session.query(WhatsAppInboundMessage).count() == 0


def test_missing_app_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    get_settings.cache_clear()

    assert not verify_meta_signature(b"{}", None)
    assert not verify_meta_signature(b"{}", "sha256=anything")


def test_callback_verification_returns_plain_challenge(db_session: Session) -> None:
    app = FastAPI()
    app.include_router(webhook_router)
    sent = []

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/webhooks/meta",
        "raw_path": b"/webhooks/meta",
        "query_string": (
            b"hub.mode=subscribe&hub.verify_token="
            b"test-verify-token-at-least-32-characters&hub.challenge=123456"
        ),
        "headers": [(b"host", b"callback.example")],
        "client": ("127.0.0.1", 12345),
        "server": ("callback.example", 443),
    }
    asyncio.run(app(scope, receive, send))

    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    assert start["status"] == 200
    assert (b"content-type", b"text/plain; charset=utf-8") in start["headers"]
    assert body == b"123456"


def test_message_id_is_processed_and_queued_exactly_once(db_session: Session) -> None:
    first = call_webhook(db_session, payload("wamid.once", "I need a human"))
    duplicate = call_webhook(db_session, payload("wamid.once", "I need a human"))

    assert (first.processed, first.duplicates) == (1, 0)
    assert (duplicate.processed, duplicate.duplicates) == (0, 1)
    assert db_session.query(WhatsAppInboundMessage).count() == 1
    assert db_session.query(WhatsAppOutboundMessage).count() == 1
    assert db_session.query(Message).count() == 2
    assert db_session.query(WhatsAppInboundMessage).one().payload["text"] == "I need a human"
    audit = db_session.query(AuditLog).filter_by(event_type="meta_webhook_received").first()
    assert "I need a human" not in json.dumps(audit.details)
    assert "60108865432" not in json.dumps(audit.details)


def test_public_beta_limits_are_calendar_aligned_and_atomic(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLIC_BETA_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BETA_MESSAGES_PER_USER_DAY", "2")
    monkeypatch.setenv("PUBLIC_BETA_MESSAGES_PER_DAY", "3")
    monkeypatch.setenv("PUBLIC_BETA_MESSAGES_TOTAL", "4")
    get_settings.cache_clear()
    first_day = datetime(2026, 9, 10, 8, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))

    assert _consume_public_beta_limits(db_session, "user-a", first_day) == [1, 1, 1, 1]
    assert _consume_public_beta_limits(db_session, "user-a", first_day) == [2, 2, 2, 2]
    assert _consume_public_beta_limits(db_session, "user-a", first_day) is None
    assert _consume_public_beta_limits(db_session, "user-b", first_day) == [1, 1, 3, 3]
    assert _consume_public_beta_limits(db_session, "user-b", first_day) is None

    second_day = first_day + timedelta(days=1)
    assert _consume_public_beta_limits(db_session, "user-b", second_day) == [1, 1, 1, 4]
    assert _consume_public_beta_limits(db_session, "user-c", second_day) is None
    db_session.commit()

    assert all(
        "user-a" not in bucket.key_hash and "user-b" not in bucket.key_hash
        for bucket in db_session.query(RateLimitBucket).all()
    )
    get_settings.cache_clear()


def test_public_beta_sends_only_one_capacity_notice_per_user_day(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date()
    monkeypatch.setenv("PUBLIC_BETA_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BETA_START_DATE", today.isoformat())
    monkeypatch.setenv("PUBLIC_BETA_END_DATE", today.isoformat())
    monkeypatch.setenv("PUBLIC_BETA_MESSAGES_PER_USER_DAY", "1")
    get_settings.cache_clear()

    call_webhook(db_session, payload("wamid.beta-1", "Hello"))
    call_webhook(db_session, payload("wamid.beta-2", "Again"))
    call_webhook(db_session, payload("wamid.beta-3", "Again"))

    outbound = db_session.query(WhatsAppOutboundMessage).all()
    assert len(outbound) == 2
    assert sum("public beta message limit" in item.body.lower() for item in outbound) == 1
    assert db_session.query(WhatsAppInboundMessage).count() == 3
    get_settings.cache_clear()


def test_webhook_preserves_durable_inbox_when_chat_processing_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("chat failed")

    monkeypatch.setattr("app.routers.webhooks_meta.chatbot_service.handle", fail)

    call_webhook(db_session, payload("wamid.rollback", "Hello"))

    assert db_session.query(WhatsAppInboundMessage).count() == 1
    assert db_session.query(WhatsAppInboundMessage).one().status == "processing"
    assert db_session.query(WhatsAppOutboundMessage).count() == 0
    assert db_session.query(Message).count() == 0


def test_whatsapp_completes_one_multiturn_ticket_despite_retry(db_session: Session) -> None:
    turns = [
        ("wamid.1", "I need a human"),
        ("wamid.2", "Yes"),
        ("wamid.3", "Test Rider"),
        ("wamid.4", "rider@example.com"),
        ("wamid.5", "My ride receipt is missing"),
        ("wamid.6", "Skip"),
    ]
    result = None
    for message_id, text in turns:
        result = call_webhook(db_session, payload(message_id, text))
    retry = call_webhook(db_session, payload("wamid.6", "Skip"))

    assert result is not None and result.processed == 1
    assert retry.duplicates == 1
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(WhatsAppInboundMessage).count() == 6
    assert db_session.query(WhatsAppOutboundMessage).count() == 6
    ticket = db_session.query(Ticket).one()
    assert ticket.phone_number == "+60108865432"
    assert ticket.consent_given


class FakeClient:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0
        self.callback_data = []

    def send_text(
        self, recipient: str, body: str, *, callback_data: str | None = None
    ) -> str:
        self.calls += 1
        self.callback_data.append(callback_data)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return str(result)


def queue_message(db: Session) -> WhatsAppOutboundMessage:
    inbound = WhatsAppInboundMessage(
        provider_message_id="wamid.queue",
        status="done",
        sender="60108865432",
        phone_number_id=PHONE_NUMBER_ID,
        message_type="text",
    )
    db.add(inbound)
    db.flush()
    outbound = WhatsAppOutboundMessage(
        inbound_message_id=inbound.id,
        recipient=inbound.sender,
        body="Safe reply",
    )
    db.add(outbound)
    db.commit()
    return outbound


def send_settings(**changes) -> Settings:
    values = {
        "meta_send_enabled": True,
        "meta_access_token": "test-access-token-at-least-24-characters",
        "meta_phone_number_id": PHONE_NUMBER_ID,
    }
    return Settings(_env_file=None, **(values | changes))


def test_send_kill_switch_makes_no_network_call(db_session: Session) -> None:
    message = queue_message(db_session)
    client = FakeClient(["wamid.sent"])

    assert not process_next_outbound(
        db_session,
        client,
        settings=send_settings(meta_send_enabled=False),
    )
    assert client.calls == 0
    assert message.status == "queued"


def test_transient_send_retries_then_records_provider_id(db_session: Session) -> None:
    message = queue_message(db_session)
    client = FakeClient([MetaSendError("500", True), "wamid.outbound"])
    now = message.next_attempt_at
    settings = send_settings(meta_send_max_attempts=3)

    assert process_next_outbound(db_session, client, now=now, settings=settings)
    db_session.refresh(message)
    assert (message.status, message.attempts, message.last_error_code) == ("retry", 1, "500")
    assert not process_next_outbound(
        db_session, client, now=now + timedelta(seconds=1), settings=settings
    )
    assert process_next_outbound(
        db_session, client, now=now + timedelta(seconds=3), settings=settings
    )
    db_session.refresh(message)
    assert (message.status, message.attempts, message.provider_message_id) == (
        "sent",
        2,
        "wamid.outbound",
    )
    assert client.callback_data == [f"outbox:{message.id}"] * 2


def test_crash_after_provider_acceptance_leaves_uncertain_and_blocks_recipient(
    db_session: Session,
) -> None:
    message = queue_message(db_session)
    later_inbound = WhatsAppInboundMessage(
        provider_message_id="wamid.later",
        status="done",
        sender=message.recipient,
        phone_number_id=PHONE_NUMBER_ID,
        message_type="text",
    )
    db_session.add(later_inbound)
    db_session.flush()
    db_session.add(
        WhatsAppOutboundMessage(
            inbound_message_id=later_inbound.id,
            recipient=message.recipient,
            body="Later reply",
        )
    )
    db_session.commit()

    class AcceptedThenCrashed:
        def send_text(self, recipient, body, *, callback_data=None):
            current = db_session.get(WhatsAppOutboundMessage, message.id)
            assert (current.status, current.attempts) == ("uncertain", 1)
            assert callback_data == f"outbox:{message.id}"
            raise RuntimeError("simulated process death after acceptance")

    with pytest.raises(RuntimeError, match="simulated process death"):
        process_next_outbound(
            db_session, AcceptedThenCrashed(), settings=send_settings()
        )
    db_session.rollback()
    db_session.refresh(message)
    replacement = FakeClient(["wamid.duplicate"])

    assert (message.status, message.attempts) == ("uncertain", 1)
    assert not process_next_outbound(
        db_session, replacement, settings=send_settings()
    )
    assert replacement.calls == 0


def test_unknown_meta_outcome_is_not_automatically_retried(db_session: Session) -> None:
    message = queue_message(db_session)
    client = FakeClient(
        [MetaSendError("TimeoutError", False, uncertain=True), "wamid.duplicate"]
    )

    assert process_next_outbound(db_session, client, settings=send_settings())
    db_session.refresh(message)
    assert (message.status, message.last_error_code) == ("uncertain", "TimeoutError")
    assert not process_next_outbound(db_session, client, settings=send_settings())
    assert client.calls == 1


def test_send_failure_is_dead_lettered_at_bound(db_session: Session) -> None:
    message = queue_message(db_session)
    client = FakeClient([MetaSendError("400", False)])

    assert process_next_outbound(db_session, client, settings=send_settings())
    db_session.refresh(message)
    assert (message.status, message.attempts, message.last_error_code) == (
        "dead_letter",
        1,
        "400",
    )


def test_transient_send_is_dead_lettered_after_retry_limit(db_session: Session) -> None:
    message = queue_message(db_session)
    client = FakeClient([MetaSendError("500", True) for _ in range(3)])
    settings = send_settings(meta_send_max_attempts=3)
    now = message.next_attempt_at

    for elapsed in (0, 3, 8):
        assert process_next_outbound(
            db_session,
            client,
            now=now + timedelta(seconds=elapsed),
            settings=settings,
        )

    db_session.refresh(message)
    assert (message.status, message.attempts, client.calls) == ("dead_letter", 3, 3)


def test_signed_delivery_status_updates_outbox(db_session: Session) -> None:
    message = queue_message(db_session)
    message.provider_message_id = "wamid.outbound"
    message.status = "sent"
    db_session.commit()
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.outbound",
                                    "status": "delivered",
                                    "timestamp": "1788596000",
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }

    result = call_webhook(db_session, status_payload)
    db_session.refresh(message)
    assert result.status_updates == 1
    assert message.status == "delivered"
    assert message.delivered_at is not None


def test_signed_callback_reconciles_only_its_bound_uncertain_outbox(
    db_session: Session,
) -> None:
    message = queue_message(db_session)
    message.status = "uncertain"
    message.attempts = 1
    db_session.commit()
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.accepted",
                                    "status": "sent",
                                    "timestamp": "1788596000",
                                    "recipient_id": message.recipient,
                                    "biz_opaque_callback_data": f"outbox:{message.id}",
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }

    with pytest.raises(HTTPException):
        call_webhook(db_session, status_payload, "sha256=bad")
    db_session.refresh(message)
    assert (message.status, message.provider_message_id) == ("uncertain", None)

    status = status_payload["entry"][0]["changes"][0]["value"]["statuses"][0]
    status["recipient_id"] = "60999999999"
    assert call_webhook(db_session, status_payload).status_updates == 0
    status.pop("recipient_id")
    assert call_webhook(db_session, status_payload).status_updates == 0
    status["recipient_id"] = message.recipient
    status["status"] = "unknown"
    assert call_webhook(db_session, status_payload).status_updates == 0
    db_session.refresh(message)
    assert (message.status, message.provider_message_id) == ("uncertain", None)

    status["status"] = "sent"
    message.status = "queued"
    message.attempts = 0
    db_session.commit()
    assert call_webhook(db_session, status_payload).status_updates == 0
    db_session.refresh(message)
    assert (message.status, message.provider_message_id) == ("queued", None)
    message.status = "uncertain"
    message.attempts = 1
    db_session.commit()

    status_payload["entry"][0]["changes"][0]["value"]["statuses"][0][
        "biz_opaque_callback_data"
    ] = "outbox:missing"
    assert call_webhook(db_session, status_payload).status_updates == 0
    db_session.refresh(message)
    assert (message.status, message.provider_message_id) == ("uncertain", None)

    status_payload["entry"][0]["changes"][0]["value"]["statuses"][0][
        "biz_opaque_callback_data"
    ] = f"outbox:{message.id}"
    assert call_webhook(db_session, status_payload).status_updates == 1
    db_session.refresh(message)
    assert (message.status, message.provider_message_id) == (
        "sent",
        "wamid.accepted",
    )


def test_signed_callback_reconciles_bound_whatsapp_notification(
    db_session: Session,
) -> None:
    notification = SupportNotification(
        ticket_id="ticket-id",
        channel="whatsapp",
        recipient="+60108865432",
        event_type="ticket_created",
        status="uncertain",
        attempts=1,
        payload={"public_id": "DUDU-1"},
    )
    db_session.add(notification)
    db_session.commit()
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.notification",
                                    "status": "sent",
                                    "timestamp": "1788596000",
                                    "recipient_id": "60108865432",
                                    "biz_opaque_callback_data": (
                                        f"notification:{notification.id}"
                                    ),
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }

    assert call_webhook(db_session, status_payload).status_updates == 1
    db_session.refresh(notification)
    assert notification.status == "sent"
    assert notification.payload["provider_message_id"] == "wamid.notification"


def test_batched_callback_statuses_preserve_outbox_delivery_state(
    db_session: Session,
) -> None:
    message = queue_message(db_session)
    message.status = "uncertain"
    message.attempts = 1
    db_session.commit()
    statuses = [
        {
            "id": "wamid.batched",
            "status": status,
            "timestamp": "1788596000",
            "recipient_id": message.recipient,
            "biz_opaque_callback_data": f"outbox:{message.id}",
        }
        for status in ("delivered", "sent")
    ]
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {"changes": [{"field": "messages", "value": {"statuses": statuses}}]}
        ],
    }

    assert call_webhook(db_session, status_payload).status_updates == 2
    db_session.refresh(message)
    assert message.status == "delivered"
    assert message.provider_message_id == "wamid.batched"
    assert message.delivered_at is not None


def test_batched_callback_statuses_preserve_notification_delivery_state(
    db_session: Session,
) -> None:
    notification = SupportNotification(
        ticket_id="ticket-id",
        channel="whatsapp",
        recipient="+60108865432",
        event_type="ticket_created",
        status="uncertain",
        attempts=1,
        payload={"public_id": "DUDU-1"},
    )
    db_session.add(notification)
    db_session.commit()
    statuses = [
        {
            "id": "wamid.notification-batch",
            "status": status,
            "timestamp": "1788596000",
            "recipient_id": "60108865432",
            "biz_opaque_callback_data": f"notification:{notification.id}",
        }
        for status in ("delivered", "sent")
    ]
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {"changes": [{"field": "messages", "value": {"statuses": statuses}}]}
        ],
    }

    assert call_webhook(db_session, status_payload).status_updates == 2
    db_session.refresh(notification)
    assert notification.status == "delivered"
    assert notification.payload["provider_message_id"] == "wamid.notification-batch"


def test_signed_failure_status_records_only_error_code(db_session: Session) -> None:
    message = queue_message(db_session)
    message.provider_message_id = "wamid.failed"
    message.status = "sent"
    db_session.commit()
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.failed",
                                    "status": "failed",
                                    "timestamp": "1788596000",
                                    "errors": [
                                        {
                                            "code": 131047,
                                            "title": "contains customer data to discard",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }

    result = call_webhook(db_session, status_payload)
    db_session.refresh(message)
    assert result.status_updates == 1
    assert (message.status, message.last_error_code) == ("failed", "131047")
    assert all(
        "customer data" not in str(value)
        for key, value in message.__dict__.items()
        if key != "_sa_instance_state"
    )


def test_meta_client_uses_approved_version_and_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b'{"messages":[{"id":"wamid.accepted"}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(whatsapp_module, "urlopen", fake_urlopen)
    settings = send_settings()

    result = whatsapp_module.MetaWhatsAppClient(settings).send_text(
        "60108865432", "Hello", callback_data="outbox:local-id"
    )

    assert result == "wamid.accepted"
    assert captured == {
        "url": f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages",
        "authorization": f"Bearer {settings.meta_access_token}",
        "timeout": 10.0,
        "payload": {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "60108865432",
            "type": "text",
            "text": {"preview_url": False, "body": "Hello"},
            "biz_opaque_callback_data": "outbox:local-id",
        },
    }


def test_meta_temporary_error_code_is_retryable_even_on_http_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise HTTPError(
            "https://graph.facebook.com/v26.0/messages",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":{"code":131016}}'),
        )

    monkeypatch.setattr(whatsapp_module, "urlopen", fail)

    with pytest.raises(MetaSendError) as exc:
        whatsapp_module.MetaWhatsAppClient(send_settings()).send_text("60108865432", "Hello")

    assert (exc.value.code, exc.value.retryable) == ("131016", True)


def test_unstructured_meta_server_error_is_an_uncertain_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise HTTPError(
            "https://graph.facebook.com/v26.0/messages",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b"upstream connection lost"),
        )

    monkeypatch.setattr(whatsapp_module, "urlopen", fail)

    with pytest.raises(MetaSendError) as exc:
        whatsapp_module.MetaWhatsAppClient(send_settings()).send_text(
            "60108865432", "Hello"
        )

    assert (exc.value.code, exc.value.retryable, exc.value.uncertain) == (
        "http_502",
        False,
        True,
    )


@pytest.mark.parametrize("error_body", [b"[]", b'{"error":[]}'])
def test_worker_audits_malformed_meta_server_error_as_uncertain(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    error_body: bytes,
) -> None:
    message = queue_message(db_session)

    def fail(*args, **kwargs):
        raise HTTPError(
            "https://graph.facebook.com/v26.0/messages",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(error_body),
        )

    monkeypatch.setattr(whatsapp_module, "urlopen", fail)

    assert process_next_outbound(db_session, settings=send_settings())
    db_session.refresh(message)
    audit = (
        db_session.query(AuditLog)
        .filter_by(event_type="whatsapp_message_send_uncertain")
        .one()
    )
    assert (message.status, message.last_error_code) == ("uncertain", "http_502")
    assert audit.details["error_code"] == "http_502"


def test_meta_invalid_response_is_an_uncertain_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b"{}"

    monkeypatch.setattr(whatsapp_module, "urlopen", lambda *args, **kwargs: Response())

    with pytest.raises(MetaSendError) as exc:
        whatsapp_module.MetaWhatsAppClient(send_settings()).send_text(
            "60108865432", "Hello"
        )

    assert (exc.value.code, exc.value.retryable, exc.value.uncertain) == (
        "invalid_meta_response",
        False,
        True,
    )


def test_equal_timestamp_replies_wait_for_earlier_retry(db_session):
    now = datetime.utcnow()
    for index in (1, 2):
        incoming = WhatsAppInboundMessage(provider_message_id=f"same-time-{index}", status="done", sender="60108865432", phone_number_id=PHONE_NUMBER_ID, message_type="text")
        db_session.add(incoming)
        db_session.flush()
        db_session.add(WhatsAppOutboundMessage(id=str(index).zfill(36), inbound_message_id=incoming.id, recipient=incoming.sender, body="Safe reply", created_at=now, next_attempt_at=now + timedelta(seconds=60) if index == 1 else now, status="retry" if index == 1 else "queued"))
    db_session.commit()
    client = FakeClient(["wamid.sent"])
    assert not process_next_outbound(db_session, client, now=now, settings=send_settings())
    assert client.calls == 0
