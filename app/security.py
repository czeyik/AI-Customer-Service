import hmac
from hashlib import sha256
from typing import Any

import bcrypt
import pyotp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


def verify_totp(code: str) -> bool:
    settings = get_settings()
    if settings.admin_totp_secret:
        return pyotp.TOTP(settings.admin_totp_secret).verify(code, valid_window=1)
    if settings.is_production:
        return False
    return code == "000000"


def make_session_token(username: str) -> str:
    signer = URLSafeTimedSerializer(get_settings().secret_key)
    return signer.dumps({"username": username})


def read_session_token(token: str, max_age_seconds: int = 60 * 60 * 12) -> dict[str, Any] | None:
    signer = URLSafeTimedSerializer(get_settings().secret_key)
    try:
        data = signer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "username" not in data:
        return None
    return data


def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = get_settings().meta_app_secret
    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)

