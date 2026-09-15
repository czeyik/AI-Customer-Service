import base64
import hashlib
import io
import os
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from app.config import Settings
from app.services.media import ClamAVScanner, MediaRejected, PrivateObjectStore


if os.getenv("RUN_MEDIA_INTEGRATION") != "1":
    pytest.skip(
        "set RUN_MEDIA_INTEGRATION=1 with private S3 and ClamAV services",
        allow_module_level=True,
    )


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        media_processing_enabled=True,
        media_bucket=os.environ["MEDIA_BUCKET"],
        media_s3_endpoint_url=os.environ["MEDIA_S3_ENDPOINT_URL"],
        clamav_host=os.environ["CLAMAV_HOST"],
    )


def test_real_private_object_store_and_clamav() -> None:
    configured = settings()
    store = PrivateObjectStore(configured)
    store.client.create_bucket(Bucket=configured.media_bucket)
    digest = hashlib.sha256(PNG).hexdigest()
    store.put("approved/integration.png", io.BytesIO(PNG), "image/png", digest)

    with pytest.raises(HTTPError) as denied:
        urlopen(
            f"{configured.media_s3_endpoint_url}/{configured.media_bucket}/approved/integration.png"
        )
    assert denied.value.code == 403
    with urlopen(store.signed_url("approved/integration.png")) as response:
        assert response.read() == PNG
    stored = store.client.head_object(
        Bucket=configured.media_bucket, Key="approved/integration.png"
    )
    assert stored["Metadata"]["sha256"] == digest

    scanner = ClamAVScanner(configured)
    assert scanner.scan(io.BytesIO(PNG)).endswith(" OK")
    with pytest.raises(MediaRejected, match="malware"):
        scanner.scan(io.BytesIO(EICAR))

    store.delete("approved/integration.png")
