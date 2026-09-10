import base64
import hashlib
import json
import socket
import struct
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AuditLog, MediaAttachment, Ticket
from app.services.object_storage import delete_all_versions


ALLOWED_MEDIA = {
    "image/jpeg": ("image", 5 * 1024 * 1024, "jpg"),
    "image/png": ("image", 5 * 1024 * 1024, "png"),
    "video/mp4": ("video", 16 * 1024 * 1024, "mp4"),
    "video/3gpp": ("video", 16 * 1024 * 1024, "3gp"),
}


class MediaRejected(Exception):
    pass


class MediaProcessingError(Exception):
    pass


@dataclass
class DownloadedMedia:
    stream: BinaryIO
    mime_type: str
    provider_sha256: str | None


class MetaMediaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def download(self, media_id: str, max_bytes: int) -> DownloadedMedia:
        metadata = self._json(
            f"https://graph.facebook.com/{self.settings.meta_graph_api_version}/"
            f"{quote(media_id, safe='')}?phone_number_id="
            f"{quote(self.settings.meta_phone_number_id, safe='')}"
        )
        if str(metadata.get("id", "")) != media_id:
            raise MediaProcessingError("invalid_metadata_id")
        url = str(metadata.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (
            parsed.hostname == "facebook.com"
            or parsed.hostname == "fbsbx.com"
            or (parsed.hostname or "").endswith((".facebook.com", ".fbsbx.com"))
        ):
            raise MediaProcessingError("invalid_download_url")
        declared_size = metadata.get("file_size")
        if declared_size is not None and int(declared_size) > max_bytes:
            raise MediaRejected("oversized")

        request = Request(url, headers=self._headers())
        stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        total = 0
        try:
            with urlopen(request, timeout=self.settings.meta_send_timeout_seconds) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise MediaRejected("oversized")
                while chunk := response.read(min(64 * 1024, max_bytes + 1 - total)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaRejected("oversized")
                    stream.write(chunk)
        except MediaRejected:
            stream.close()
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            stream.close()
            raise MediaProcessingError("download_failed") from exc
        stream.seek(0)
        return DownloadedMedia(
            stream=stream,
            mime_type=str(metadata.get("mime_type", "")),
            provider_sha256=str(metadata["sha256"]) if metadata.get("sha256") else None,
        )

    def _json(self, url: str) -> dict:
        try:
            with urlopen(
                Request(url, headers=self._headers()),
                timeout=self.settings.meta_send_timeout_seconds,
            ) as response:
                value = json.loads(response.read(64 * 1024))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise MediaProcessingError("metadata_failed") from exc
        if not isinstance(value, dict):
            raise MediaProcessingError("invalid_metadata")
        return value

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.meta_access_token}"}


class ClamAVScanner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def scan(self, stream: BinaryIO) -> str:
        try:
            with socket.create_connection(
                (self.settings.clamav_host, self.settings.clamav_port),
                timeout=self.settings.clamav_timeout_seconds,
            ) as connection:
                connection.settimeout(self.settings.clamav_timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                stream.seek(0)
                while chunk := stream.read(64 * 1024):
                    connection.sendall(struct.pack(">I", len(chunk)) + chunk)
                connection.sendall(struct.pack(">I", 0))
                response_bytes = bytearray()
                while not response_bytes.endswith(b"\0"):
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response_bytes.extend(chunk)
                response = bytes(response_bytes).rstrip(b"\0").decode("utf-8", "replace")
        except (OSError, UnicodeError) as exc:
            raise MediaProcessingError("scanner_unavailable") from exc
        finally:
            stream.seek(0)
        if response.endswith(" OK"):
            return response[:80]
        if " FOUND" in response:
            raise MediaRejected("malware")
        raise MediaProcessingError("scanner_error")


class PrivateObjectStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        import boto3

        endpoint_url = self.settings.media_s3_endpoint_url or (
            f"https://s3.{self.settings.media_region}.amazonaws.com"
        )
        self.client = boto3.client(
            "s3",
            region_name=self.settings.media_region,
            endpoint_url=endpoint_url,
        )

    def put(self, key: str, stream: BinaryIO, mime_type: str, sha256: str) -> None:
        stream.seek(0)
        self.client.upload_fileobj(
            stream,
            self.settings.media_bucket,
            key,
            ExtraArgs={
                "ContentType": mime_type,
                "Metadata": {"sha256": sha256},
            },
        )

    def signed_url(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.media_bucket, "Key": key},
            ExpiresIn=self.settings.media_signed_url_seconds,
        )

    def delete(self, key: str) -> None:
        delete_all_versions(self.client, self.settings.media_bucket, key)

    def list(self, prefix: str):
        continuation = None
        while True:
            arguments = {"Bucket": self.settings.media_bucket, "Prefix": prefix}
            if continuation:
                arguments["ContinuationToken"] = continuation
            page = self.client.list_objects_v2(**arguments)
            yield from page.get("Contents", [])
            if not page.get("IsTruncated"):
                return
            continuation = page["NextContinuationToken"]


def reconcile_orphaned_media(
    db: Session,
    storage: PrivateObjectStore | None = None,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    storage = storage or PrivateObjectStore()
    linked = {
        key
        for (key,) in db.query(MediaAttachment.object_key)
        .filter(MediaAttachment.object_key.isnot(None))
        .all()
    }
    removed = 0
    failures = 0
    # ponytail: scan the prefix directly for the pilot; use S3 Inventory above 10,000 objects.
    for item in storage.list("approved/"):
        modified = item["LastModified"]
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        key = item["Key"]
        if key in linked or modified > now - timedelta(hours=24):
            continue
        subject = hashlib.sha256(key.encode()).hexdigest()[:24]
        try:
            storage.delete(key)
            event = "orphaned_media_deleted"
            removed += 1
        except Exception:
            event = "orphaned_media_delete_failed"
            failures += 1
        db.add(
            AuditLog(
                actor="media-reconciler",
                event_type=event,
                subject_type="object",
                subject_id=subject,
                details={"key_hash": subject},
            )
        )
    db.commit()
    if failures:
        raise MediaProcessingError("orphan_cleanup_failed")
    return removed


def process_next_media(
    db: Session,
    client: MetaMediaClient | None = None,
    scanner: ClamAVScanner | None = None,
    storage: PrivateObjectStore | None = None,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> bool:
    settings = settings or get_settings()
    if not settings.media_processing_enabled:
        return False
    now = now or datetime.utcnow()
    attachment = (
        db.query(MediaAttachment)
        .filter(
            MediaAttachment.status == "queued",
            MediaAttachment.next_attempt_at <= now,
        )
        .order_by(MediaAttachment.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not attachment:
        return False
    attachment.attempts += 1
    object_store = storage
    object_key: str | None = None
    event = "media_processing_failed"
    try:
        specification = ALLOWED_MEDIA.get(attachment.declared_mime_type)
        if not specification or specification[0] != attachment.media_type:
            raise MediaRejected("unsupported_type")
        downloaded = (client or MetaMediaClient(settings)).download(
            attachment.provider_media_id, specification[1]
        )
        try:
            if downloaded.mime_type != attachment.declared_mime_type:
                raise MediaRejected("metadata_type_mismatch")
            content = downloaded.stream.read(specification[1] + 1)
            downloaded.stream.seek(0)
            if not content or len(content) > specification[1]:
                raise MediaRejected("empty_or_oversized")
            detected = validate_media(content)
            if detected != attachment.declared_mime_type:
                raise MediaRejected("content_type_mismatch")
            digest = hashlib.sha256(content).hexdigest()
            expected_hash = downloaded.provider_sha256 or attachment.provider_sha256
            if expected_hash and not _hash_matches(expected_hash, digest):
                raise MediaRejected("integrity_mismatch")
            attachment.scan_result = (scanner or ClamAVScanner(settings)).scan(downloaded.stream)
            object_key = f"approved/{digest[:2]}/{attachment.id}.{specification[2]}"
            object_store = object_store or PrivateObjectStore(settings)
            object_store.put(object_key, downloaded.stream, detected, digest)
        finally:
            downloaded.stream.close()

        attachment.detected_mime_type = detected
        attachment.sha256 = digest
        attachment.size_bytes = len(content)
        attachment.provider_sha256 = expected_hash
        attachment.object_key = object_key
        attachment.status = "approved"
        attachment.failure_code = None
        attachment.approved_at = now
        ticket = (
            db.query(Ticket)
            .filter(
                Ticket.conversation_id == attachment.conversation_id,
                Ticket.created_at >= attachment.created_at,
            )
            .order_by(Ticket.created_at.desc())
            .first()
        )
        if ticket:
            attachment.ticket = ticket
            ticket.attachment_count = sum(
                item.status == "approved" for item in ticket.attachments
            )
        event = "media_approved"
    except MediaRejected as exc:
        attachment.status = "rejected"
        attachment.failure_code = str(exc)[:80]
        event = "media_rejected"
    except Exception as exc:
        if object_key:
            try:
                if object_store:
                    object_store.delete(object_key)
            except Exception:
                # ponytail: S3/PostgreSQL are not atomic; Wave 11 reconciliation must alert on
                # cleanup failures and remove any object without a matching approved row.
                pass
        attachment.failure_code = (
            str(exc) if isinstance(exc, MediaProcessingError) else "processing_failed"
        )[:80]
        attachment.status = (
            "queued" if attachment.attempts < settings.media_max_attempts else "failed"
        )
        if attachment.status == "queued":
            attachment.next_attempt_at = now + timedelta(seconds=2**attachment.attempts)

    db.add(
        AuditLog(
            actor="media-worker",
            event_type=event,
            subject_type="attachment",
            subject_id=attachment.id,
            details={
                "attachment_id": attachment.id,
                "status": attachment.status,
                "failure_code": attachment.failure_code,
                "attempt": attachment.attempts,
            },
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        if object_key and event == "media_approved" and object_store:
            try:
                object_store.delete(object_key)
            except Exception:
                pass
        raise
    return True


def delete_attachment(
    db: Session,
    attachment: MediaAttachment,
    actor: str,
    storage: PrivateObjectStore | None = None,
) -> None:
    if attachment.status == "deleted":
        return
    if attachment.object_key:
        (storage or PrivateObjectStore()).delete(attachment.object_key)
    ticket = attachment.ticket
    attachment.status = "deleted"
    attachment.deleted_at = datetime.utcnow()
    attachment.object_key = None
    if ticket:
        ticket.attachment_count = len(
            [
                item
                for item in ticket.attachments
                if item.status == "approved" and item != attachment
            ]
        )
    db.add(
        AuditLog(
            actor=actor,
            event_type="media_deleted",
            subject_type="attachment",
            subject_id=attachment.id,
            details={"attachment_id": attachment.id, "ticket_id": attachment.ticket_id},
        )
    )
    db.commit()


def validate_media(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        _validate_png(content)
        return "image/png"
    if content.startswith(b"\xff\xd8"):
        _validate_jpeg(content)
        return "image/jpeg"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return _validate_iso_video(content)
    raise MediaRejected("unknown_content_type")


def _validate_png(content: bytes) -> None:
    offset = 8
    chunks: list[bytes] = []
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset : offset + 4], "big")
        kind = content[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(content):
            raise MediaRejected("corrupt_png")
        data = content[offset + 8 : offset + 8 + length]
        crc = int.from_bytes(content[offset + 8 + length : end], "big")
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise MediaRejected("corrupt_png")
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            break
    if (
        chunks[:1] != [b"IHDR"]
        or b"IDAT" not in chunks
        or chunks[-1:] != [b"IEND"]
        or offset != len(content)
    ):
        raise MediaRejected("corrupt_png")


def _validate_jpeg(content: bytes) -> None:
    if len(content) < 20 or not content.endswith(b"\xff\xd9"):
        raise MediaRejected("corrupt_jpeg")
    offset = 2
    has_frame = has_scan = False
    while offset + 4 <= len(content):
        if content[offset] != 0xFF:
            raise MediaRejected("corrupt_jpeg")
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        marker = content[offset]
        offset += 1
        if marker == 0xD9:
            break
        if marker == 0xDA:
            has_scan = True
            break
        if marker in range(0xD0, 0xD8) or marker == 0x01:
            continue
        if offset + 2 > len(content):
            raise MediaRejected("corrupt_jpeg")
        length = int.from_bytes(content[offset : offset + 2], "big")
        if length < 2 or offset + length > len(content):
            raise MediaRejected("corrupt_jpeg")
        if marker in (
            *range(0xC0, 0xC4),
            *range(0xC5, 0xC8),
            *range(0xC9, 0xCC),
            *range(0xCD, 0xD0),
        ):
            has_frame = True
        offset += length
    if not has_frame or not has_scan:
        raise MediaRejected("corrupt_jpeg")


def _validate_iso_video(content: bytes) -> str:
    offset = 0
    boxes: set[bytes] = set()
    brand = b""
    while offset + 8 <= len(content):
        size = int.from_bytes(content[offset : offset + 4], "big")
        kind = content[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > len(content):
                raise MediaRejected("corrupt_video")
            size = int.from_bytes(content[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(content) - offset
        if size < header or offset + size > len(content):
            raise MediaRejected("corrupt_video")
        boxes.add(kind)
        if kind == b"ftyp" and size >= header + 4:
            brand = content[offset + header : offset + header + 4]
        offset += size
    if (
        offset != len(content)
        or b"ftyp" not in boxes
        or b"mdat" not in boxes
        or not ({b"moov", b"moof"} & boxes)
    ):
        raise MediaRejected("corrupt_video")
    return "video/3gpp" if brand.startswith(b"3g") else "video/mp4"


def _hash_matches(expected: str, digest: str) -> bool:
    compact = expected.strip().lower()
    if compact == digest:
        return True
    try:
        return base64.b64decode(expected, validate=True).hex() == digest
    except (ValueError, TypeError):
        return False
