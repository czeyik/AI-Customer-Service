import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.main import app
from app.models import Base, Message, RateLimitBucket
from app.schemas import ChatRequest
from app.services.chatbot import ChatbotService
from app.services.rate_limit import DatabaseRateLimiter


def test_rate_limits_are_shared_hashed_expiring_and_bounded() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    limiter = DatabaseRateLimiter()
    now = datetime(2026, 9, 9, 12, 0)

    first = sessions()
    assert limiter.allow(first, "user:private-id", 1, max_keys=1, now=now)
    first.commit()
    assert "private-id" not in first.query(RateLimitBucket).one().key_hash
    first.close()

    second = sessions()
    assert not limiter.allow(second, "user:private-id", 1, max_keys=1, now=now)
    assert not limiter.allow(second, "user:another-id", 1, max_keys=1, now=now)
    second.rollback()
    assert limiter.allow(
        second,
        "user:another-id",
        1,
        max_keys=1,
        now=now + timedelta(seconds=61),
    )
    second.commit()
    assert second.query(RateLimitBucket).count() == 1
    second.close()


def test_security_headers_are_set_on_application_responses() -> None:
    sent: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health",
                "raw_path": b"/health",
                "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {key.decode(): value.decode() for key, value in start["headers"]}

    assert start["status"] == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_prohibited_secrets_are_redacted_before_message_storage() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    ChatbotService().handle(
        db,
        ChatRequest(
            external_user_id="redaction-test",
            text="My password is hunter2 and passport number is A12345678",
        ),
    )

    stored = " ".join(row.content for row in db.query(Message).all())
    assert "hunter2" not in stored and "A12345678" not in stored
    assert "[REDACTED_SECRET]" in stored and "[REDACTED_ID_NUMBER]" in stored
    db.close()


def test_web_ip_limit_precedes_attacker_controlled_user_identity(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_MESSAGES_PER_MINUTE", "1")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    service = ChatbotService()

    service.handle(
        db,
        ChatRequest(external_user_id="first", text="Hello"),
        ip_address="203.0.113.8",
    )
    blocked = service.handle(
        db,
        ChatRequest(external_user_id="rotated", text="Hello"),
        ip_address="203.0.113.8",
    )

    assert blocked.safety_flags == ["rate_limited"]
    assert db.query(RateLimitBucket).count() == 2
    db.close()
    get_settings.cache_clear()
