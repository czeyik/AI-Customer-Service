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
        re.compile(r"(?i)\b(password|passcode|otp|pin)\s*(?::|=|\bis\b)\s*\S+"),
        "[REDACTED_SECRET]",
    ),
    (
        "api_secret",
        re.compile(r"(?i)\b(api[_ -]?key|secret|token)\s*[:=]\s*[A-Za-z0-9_.\-]{8,}"),
        "[REDACTED_SECRET]",
    ),
    (
        "identity_number",
        re.compile(
            r"(?i)\b(passport(?: number)?|nric|identity(?: card)?(?: number)?|id number)\s*"
            r"(?::|=|\bis\b)\s*[A-Z0-9-]{5,}"
        ),
        "[REDACTED_ID_NUMBER]",
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


# Provider minimization is separate from permitted local intake storage.
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)")
FIELD_PATTERN = re.compile(
    r"(?i)(?:\b(?:my name is|name is|name:|nama saya|nama:)|我叫|姓名[：:])\s*"
    r"([^,;\n.!?，。；@]+)|\b(?:i am|i'm)\s+((?-i:[A-Z][a-z'-]+)(?:\s+(?-i:[A-Z][a-z'-]+))*)"
)
IDENTIFIER_PATTERN = re.compile(
    r"(?i)\b(?:account|trip|booking|ride|ticket|akaun|perjalanan)(?:[ _-]?id| number| no\.?|编号)?"
    r"\s*[:=#]?\s*([A-Z]*[-_]?[0-9][A-Z0-9_-]*)\b|DUDU-[A-Z0-9-]+"
)
REVIEW_MARKERS = (
    "please review these ticket details",
    "sila semak butiran tiket ini",
    "请检查工单资料",
)
FIELD_LABELS = {
    "name": ("name:", "nama:", "姓名：", "姓名:"),
    "email": ("email:", "e-mel:", "邮箱：", "邮箱:"),
    "phone_number": ("contact phone:", "telefon hubungan:", "联系电话：", "联系电话:"),
    "description": ("issue:", "isu:", "问题：", "问题:"),
    "trip_id": ("trip id:", "id perjalanan:", "行程编号：", "行程编号:"),
    "ride_details": ("ride details:", "butiran perjalanan:", "行程详情：", "行程详情:"),
}


def provider_question(text: str, local_values: dict[str, str] | None = None) -> str | None:
    """Minimize known fields; uncertain identity/location prose stays local for clarification."""
    sanitized = text
    for role, value in sorted((local_values or {}).items(), key=lambda item: len(str(item[1])), reverse=True):
        if value:
            sanitized = re.sub(re.escape(str(value)), f"[FIELD_{role.upper()}]", sanitized, flags=re.I)
    sanitized = FIELD_PATTERN.sub("[FIELD_NAME]", sanitized)
    sanitized = EMAIL_PATTERN.sub("[FIELD_EMAIL]", sanitized)
    sanitized = PHONE_PATTERN.sub("[FIELD_PHONE_NUMBER]", sanitized)
    sanitized = IDENTIFIER_PATTERN.sub("[FIELD_IDENTIFIER]", sanitized)
    sanitized = re.sub(r"(?i)\b(?:otp|pin|passcode|password)\s+\d{4,8}\b", "[REDACTED_SECRET]", sanitized)
    sanitized = re.sub(r"(?i)\b(?:account|trip|booking|ride)(?:[ _-]?id| number)?\s*[:=]\s*[\w-]+", "[FIELD_IDENTIFIER]", sanitized)
    sanitized = re.sub(r"\b(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{4,}\b", "[FIELD_IDENTIFIER]", sanitized)
    sanitized = redact_sensitive(sanitized).text
    known_words = {"I", "My", "The", "How", "What", "Why", "When", "Where", "Can", "Could", "Does", "Do", "Are", "Is", "Hi", "Hello", "Please", "Thanks", "Thank", "If", "It", "Yes", "No", "Fare", "Car", "Malaysia", "English", "Malay", "Chinese", "Bahasa", "Saya", "Bagaimana", "Apakah", "Boleh", "Di", "Adakah", "Sila", "Ini", "Tidak", "Terima", "Jika", "Mengapa", "Siapa", "Siapakah", "Nama", "Resit", "Semak", "Anda", "Hantar", "Langkau", "Done", "Submit", "Stop"}
    sanitized = re.sub(r"\b[A-Z][a-z]{1,}\b", lambda match: match.group() if match.group() in known_words else "[FIELD_POSSIBLE_NAME]", sanitized)
    # ponytail: regex cannot anonymize arbitrary prose. Unrecognized identifying narratives
    # require a local rephrase; upgrade with an evaluated local entity detector if needed.
    if re.search(
        r"(?i)\b(?:address|passport|nric|ic number|bank account|my driver is|driver named|lives? at|located at|pickup at|pick me up at|destination is|alamat|pemandu bernama)\b|住址|身份证|护照|司机叫|上车地点|目的地是",
        sanitized,
    ):
        return None
    return sanitized if len(sanitized) <= 1600 else None


def sanitize_history_text(
    text: str, local_values: dict[str, str] | None = None
) -> str | None:
    """Return model-safe history text, replacing state snapshots as one bounded marker."""
    lowered = text.lower()
    if any(marker in lowered for marker in REVIEW_MARKERS):
        present = [
            field
            for field, labels in FIELD_LABELS.items()
            if any(label in lowered for label in labels)
        ]
        return f"[TICKET_REVIEW fields_present={','.join(present)}]"
    if text.lstrip().startswith(("{", "[")) and any(
        f'"{field}"' in lowered for field in FIELD_LABELS
    ):
        present = [field for field in FIELD_LABELS if f'"{field}"' in lowered]
        return f"[DIALOGUE_TOOL_RESULT fields_present={','.join(present)}]"
    return provider_question(redact_sensitive(text).text, local_values)
