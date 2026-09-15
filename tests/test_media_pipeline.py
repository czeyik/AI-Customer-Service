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
    WhatsAppOutboundMessage,
)
from app.routers import admin as admin_router
from app.services.inbound import process_next_inbound
from app.routers.webhooks_meta import receive_webhook
from app.services import media as media_module
from app.services.media import (
    MetaMediaClient,
    DownloadedMedia,
    MediaProcessingError,
    MediaRejected,
    PrivateObjectStore,
    process_next_media,
    validate_media,
)
from app.services.dialogue import load_dialogue_data
from app.services.retention import run_retention
from app.services.ticket_drafts import ConsentEvidence, DialogueData, DraftFields, make_prompt, new_draft


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


def test_upload_replay_reuses_verified_object_after_acceptance():
    from botocore.exceptions import ClientError

    class S3:
        saved = None
        versions = 0

        def put_object(self, **kwargs):
            assert kwargs["IfNoneMatch"] == "*"
            if self.saved:
                raise ClientError({"Error": {"Code": "PreconditionFailed"},
                                   "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
            self.saved = kwargs
            self.versions += 1
            raise KeyboardInterrupt("crash after object acceptance")

        def head_object(self, **kwargs):
            assert kwargs == {"Bucket": self.saved["Bucket"], "Key": self.saved["Key"]}
            return self.saved

    storage = object.__new__(PrivateObjectStore)
    storage.settings = Settings(_env_file=None, media_bucket="synthetic")
    storage.client = S3()
    with pytest.raises(KeyboardInterrupt):
        storage.put("approved/test.png", io.BytesIO(PNG), "image/png", "digest")
    storage.put("approved/test.png", io.BytesIO(PNG), "image/png", "digest")
    assert storage.client.versions == 1
    with pytest.raises(MediaRejected, match="stored_object_mismatch"):
        storage.put("approved/test.png", io.BytesIO(PNG), "image/png", "different")


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
    result = asyncio.run(receive_webhook(request, db))
    while process_next_inbound(db):
        pass
    return result


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
    conversation = db_session.query(Conversation).one()
    assert conversation.dialogue_data["evidence_group"] == attachment.evidence_group
    # Media ownership changes the typed dialogue once; an attachment-only outage
    # turn does not invent a handoff offer.
    assert conversation.dialogue_revision == 1
    assert "evidence_group" not in conversation.intake_data
    assert db_session.query(WhatsAppInboundMessage).count() == 1
    assert "quarantined" in db_session.query(WhatsAppOutboundMessage).one().body


def test_media_reply_keeps_intake_open_and_prompts_for_done(db_session: Session) -> None:
    draft = new_draft(expiry_minutes=60)
    draft.fields = DraftFields(
        name="Alex",
        email="alex@example.com",
        phone_number="+60123456789",
        description="My ride receipt is missing",
    )
    draft.issue_collected = True
    draft.consent = ConsentEvidence(
        prompt_id="consent-prompt",
        draft_id=draft.id,
        draft_version=draft.version,
        originating_turn="consent-turn",
        customer_input="Yes",
        recorded_at=datetime.utcnow(),
    )
    dialogue = make_prompt(DialogueData(draft=draft), "details", "details-turn")
    conversation = Conversation(
        channel="whatsapp",
        external_user_id="60108865432",
        preferred_language="en",
        dialogue_data=dialogue.model_dump(mode="json"),
    )
    db_session.add(conversation)
    db_session.commit()

    call_webhook(db_session, media_payload("wamid.more-media", "media-more"))

    db_session.refresh(conversation)
    reply = db_session.query(WhatsAppOutboundMessage).one().body
    assert load_dialogue_data(conversation).pending_prompt.purpose == "details"
    assert "quarantined for security checks" in reply
    assert "Reply Done" in reply


def test_object_store_uses_explicit_regional_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {}
    client = object()

    def fake_client(service: str, **kwargs):
        arguments.update(service=service, **kwargs)
        return client

    monkeypatch.setattr("boto3.client", fake_client)

    assert PrivateObjectStore(media_settings()).client is client
    assert arguments == {
        "service": "s3",
        "region_name": "ap-southeast-5",
        "endpoint_url": "https://s3.ap-southeast-5.amazonaws.com",
    }


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
    attachment.ticket = ticket
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
        status="closed",
        closed_at=datetime.utcnow() - timedelta(days=4 * 365),
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

    result = run_retention(db_session, storage=storage)

    assert (result.tickets, result.attachments, result.failures) == (1, 1, 0)
    assert db_session.get(MediaAttachment, attachment.id) is None
    assert storage.deleted == ["approved/aa/delete.png"]
    with pytest.raises(HTTPException) as exc:
        admin_router.review_media(attachment.id, Request({"type": "http"}), db_session, AdminUser())
    assert exc.value.status_code == 404
