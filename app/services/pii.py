import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    text: str
    findings: list[str]


PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("payment_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_PAYMENT_CARD]"),
    ("malaysia_nric_like", re.compile(r"\b\d{6}-?\d{2}-?\d{4}\b"), "[REDACTED_ID_NUMBER]"),
    (
        "password_or_otp",
        re.compile(r"(?i)\b(password|passcode|otp|pin)\s*[:=]\s*\S+"),
        "[REDACTED_SECRET]",
    ),
    (
        "api_secret",
        re.compile(r"(?i)\b(api[_ -]?key|secret|token)\s*[:=]\s*[A-Za-z0-9_.\-]{8,}"),
        "[REDACTED_SECRET]",
    ),
]


def redact_sensitive(text: str) -> RedactionResult:
    findings: list[str] = []
    redacted = text
    for finding, pattern, replacement in PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            findings.append(finding)
    return RedactionResult(text=redacted, findings=findings)

