import asyncio
import base64
import hashlib
import hmac
import io
import json
from collections.abc import Generator
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.config import Settings, get_settings
from app.models import (
    AdminUser,
    AuditLog,
    Base,
    Conversation,
    MediaAttachment,
    Ticket,
    WhatsAppInboundMessage,
)
from app.routers import admin as admin_router
from app.routers.webhooks_meta import receive_webhook
from app.services import media as media_module
from app.services.media import (
    MetaMediaClient,
    DownloadedMedia,
    MediaProcessingError,
    MediaRejected,
    delete_attachment,
    process_next_media,
    validate_media,
)


APP_SECRET = "test-meta-app-secret-at-least-24"
PHONE_NUMBER_ID = "123456789"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def box(kind: bytes, body: bytes = b"") -> bytes:
    return (len(body) + 8).to_bytes(4, "big") + kind + body


MP4 = box(b"ftyp", b"isom\0\0\0\0isom") + box(b"moov") + box(b"mdat", b"video")
THREE_GP = box(b"ftyp", b"3gp5\0\0\0\03gp5") + box(b"moov") + box(b"mdat", b"video")
JPEG = (
    b"\xff\xd8"
    + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    + b"\x00\xff\xd9"
)


@pytest.fixture()
def db_session(monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_PHONE_NUMBER_ID", PHONE_NUMBER_ID)
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


def media_payload(
    message_id: str,
    media_id: str,
    caption: str = "Damage evidence",
    *,
    media_type: str = "image",
    mime_type: str = "image/png",
    content: bytes = PNG,
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "messages": [
                                {
                                    "from": "60108865432",
                                    "id": message_id,
                                    "timestamp": "1788596000",
                                    "type": media_type,
                                    media_type: {
                                        "id": media_id,
                                        "mime_type": mime_type,
                                        "sha256": hashlib.sha256(content).hexdigest(),
                                        "caption": caption,
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def call_webhook(db: Session, payload: dict):
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/meta",
            "headers": [(b"x-hub-signature-256", f"sha256={digest}".encode())],
        },
        receive,
    )
    return asyncio.run(receive_webhook(request, db))


class FakeMetaClient:
    def __init__(self, content: bytes = PNG, mime_type: str = "image/png") -> None:
        self.content = content
        self.mime_type = mime_type

    def download(self, media_id: str, max_bytes: int) -> DownloadedMedia:
        return DownloadedMedia(
            io.BytesIO(self.content), self.mime_type, hashlib.sha256(self.content).hexdigest()
        )


class FakeScanner:
    def __init__(self, result: Exception | None = None) -> None:
        self.result = result

    def scan(self, stream) -> str:
        if self.result:
            raise self.result
        return "stream: OK"


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, stream, mime_type: str, sha256: str) -> None:
        self.objects[key] = stream.read()
        stream.seek(0)

    def signed_url(self, key: str) -> str:
        return f"https://private.example/{key}?expires=300"

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)


def media_settings(**changes) -> Settings:
    return Settings(
        _env_file=None,
        media_processing_enabled=True,
        media_bucket="test-private-bucket",
        **changes,
    )


def test_meta_download_authenticates_metadata_and_stream_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Response:
        def __init__(self, body: bytes, headers: dict | None = None) -> None:
            self.body = io.BytesIO(body)
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amount: int = -1) -> bytes:
            return self.body.read(amount)

    metadata = json.dumps(
        {
            "id": "media-auth",
            "url": "https://lookaside.fbsbx.com/whatsapp_business/attachment",
            "mime_type": "image/png",
            "sha256": hashlib.sha256(PNG).hexdigest(),
            "file_size": len(PNG),
        }
    ).encode()
    responses = [Response(metadata), Response(PNG, {"Content-Length": str(len(PNG))})]

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.headers["Authorization"], timeout))
        return responses.pop(0)

    monkeypatch.setattr(media_module, "urlopen", fake_urlopen)
    configured = media_settings(
        meta_access_token="test-access-token-at-least-24-characters",
        meta_phone_number_id=PHONE_NUMBER_ID,
    )
    downloaded = MetaMediaClient(configured).download("media-auth", 5 * 1024 * 1024)

    assert downloaded.stream.read() == PNG
    downloaded.stream.close()
    assert calls == [
        (
            f"https://graph.facebook.com/v26.0/media-auth?phone_number_id={PHONE_NUMBER_ID}",
            f"Bearer {configured.meta_access_token}",
            10.0,
        ),
        (
            "https://lookaside.fbsbx.com/whatsapp_business/attachment",
            f"Bearer {configured.meta_access_token}",
            10.0,
        ),
    ]


def test_meta_download_rejects_declared_oversize_before_fetching_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amount: int = -1) -> bytes:
            return json.dumps(
                {
                    "id": "media-large",
                    "url": "https://lookaside.fbsbx.com/attachment",
                    "mime_type": "image/png",
                    "file_size": 5 * 1024 * 1024 + 1,
                }
            ).encode()

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr(media_module, "urlopen", fake_urlopen)
    configured = media_settings(
        meta_access_token="test-access-token-at-least-24-characters",
        meta_phone_number_id=PHONE_NUMBER_ID,
    )
    with pytest.raises(MediaRejected, match="oversized"):
        MetaMediaClient(configured).download("media-large", 5 * 1024 * 1024)
    assert calls == 1


def test_launch_image_types_are_content_sniffed() -> None:
    assert validate_media(PNG) == "image/png"
    assert validate_media(JPEG) == "image/jpeg"


def test_signed_media_webhook_is_queued_exactly_once(db_session: Session) -> None:
    first = call_webhook(db_session, media_payload("wamid.image", "media-1"))
    duplicate = call_webhook(db_session, media_payload("wamid.image", "media-1"))

    assert (first.processed, first.duplicates) == (1, 0)
    assert (duplicate.processed, duplicate.duplicates) == (0, 1)
    attachment = db_session.query(MediaAttachment).one()
    assert attachment.status == "queued"
    assert attachment.object_key is None
    assert db_session.query(WhatsAppInboundMessage).count() == 1


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [("video/mp4", MP4), ("video/3gpp", THREE_GP)],
)
def test_launch_video_types_are_sniffed_scanned_and_stored(
    db_session: Session, mime_type: str, content: bytes
) -> None:
    call_webhook(
        db_session,
        media_payload(
            "wamid.video",
            "media-video",
            media_type="video",
            mime_type=mime_type,
            content=content,
        ),
    )
    attachment = db_session.query(MediaAttachment).one()
    storage = FakeStore()

    process_next_media(
        db_session,
        FakeMetaClient(content, mime_type),
        FakeScanner(),
        storage,
        settings=media_settings(),
    )
    db_session.refresh(attachment)
    assert attachment.status == "approved" and attachment.detected_mime_type == mime_type
    assert list(storage.objects.values()) == [content]


def test_clean_media_reaches_ticket_and_authorized_reviewer(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_webhook(db_session, media_payload("wamid.clean", "media-clean"))
    attachment = db_session.query(MediaAttachment).one()
    ticket = Ticket(
        public_id="DUDU-TEST-MEDIA",
        conversation_id=attachment.conversation_id,
        urgency="normal",
        channel="whatsapp",
        external_user_id="60108865432",
        name="Test Rider",
        email="rider@example.com",
        phone_number="+60108865432",
        issue_type="complaint",
        description="Vehicle damage",
        consent_given=True,
    )
    admin = AdminUser(
        username="jane",
        display_name="Jane",
        email="jane@example.com",
        password_hash="unused",
        totp_secret_ref="jane-ref",
    )
    db_session.add_all([ticket, admin])
    db_session.commit()
    storage = FakeStore()

    assert process_next_media(
        db_session,
        FakeMetaClient(),
        FakeScanner(),
        storage,
        settings=media_settings(),
    )
    db_session.refresh(attachment)
    db_session.refresh(ticket)
    assert attachment.status == "approved" and attachment.ticket_id == ticket.id
    assert attachment.sha256 == hashlib.sha256(PNG).hexdigest()
    assert ticket.attachment_count == 1
    assert list(storage.objects.values()) == [PNG]

    monkeypatch.setattr(admin_router, "PrivateObjectStore", lambda: storage)
    request = Request({"type": "http", "method": "GET", "path": "/admin/media/x", "headers": []})
    response = admin_router.review_media(attachment.id, request, db_session, admin)
    assert response.status_code == 303 and "expires=300" in response.headers["location"]
    assert db_session.query(AuditLog).filter_by(event_type="media_review_link_issued").one()


def test_reviewer_route_requires_an_authenticated_admin(db_session: Session) -> None:
    request = Request({"type": "http", "method": "GET", "path": "/admin/media/x", "headers": []})
    with pytest.raises(HTTPException) as exc:
        admin_router.get_current_admin(request, db_session)
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    ("scanner_error", "expected_status"),
    [
        (MediaRejected("malware"), "rejected"),
        (MediaProcessingError("scanner_unavailable"), "failed"),
    ],
)
def test_unsafe_or_failed_media_never_becomes_accessible(
    db_session: Session, scanner_error: Exception, expected_status: str
) -> None:
    call_webhook(db_session, media_payload("wamid.bad", "media-bad"))
    attachment = db_session.query(MediaAttachment).one()
    storage = FakeStore()
    started = datetime.utcnow()

    for seconds in (0, 3, 8):
        process_next_media(
            db_session,
            FakeMetaClient(),
            FakeScanner(scanner_error),
            storage,
            settings=media_settings(),
            now=started + timedelta(seconds=seconds),
        )
    db_session.refresh(attachment)
    assert attachment.status == expected_status
    assert attachment.object_key is None and not storage.objects
    with pytest.raises(HTTPException) as exc:
        admin_router.review_media(attachment.id, Request({"type": "http"}), db_session, AdminUser())
    assert exc.value.status_code == 404


def test_corrupt_and_mismatched_content_is_rejected(db_session: Session) -> None:
    with pytest.raises(MediaRejected, match="corrupt_png"):
        validate_media(PNG[:-4])
    call_webhook(db_session, media_payload("wamid.mismatch", "media-mismatch"))
    attachment = db_session.query(MediaAttachment).one()

    process_next_media(
        db_session,
        FakeMetaClient(mime_type="image/jpeg"),
        FakeScanner(),
        FakeStore(),
        settings=media_settings(),
    )
    db_session.refresh(attachment)
    assert (attachment.status, attachment.failure_code) == ("rejected", "metadata_type_mismatch")


def test_sensitive_caption_is_rejected_without_download(db_session: Session) -> None:
    call_webhook(
        db_session,
        media_payload("wamid.sensitive", "media-sensitive", "My identity card"),
    )
    attachment = db_session.query(MediaAttachment).one()
    assert (attachment.status, attachment.failure_code) == ("rejected", "sensitive_content")


def test_deletion_invalidates_review_link(db_session: Session) -> None:
    conversation = Conversation(channel="whatsapp", external_user_id="60108865432")
    inbound = WhatsAppInboundMessage(
        id="inbound-delete",
        provider_message_id="wamid.delete",
        sender="60108865432",
        phone_number_id=PHONE_NUMBER_ID,
        message_type="image",
    )
    ticket = Ticket(
        public_id="DUDU-TEST-DELETE",
        conversation=conversation,
        urgency="normal",
        channel="whatsapp",
        external_user_id="60108865432",
        name="Test Rider",
        email="rider@example.com",
        phone_number="+60108865432",
        issue_type="complaint",
        description="Damage",
        consent_given=True,
        attachment_count=1,
    )
    attachment = MediaAttachment(
        provider_media_id="media-delete",
        inbound_message_id=inbound.id,
        conversation=conversation,
        ticket=ticket,
        media_type="image",
        declared_mime_type="image/png",
        detected_mime_type="image/png",
        sha256=hashlib.sha256(PNG).hexdigest(),
        size_bytes=len(PNG),
        status="approved",
        object_key="approved/aa/delete.png",
    )
    db_session.add_all([conversation, inbound, ticket, attachment])
    db_session.commit()
    storage = FakeStore()
    storage.objects[attachment.object_key] = PNG

    delete_attachment(db_session, attachment, "retention-job", storage)

    assert attachment.status == "deleted" and attachment.object_key is None
    assert ticket.attachment_count == 0 and storage.deleted == ["approved/aa/delete.png"]
    with pytest.raises(HTTPException) as exc:
        admin_router.review_media(attachment.id, Request({"type": "http"}), db_session, AdminUser())
    assert exc.value.status_code == 404
