import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import ChatRequest
from app.services.guardrails import assess_message
from app.services.pii import EMAIL_PATTERN, FIELD_PATTERN, PHONE_PATTERN


Language = Literal["en", "ms", "zh"]
FieldName = Literal[
    "name", "email", "phone_number", "account_id", "trip_id", "description", "ride_details"
]
DraftStatus = Literal["active", "paused", "cancelled", "submitted"]
PromptPurpose = Literal[
    "offer", "consent", "field", "details", "review", "case_confirmation"
]
IssueType = Literal[
    "general_faq",
    "human_escalation",
    "complaint",
    "payment_or_fare",
    "fraud",
    "account_support",
    "partnership",
    "prohibited_action_request",
    "safety_incident",
    "unconfirmed_question",
]
Priority = Literal["normal", "high", "urgent"]

PHONE_RE = re.compile(r"^[+\d][\d ()-]{7,24}$")
SUBMIT = {"submit", "hantar", "提交"}
EXPLICIT_WITHDRAWAL = {"i decline", "decline", "tidak setuju", "不同意", "拒绝"}
MAX_DETAILS_LENGTH = 2_000
MAX_CASE_UPDATES = 20


def normalize_phone_number(value: str | None) -> str | None:
    if not value or not PHONE_RE.fullmatch(value.strip()):
        return None
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15:
        return None
    if digits.startswith("0"):
        digits = f"60{digits[1:]}"
    return f"+{digits}"


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def accumulate_ride_details(previous: str | None, incoming: str) -> str:
    """Append one detail fragment once while preserving the stored history."""
    incoming = incoming.strip()
    if not previous or not incoming:
        return previous or incoming
    if previous == incoming or previous.endswith(f"\n{incoming}"):
        return previous
    return f"{previous}\n{incoming}"


class ConsentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str = Field(min_length=1, max_length=64)
    draft_id: str
    draft_version: int = Field(ge=1)
    originating_turn: str = Field(min_length=1, max_length=255)
    customer_input: str = Field(min_length=1, max_length=64)
    recorded_at: datetime


class DraftFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone_number: str | None = Field(default=None, max_length=32)
    account_id: str | None = Field(default=None, max_length=255)
    trip_id: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=4096)
    ride_details: str | None = Field(default=None, max_length=MAX_DETAILS_LENGTH)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: int = Field(default=1, ge=1)
    status: DraftStatus = "active"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    fields: DraftFields = Field(default_factory=DraftFields)
    issue_collected: bool = False
    ride_details_collected: bool = False
    details_complete: bool = False
    issue_type: IssueType = "human_escalation"
    proposed_issue_type: IssueType | None = None
    priority: Priority = "normal"
    proposed_priority: Priority | None = None
    consent: ConsentEvidence | None = None
    review_required: bool = False
    evidence_group: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    attachment_count: int = Field(default=0, ge=0)
    safety_flags: list[str] = Field(default_factory=list, max_length=20)
    safety_notes: list[str] = Field(default_factory=list)


class PendingOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1, max_length=4096)
    issue_type: IssueType = "unconfirmed_question"
    originating_turn: str


class PendingPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    purpose: PromptPurpose
    field: FieldName | None = None
    draft_id: str | None = None
    case_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    operation_id: str | None = None
    originating_turn: str = Field(min_length=1, max_length=255)


class OperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    prompt_id: str
    version: int
    customer_input: str = Field(max_length=64)
    case_reference: str | None = None


class PendingCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    case_id: str
    case_reference: str
    case_version: int = Field(default=1, ge=1)
    update: str = Field(min_length=1, max_length=MAX_DETAILS_LENGTH)
    fields: dict[FieldName, str] = Field(default_factory=dict)
    evidence_group: str | None = None
    originating_turn: str


class DialogueData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    draft: Draft | None = None
    pending_offer: PendingOffer | None = None
    pending_prompt: PendingPrompt | None = None
    evidence_group: str | None = None
    last_case_reference: str | None = None
    pending_case_update: PendingCaseUpdate | None = None
    last_receipt: OperationReceipt | None = None


class FieldReference(BaseModel):
    """The model-safe portion of a private, turn-local value reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    field: FieldName
    source: Literal["current_input", "draft"]


@dataclass(frozen=True)
class _PrivateField:
    public: FieldReference
    value: str


class FieldReferences:
    def __init__(self, values: list[_PrivateField], ambiguous_fields: tuple[FieldName, ...] = ()):
        self._values = {item.public.id: item for item in values}
        self.ambiguous_fields = ambiguous_fields

    @property
    def public(self) -> tuple[FieldReference, ...]:
        return tuple(item.public for item in self._values.values())

    def resolve(self, reference: str, expected_field: FieldName | None = None) -> tuple[FieldName, str]:
        item = self._values.get(reference)
        if not item or (expected_field and item.public.field != expected_field):
            raise ValueError("unknown or mismatched customer field reference")
        return item.public.field, item.value

    def resolve_current(
        self, reference: str, expected_field: FieldName | None = None
    ) -> tuple[FieldName, str]:
        item = self._values.get(reference)
        if not item or item.public.source != "current_input":
            raise ValueError("field reference is not from the current customer input")
        return self.resolve(reference, expected_field)


class FieldExtraction(BaseModel):
    values: dict[FieldName, str] = Field(default_factory=dict)
    ambiguous_fields: tuple[FieldName, ...] = ()
    invalid_fields: tuple[FieldName, ...] = ()


class DraftValidation(BaseModel):
    valid: bool
    missing_fields: tuple[FieldName, ...] = ()
    invalid_fields: tuple[FieldName, ...] = ()


class DraftOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["prepared", "rejected"]
    dialogue: DialogueData
    validation: DraftValidation
    reason: str | None = None
    operation: OperationReceipt | None = None


class OwnedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    public_id: str
    status: str
    version: int = Field(default=1, ge=1)
    update_count: int = Field(ge=0)


class StagedCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    case_id: str
    public_id: str
    update: str = Field(min_length=1, max_length=MAX_DETAILS_LENGTH)
    fields: dict[FieldName, str] = Field(default_factory=dict)
    reopen: bool = False
    evidence_group: str | None = None
    prompt_id: str
    confirmed: bool = False


class CaseOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["prepared", "rejected"]
    dialogue: DialogueData
    reason: str | None = None
    operation: StagedCaseUpdate | None = None


def consent_answer(text: str, language: Language) -> bool | None:
    normalized = text.strip().lower().rstrip(".!。")
    yes = {
        "en": {"yes", "i agree", "i consent", "agree", "consent"},
        "ms": {"ya", "saya setuju", "setuju", "yes", "i agree"},
        "zh": {"同意", "我同意", "是", "yes", "i agree"},
    }[language]
    no = {
        "en": {"no", "no thanks", "i decline", "decline"},
        "ms": {"tidak", "tidak setuju", "tak mahu", "no", "no thanks"},
        "zh": {"不同意", "拒绝", "不要", "否", "no", "no thanks"},
    }[language]
    if normalized in yes:
        return True
    if normalized in no:
        return False
    return None


def is_cancel(text: str, language: Language) -> bool:
    return text.strip().lower().rstrip(".!。") in {
        "en": {
            "cancel ticket", "stop", "never mind", "stop bro",
            "i don't need a human follow-up", "i do not want a ticket",
        },
        "ms": {"batalkan tiket", "berhenti", "tak jadi", "saya tak perlukan pegawai"},
        "zh": {"取消工单", "停止", "算了", "我不需要人工跟进"},
    }[language]


def is_pause_request(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".!?。！？")
    if normalized in {
        "pause",
        "pause this for later",
        "hold on",
        "later",
        "jeda",
        "nanti",
        "tangguh",
        "berhenti dulu",
        "暂停",
        "稍后",
        "等一下",
        "先停一下",
    }:
        return True
    return bool(
        re.fullmatch(
            r"(?i)(?:please\s+)?(?:pause(?:\s+(?:this|the\s+draft))?(?:\s+for\s+(?:now|later))?|hold\s+on(?:\s+(?:for\s+)?(?:now|later))?)",
            normalized,
        )
    )


def is_skip(text: str, language: Language) -> bool:
    return text.strip().lower().rstrip(".!。") in {
        "en": {"skip", "not applicable", "none", "no evidence"},
        "ms": {"langkau", "tidak berkenaan", "tiada", "tiada bukti", "skip"},
        "zh": {"跳过", "不适用", "没有", "没有证据", "skip"},
    }[language]


def is_done(text: str, language: Language) -> bool:
    return text.strip().lower().rstrip(".!。") in {
        "en": {"done", "finished", "that's all", "that is all"},
        "ms": {"selesai", "dah selesai", "itu sahaja", "done"},
        "zh": {"完成", "好了", "就这些", "done"},
    }[language]


def is_submit(text: str) -> bool:
    return text.strip().lower().rstrip(".!。") in SUBMIT


def is_explicit_withdrawal(text: str) -> bool:
    return text.strip().lower().rstrip(".!。") in EXPLICIT_WITHDRAWAL


def _is_anchored_negative_request(text: str, language: Language) -> bool:
    normalized = text.strip().lower().rstrip(".!?。！？")
    # ponytail: an initial refusal makes mixed intent ambiguous; clarify instead
    # of letting an unverified later phrase override it.
    patterns = {
        "en": r"(?:no[,:]?\s+)?(?:i|we)\s+(?:do\s+not|don't|dont)\s+(?:want|need|require|wish)\b|(?:i(?:\s+am|'m)|we(?:\s+are|'re))\s+not\s+(?:interested|looking|proposing|requesting|seeking)\b|(?:do\s+not|don't|dont)\s+(?:delete|remove|cancel|close|disable)\b|no\s+(?:a\s+)?(?:ticket|human(?:\s+follow[- ]?up)?|support\s+ticket)\b",
        "ms": r"(?:saya|kami)?\s*(?:tidak|tak)\s+(?:mahu|hendak|perlu|perlukan)\b|jangan\s+(?:padam|hapus|buang|tutup)\b",
        "zh": r"^我?(?:不想|不要|不需要|无需|拒绝|不是要)|^(?:不要|别)(?:删除|刪除|关闭|取消|停用)",
    }
    factual_negatives = {
        "en": r"(?:i(?:\s+am|'m)|we(?:\s+are|'re))\s+(?:not\s+(?:in\s+)?(?:danger|unsafe|at\s+risk|injured|hurt)|safe)(?:\s+(?:now|currently))?(?:\s*[,;]\s*(?:i(?:\s+am|'m)|we(?:\s+are|'re))\s+(?:just\s+|only\s+)?(?:reading|reviewing|checking|asking\s+about|looking\s+at)\b.*)?",
        "ms": r"(?:saya|kami)\s+(?:(?:tidak|tak)\s+(?:dalam\s+bahaya|cedera|terancam|diganggu)|selamat)(?:\s+(?:sekarang|kini))?(?:\s*[,;]\s*(?:saya|kami)\s+(?:hanya\s+)?(?:membaca|menyemak|melihat|bertanya\s+tentang)\b.*)?",
        "zh": r"(?:我|我们)(?:现在)?(?:(?:没有|没|并无)(?:危险|受伤|受到威胁|被骚扰)|(?:很)?安全)(?:\s*[，,；;]\s*(?:我|我们)?(?:只是在?|只在|只是|仅仅在?|仅在)?(?:阅读|查看|了解|咨询).*)?",
    }
    return bool(
        re.match(r"^(?:" + patterns[language] + r")", normalized)
        or re.fullmatch(factual_negatives[language], normalized)
    )


def _is_quoted_or_hypothetical(text: str) -> bool:
    stripped = text.strip()
    if re.fullmatch(r'(?:"[^"\n]+"|“[^”\n]+”|‘[^’\n]+’)[.!?。！？]?', stripped):
        return True
    return bool(
        re.match(
            r"(?i)^(?:if|what\s+if|suppose|imagine|hypothetical|jika|sekiranya|kalau|andaikan|hipotesis)\b|^(?:如果|假如|假设|假想|万一)",
            stripped,
        )
    )


def is_new_draft_control(text: str, language: Language) -> bool:
    """Recognize only explicit controls that must not start a fresh draft."""
    return (
        is_cancel(text, language)
        or is_explicit_withdrawal(text)
        or is_pause_request(text)
        or _is_anchored_negative_request(text, language)
        or _is_quoted_or_hypothetical(text)
        or not _is_description_eligible(text)
    )


def _is_uncertain_control(text: str) -> bool:
    return bool(re.search(
        r"(?i)[?？]|(?<!\w)[\"']|[\"'](?!\w)|[“”‘’]|\b(?:if|what|why|how|would|could|should|suppose|imagine|"
        r"hypothetical|not|don't|do not|isn't|aren't|cannot|can't|tidak|bukan|jangan|"
        r"jika|sekiranya|bagaimana|adakah|boleh)\b|\bcan\s+(?:i|you)\b|"
        r"\b(?:do|does)\s+(?:i|you|my)\b|\b(?:is|are)\s+(?:this|that|my|your)\b|"
        r"\bke\s*$|(?:如果|假如|假设|不是|没有|不要|不会|可以|如何|怎样|怎么|为何|为什么|是否)|(?:吗|呢)\s*$",
        text,
    ))


def _acknowledgement(text: str) -> bool:
    return text.strip().lower().rstrip(".!。") in {
        "hi", "hello", "hey", "thanks", "thank you", "yes", "no", "ok", "okay", "👍", "🙏",
        "你好", "谢谢", "好的", "terima kasih", "hai", "ya",
    }


def _question_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(r"(?<=[。！？；])|(?<=[.!?;])\s+|\n+", text.strip())
        if clause.strip()
    ]


def _is_question_clause(text: str) -> bool:
    clause = text.strip().strip("\"'“”‘’")
    return bool(
        re.search(r"[?？]\s*[.!。]*$", clause)
        or re.match(
            r"(?i)^(?:how|what|why|where|when|(?:please\s+)?explain|bagaimana|apakah|mengapa|adakah|bolehkah)\b|"
            r"^(?:can|could|would|do|does|is|are)\s+(?:i|you|we|they|he|she|it|there|this|that|my|your|the)\b|"
            r"^boleh\s+(?:saya|anda|kami|kita|awak)\b",
            clause,
        )
        or re.match(r"^(?:如何|怎么|什么|为什么|是否)", clause)
    )


def _asserted_detail(text: str) -> str | None:
    clauses = _question_clauses(text)
    if not clauses or all(_is_question_clause(clause) for clause in clauses):
        return None
    if not any(_is_question_clause(clause) for clause in clauses):
        return text.strip()
    return "\n".join(
        clause for clause in clauses if not _is_question_clause(clause)
    )


def _canonical_question_text(text: str) -> str | None:
    normalized = text.strip().lower().rstrip(".!?。！？")
    if normalized == "why was i charged twice":
        return "I was charged twice"
    return {
        "can i speak to a human": "I need a human",
        "can i talk to a human": "I need a human",
        "could i speak to a human": "I need a human",
        "bolehkah saya bercakap dengan manusia": "saya mahu pegawai manusia",
        "boleh saya bercakap dengan manusia": "saya mahu pegawai manusia",
        "可以找人工吗": "转人工",
        "可以转人工吗": "转人工",
        "可以联系人工吗": "转人工",
    }.get(normalized)


def _is_description_eligible(text: str) -> bool:
    if _asserted_detail(text) == text.strip():
        return True
    assessment = assess_message(text)
    if assessment.issue_type != "general_faq":
        return True
    canonical = _canonical_question_text(text)
    return bool(
        canonical and assess_message(canonical).issue_type != "general_faq"
    )


def _is_customer_contact_value(text: str, start: int, end: int) -> bool:
    """Reject examples and questions before they can become contact corrections."""
    clause_start = max(text.rfind(mark, 0, start) for mark in ".!?。！？;；\n") + 1
    clause_end = min(
        (index for mark in ".!?。！？;；\n" if (index := text.find(mark, end)) >= 0),
        default=len(text),
    )
    clause = text[clause_start:clause_end]
    # ponytail: this clause heuristic rejects ambiguous natural language; upgrade to a
    # dedicated customer-confirmation UI if correction intent needs broader phrasing.
    stripped = clause.strip()
    if (
        (start and text[start - 1] in "\"'“”‘’")
        or (end < len(text) and text[end] in "\"'“”‘’")
        or (stripped[:1] in "\"'“”‘’" and stripped[-1:] in "\"'“”‘’")
        or (
            stripped[:1] in "\"'“”‘’"
            and clause_end + 1 < len(text)
            and text[clause_end + 1] in "\"'“”‘’"
        )
        or (clause_end < len(text) and text[clause_end] in "!?？")
    ):
        return False
    return not re.search(
        r"(?i)\b(?:if|what|why|how|would|could|should|suppose|imagine|hypothetical|"
        r"not|never|don't|do not|isn't|aren't|cannot|can't|tidak|bukan|jangan|"
        r"jika|sekiranya|bagaimana|adakah|boleh)\b|\bcan\s+(?:i|you)\b|"
        r"\b(?:do|does)\s+(?:i|you|my)\b|\b(?:is|are)\s+(?:this|that|my|your)\b|"
        r"\bke\s*$|(?:如果|假如|假设|不是|没有|不要|不会|可以|如何|怎样|怎么|为何|为什么|是否)|(?:吗|呢)\s*$",
        clause.lower(),
    )


def extract_customer_fields(
    request: ChatRequest, text: str, *, prompted_field: FieldName | None = None
) -> FieldExtraction:
    values: dict[FieldName, str] = {
        key: str(value)
        for key in ("name", "email", "phone_number", "account_id", "trip_id", "ride_details")
        if (value := getattr(request, key))
    }
    invalid: set[FieldName] = set()
    ambiguous: set[FieldName] = set()
    for role, label, maximum in (
        ("trip_id", r"trip(?: id)?|id perjalanan|行程编号", 120),
        ("account_id", r"account(?: id)?|id akaun|账号", 255),
    ):
        match = re.search(
            rf"(?i)(?:^|[;；\n])\s*(?:{label})\s*[:：=]\s*"
            rf"([A-Za-z0-9_-]{{1,{maximum}}})(?=\s|$|[,;，；])",
            text,
        )
        if match:
            values.setdefault(role, match.group(1))

    emails = {
        match.group()
        for match in EMAIL_PATTERN.finditer(text)
        if _is_customer_contact_value(text, match.start(), match.end())
    }
    if len(emails) > 1:
        ambiguous.add("email")
        values.pop("email", None)
    elif len(emails) == 1:
        email = normalize_email(emails.pop())
        if email:
            values.setdefault("email", email)
        else:
            invalid.add("email")

    phone_role = prompted_field == "phone_number" or bool(
        re.search(r"(?i)\b(?:phone|whatsapp|contact|telefon|nombor)\b|电话|联系", text)
    )
    phones = {
        normalize_phone_number(match.group())
        for match in PHONE_PATTERN.finditer(text)
        if (phone_role or match.group().startswith("+"))
        and _is_customer_contact_value(text, match.start(), match.end())
    } - {None}
    if len(phones) > 1:
        ambiguous.add("phone_number")
        values.pop("phone_number", None)
    elif len(phones) == 1:
        values.setdefault("phone_number", phones.pop())
    if request.phone_number:
        phone = normalize_phone_number(request.phone_number)
        if phone:
            values["phone_number"] = phone
        else:
            invalid.add("phone_number")
    elif request.channel == "whatsapp" and "phone_number" not in values:
        sender_phone = normalize_phone_number(request.external_user_id)
        if sender_phone:
            values["phone_number"] = sender_phone

    name_match = FIELD_PATTERN.search(text)
    if name_match and _is_customer_contact_value(text, name_match.start(), name_match.end()):
        name = re.split(
            r"(?i)\s+(?:and|email|e-mail|phone|emel|dan)\b",
            name_match.group(1) or name_match.group(2),
        )[0].strip()
        if not EMAIL_PATTERN.search(name) and not any(char.isdigit() for char in name) and len(name) <= 255:
            values.setdefault("name", name)
        else:
            invalid.add("name")
    elif (
        prompted_field == "name"
        and not values.get("name")
        and not EMAIL_PATTERN.search(text)
        and not _acknowledgement(text)
        and not is_done(text, request.preferred_language or "en")
        and not is_skip(text, request.preferred_language or "en")
        and not is_submit(text)
        and not _is_uncertain_control(text)
        and consent_answer(text, request.preferred_language or "en") is not True
        and re.fullmatch(
            r"(?:[A-Z][a-zA-Z'-]*|[\u4e00-\u9fff]+)"
            r"(?:[ -](?:[A-Z][a-zA-Z'-]*|[\u4e00-\u9fff]+)){0,4}",
            text.strip(),
        )
        and len(text.split()) <= 5
    ):
        values["name"] = text.strip()
    return FieldExtraction(
        values=values,
        ambiguous_fields=tuple(sorted(ambiguous)),
        invalid_fields=tuple(sorted(invalid)),
    )


def build_field_references(
    request: ChatRequest,
    text: str,
    *,
    pending_prompt: PendingPrompt | None = None,
    draft: Draft | None = None,
    include_case_update: bool = False,
    include_description: bool = False,
) -> FieldReferences:
    prompted_field = pending_prompt.field if pending_prompt and pending_prompt.purpose == "field" else None
    extraction = extract_customer_fields(request, text, prompted_field=prompted_field)
    current = dict(extraction.values)
    if (
        draft
        and draft.fields.phone_number
        and not request.phone_number
        and "phone_number" in current
        and current.get("phone_number") == normalize_phone_number(request.external_user_id)
    ):
        # The WhatsApp sender is only a default; it must not overwrite a contact
        # number the customer already supplied for the draft.
        current.pop("phone_number")
    if pending_prompt and pending_prompt.purpose == "field" and pending_prompt.field == "description":
        if len(text.strip()) >= 4 and not _acknowledgement(text) and not is_submit(text):
            current["description"] = text.strip()
    elif include_description and len(text.strip()) >= 4 and not _acknowledgement(text):
        current["description"] = text.strip()
    detail = _asserted_detail(text)
    if (
        pending_prompt
        and pending_prompt.purpose == "details"
        and detail
        and not set(current).intersection({"name", "email", "account_id", "trip_id"})
        and not (
            current.get("phone_number")
            and (
                not draft
                or current["phone_number"] != draft.fields.phone_number
                or any(character.isdigit() for character in text)
            )
        )
        and not (
            is_done(text, request.preferred_language or "en")
            or is_skip(text, request.preferred_language or "en")
            or consent_answer(text, request.preferred_language or "en") is not None
        )
    ):
        current["ride_details"] = detail
    language = request.preferred_language or "en"
    if (
        include_case_update
        and 8 <= len(text.strip()) <= MAX_DETAILS_LENGTH
        and not _acknowledgement(text)
        and not is_done(text, language)
        and not is_skip(text, language)
        and not is_submit(text)
        and not is_cancel(text, language)
        and consent_answer(text, language) is None
    ):
        current["description"] = text.strip()

    values: list[_PrivateField] = []
    for source, fields in (("current_input", current), ("draft", draft.fields.model_dump(exclude_none=True) if draft else {})):
        for field, value in fields.items():
            if source == "draft" and field in current:
                continue
            public = FieldReference(id=str(uuid.uuid4()), field=field, source=source)
            values.append(_PrivateField(public, value))
    return FieldReferences(values, extraction.ambiguous_fields)


def new_draft(
    *,
    expiry_minutes: int,
    now: datetime | None = None,
    issue_type: IssueType = "human_escalation",
    priority: Priority = "normal",
    evidence_group: str | None = None,
) -> Draft:
    started = now or datetime.utcnow()
    return Draft(
        started_at=started,
        expires_at=started + timedelta(minutes=expiry_minutes),
        issue_type=issue_type,
        priority=priority,
        evidence_group=evidence_group,
    )


def prepare_ticket_offer(
    dialogue: DialogueData,
    references: FieldReferences,
    *,
    description_reference: str,
    issue_type: IssueType,
    originating_turn: str,
    language: Language = "en",
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    if working.draft and working.draft.status in {"active", "paused"}:
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=validate_draft(working.draft),
            reason="draft_already_exists",
        )
    try:
        field, description = references.resolve_current(description_reference, "description")
    except ValueError as exc:
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason=str(exc),
        )
    assert field == "description"
    # ponytail: optional offers require a wholly asserted issue; mixed/question
    # forms clarify first. Broader authorization needs explicit customer confirmation.
    if _asserted_detail(description) != description.strip():
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason="offer_requires_asserted_detail",
        )
    if is_new_draft_control(description, language):
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason="offer_control_not_proven",
        )
    working.pending_offer = PendingOffer(
        description=description, issue_type=issue_type, originating_turn=originating_turn
    )
    working.pending_prompt = PendingPrompt(purpose="offer", originating_turn=originating_turn)
    return DraftOperationResult(
        status="prepared", dialogue=working, validation=DraftValidation(valid=False)
    )


def accept_ticket_offer(
    dialogue: DialogueData,
    *,
    prompt_id: str,
    originating_turn: str,
    consent_prompt_turn: str,
    customer_input: str,
    language: Language,
    expiry_minutes: int,
    now: datetime | None = None,
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    prompt = working.pending_prompt
    offer = working.pending_offer
    if not offer or not prompt or prompt.purpose != "offer" or prompt.id != prompt_id or prompt.originating_turn != originating_turn:
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason="stale_or_mismatched_offer_prompt",
        )
    answer = consent_answer(customer_input, language)
    if answer is False:
        working.pending_offer = None
        working.pending_prompt = None
        return DraftOperationResult(
            status="prepared",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason="offer_declined",
        )
    if answer is not True:
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False),
            reason="offer_not_accepted",
        )
    draft = new_draft(
        expiry_minutes=expiry_minutes,
        now=now,
        issue_type=offer.issue_type,
        evidence_group=working.evidence_group,
    )
    draft.fields.description = offer.description
    draft.issue_collected = True
    working.draft = draft
    working.pending_offer = None
    working.pending_prompt = PendingPrompt(
        purpose="consent",
        draft_id=draft.id,
        version=draft.version,
        originating_turn=consent_prompt_turn,
    )
    return DraftOperationResult(
        status="prepared", dialogue=working, validation=validate_draft(draft)
    )


def validate_draft(draft: Draft) -> DraftValidation:
    missing: list[FieldName] = []
    invalid: list[FieldName] = []
    for field in ("name", "email", "phone_number", "description"):
        value = getattr(draft.fields, field)
        if not value or not value.strip():
            missing.append(field)
    if draft.fields.email and not normalize_email(draft.fields.email):
        invalid.append("email")
    if draft.fields.phone_number and not normalize_phone_number(draft.fields.phone_number):
        invalid.append("phone_number")
    return DraftValidation(valid=not missing and not invalid, missing_fields=tuple(missing), invalid_fields=tuple(invalid))


def validate_ticket_fields(
    *, consent: bool, name: str | None, email: str | None, phone_number: str | None, description: str | None
) -> None:
    if not consent or not name or not name.strip() or not normalize_email(email) or not normalize_phone_number(phone_number) or not description or not description.strip():
        raise ValueError("consent, name, email, phone number, and description are required")


def update_ticket_draft(
    dialogue: DialogueData,
    references: FieldReferences,
    field_references: dict[FieldName, str],
    *,
    expiry_minutes: int = 60,
    issue_type: IssueType | None = None,
    priority: Priority | None = None,
    now: datetime | None = None,
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    for field, reference in field_references.items():
        if field != "description":
            continue
        try:
            _, description = references.resolve_current(reference, "description")
        except ValueError:
            continue
        if not _is_description_eligible(description):
            draft = working.draft
            return DraftOperationResult(
                status="rejected",
                dialogue=working,
                validation=validate_draft(draft) if draft else DraftValidation(valid=False),
                reason="description_question_not_proven",
            )
    draft = working.draft
    if draft and draft.status in {"cancelled", "submitted"}:
        working.draft = None
        working.pending_prompt = None
        draft = None
    if draft and draft.status == "paused":
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=validate_draft(draft),
            reason="draft_paused",
        )
    if not draft:
        draft = new_draft(
            expiry_minutes=expiry_minutes,
            now=now,
            issue_type=issue_type or "human_escalation",
            priority=priority or "normal",
            evidence_group=working.evidence_group,
        )
        working.draft = draft
    if (now or datetime.utcnow()) >= draft.expires_at:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validate_draft(draft), reason="draft_expired")
    if references.ambiguous_fields:
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=DraftValidation(valid=False, invalid_fields=references.ambiguous_fields),
            reason="ambiguous_customer_values",
        )

    updates: dict[FieldName, str] = {}
    try:
        for field, reference in field_references.items():
            resolved_field, value = references.resolve(reference, field)
            updates[resolved_field] = value.strip()
    except ValueError as exc:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validate_draft(draft), reason=str(exc))

    if "email" in updates and not normalize_email(updates["email"]):
        return DraftOperationResult(status="rejected", dialogue=working, validation=DraftValidation(valid=False, invalid_fields=("email",)), reason="invalid_email")
    if "phone_number" in updates:
        phone = normalize_phone_number(updates["phone_number"])
        if not phone:
            return DraftOperationResult(status="rejected", dialogue=working, validation=DraftValidation(valid=False, invalid_fields=("phone_number",)), reason="invalid_phone_number")
        updates["phone_number"] = phone
    if "ride_details" in updates:
        combined = accumulate_ride_details(
            draft.fields.ride_details, updates["ride_details"]
        )
        if len(combined) > MAX_DETAILS_LENGTH:
            return DraftOperationResult(status="rejected", dialogue=working, validation=validate_draft(draft), reason="details_too_long")
        updates["ride_details"] = combined

    changed = False
    for field, value in updates.items():
        previous = getattr(draft.fields, field)
        if previous != value:
            changed = True
            if previous is not None:
                draft.review_required = True
            setattr(draft.fields, field, value)
    if "description" in updates:
        draft.issue_collected = True
    if "ride_details" in updates:
        draft.ride_details_collected = True
    if issue_type and issue_type != draft.issue_type:
        draft.proposed_issue_type = issue_type
        draft.review_required = True
        changed = True
    if priority and priority != draft.priority:
        draft.proposed_priority = priority
        draft.review_required = True
        changed = True
    if changed:
        draft.version += 1
        if working.pending_prompt and working.pending_prompt.purpose == "review":
            working.pending_prompt = None
    return DraftOperationResult(status="prepared", dialogue=working, validation=validate_draft(draft))


def make_prompt(
    dialogue: DialogueData,
    purpose: PromptPurpose,
    originating_turn: str,
    *,
    field: FieldName | None = None,
    case_id: str | None = None,
) -> DialogueData:
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    working.pending_prompt = PendingPrompt(
        purpose=purpose,
        field=field,
        draft_id=draft.id if draft else None,
        case_id=case_id,
        version=draft.version if draft else None,
        originating_turn=originating_turn,
    )
    return working


def record_consent(
    dialogue: DialogueData,
    *,
    prompt_id: str,
    originating_turn: str,
    customer_input: str,
    language: Language,
    now: datetime | None = None,
    api_control: bool = False,
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    prompt = working.pending_prompt
    validation = validate_draft(draft) if draft else DraftValidation(valid=False, missing_fields=("name", "email", "phone_number", "description"))
    if not draft or not prompt or prompt.purpose != "consent" or prompt.id != prompt_id or prompt.originating_turn != originating_turn or prompt.draft_id != draft.id or prompt.version != draft.version:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="stale_or_mismatched_consent_prompt")
    answer = True if api_control else consent_answer(customer_input, language)
    if answer is False:
        draft.status = "cancelled"
        draft.version += 1
        working.pending_prompt = None
        return DraftOperationResult(
            status="prepared",
            dialogue=working,
            validation=validation,
            reason="consent_declined",
        )
    if answer is not True:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="consent_not_confirmed" if answer is None else "consent_declined")
    if not draft.consent:
        draft.consent = ConsentEvidence(
            prompt_id=prompt.id,
            draft_id=draft.id,
            draft_version=draft.version,
            originating_turn=originating_turn,
            customer_input=customer_input.strip()[:64],
            recorded_at=now or datetime.utcnow(),
        )
    working.pending_prompt = None
    return DraftOperationResult(status="prepared", dialogue=working, validation=validate_draft(draft))


def request_api_ticket_submission(
    dialogue: DialogueData,
    *,
    consent_prompt_id: str,
    customer_input: str,
) -> DraftOperationResult:
    """Stage the documented API supplied-details completion against its consent prompt."""
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    validation = validate_draft(draft) if draft else DraftValidation(valid=False)
    if (
        not draft
        or draft.status != "active"
        or not validation.valid
        or not draft.consent
        or draft.consent.prompt_id != consent_prompt_id
        or draft.consent.draft_id != draft.id
    ):
        return DraftOperationResult(
            status="rejected",
            dialogue=working,
            validation=validation,
            reason="api_submission_not_authorized",
        )
    operation = OperationReceipt(
        operation_id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"ticket-api:{draft.id}:{draft.version}:{consent_prompt_id}",
            )
        ),
        prompt_id=consent_prompt_id,
        version=draft.version,
        customer_input=customer_input.strip()[:64],
    )
    return DraftOperationResult(
        status="prepared",
        dialogue=working,
        validation=validation,
        operation=operation,
    )


def set_draft_status(
    dialogue: DialogueData,
    status: Literal["active", "paused", "cancelled"],
    *,
    customer_input: str,
    language: Language,
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    if not draft:
        return DraftOperationResult(status="rejected", dialogue=working, validation=DraftValidation(valid=False), reason="no_draft")
    normalized = customer_input.strip().lower().rstrip(".!。")
    uncertain = _is_uncertain_control(customer_input)
    explicit = {
        "paused": not uncertain and (
            is_pause_request(customer_input)
            or bool(re.search(r"(?i)\b(?:pause|later|hold on|jeda|nanti|tangguh)\b|暂停|稍后|等一下", customer_input))
        ),
        "active": draft.status == "paused" and not uncertain and bool(re.search(r"(?i)\b(?:resume|continue|sambung|teruskan)\b|继续|恢复", customer_input)),
        "cancelled": is_cancel(customer_input, language) or normalized in EXPLICIT_WITHDRAWAL,
    }[status]
    if not explicit:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validate_draft(draft), reason="customer_intent_not_proven")
    if draft.status == status:
        return DraftOperationResult(status="prepared", dialogue=working, validation=validate_draft(draft))
    if draft.status in {"cancelled", "submitted"}:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validate_draft(draft), reason="draft_not_editable")
    draft.status = status
    draft.version += 1
    working.pending_prompt = None
    return DraftOperationResult(status="prepared", dialogue=working, validation=validate_draft(draft))


def prepare_ticket_review(dialogue: DialogueData, *, originating_turn: str) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    if not draft:
        return DraftOperationResult(status="rejected", dialogue=working, validation=DraftValidation(valid=False), reason="no_draft")
    validation = validate_draft(draft)
    if draft.status != "active":
        return DraftOperationResult(
            status="rejected", dialogue=working, validation=validation, reason="draft_not_active"
        )
    if not validation.valid:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="draft_incomplete")
    if not draft.consent:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="consent_required")
    draft.review_required = True
    existing = working.pending_prompt
    if not existing or existing.purpose != "review" or existing.version != draft.version:
        working.pending_prompt = PendingPrompt(
            purpose="review", draft_id=draft.id, version=draft.version, originating_turn=originating_turn
        )
    return DraftOperationResult(status="prepared", dialogue=working, validation=validation)


def request_ticket_submission(
    dialogue: DialogueData,
    *,
    prompt_id: str,
    originating_turn: str,
    customer_input: str,
    language: Language,
    api_control: bool = False,
) -> DraftOperationResult:
    working = dialogue.model_copy(deep=True)
    draft = working.draft
    prompt = working.pending_prompt
    if not draft:
        return DraftOperationResult(status="rejected", dialogue=working, validation=DraftValidation(valid=False), reason="no_draft")
    validation = validate_draft(draft)
    if (
        draft.status == "submitted"
        and working.last_receipt
        and working.last_receipt.prompt_id == prompt_id
    ):
        return DraftOperationResult(
            status="prepared",
            dialogue=working,
            validation=validation,
            operation=working.last_receipt,
        )
    if draft.status != "active":
        return DraftOperationResult(
            status="rejected", dialogue=working, validation=validation, reason="draft_not_active"
        )
    if not validation.valid:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="draft_incomplete")
    if not draft.consent:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="consent_required")
    if is_explicit_withdrawal(customer_input):
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="consent_withdrawn")
    if not prompt or prompt.id != prompt_id or prompt.originating_turn != originating_turn or prompt.draft_id != draft.id or prompt.version != draft.version:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="stale_or_mismatched_submission_prompt")

    reply = customer_input.strip()
    if draft.review_required:
        authorized = prompt.purpose == "review" and (is_submit(reply) or api_control)
    else:
        authorized = prompt.purpose == "details" and (
            is_done(reply, language) or is_skip(reply, language) or consent_answer(reply, language) is False or api_control
        )
    if not authorized:
        return DraftOperationResult(status="rejected", dialogue=working, validation=validation, reason="submission_not_confirmed")
    operation = OperationReceipt(
        operation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"ticket:{draft.id}:{draft.version}:{prompt.id}")),
        prompt_id=prompt.id,
        version=draft.version,
        customer_input=reply,
    )
    if working.last_receipt and working.last_receipt.operation_id == operation.operation_id:
        return DraftOperationResult(status="prepared", dialogue=working, validation=validation, operation=working.last_receipt)
    return DraftOperationResult(status="prepared", dialogue=working, validation=validation, operation=operation)


def owned_case_view(
    *,
    case_id: str,
    public_id: str,
    status: str,
    channel: str,
    external_user_id: str,
    expected_channel: str,
    expected_external_user_id: str,
    update_count: int,
    version: int = 1,
) -> OwnedCase | None:
    if channel != expected_channel or external_user_id != expected_external_user_id:
        return None
    return OwnedCase(
        id=case_id, public_id=public_id, status=status, version=version, update_count=update_count
    )


def request_case_update(
    dialogue: DialogueData,
    case: OwnedCase,
    references: FieldReferences,
    *,
    update_reference: str | None = None,
    originating_turn: str,
    field_references: dict[FieldName, str] | None = None,
    customer_input: str | None = None,
    prompt_id: str | None = None,
    language: Language = "en",
) -> CaseOperationResult:
    working = dialogue.model_copy(deep=True)
    if case.update_count >= MAX_CASE_UPDATES:
        return CaseOperationResult(status="rejected", dialogue=working, reason="case_update_limit")
    pending = working.pending_case_update
    prompt = working.pending_prompt
    if customer_input is not None and pending:
        if (
            not prompt
            or prompt.purpose != "case_confirmation"
            or prompt.id != prompt_id
            or prompt.case_id != case.id
            or prompt.version != case.version
            or prompt.operation_id != pending.operation_id
            or prompt.originating_turn != originating_turn
            or pending.case_id != case.id
            or pending.case_version != case.version
            or consent_answer(customer_input, language) is not True
        ):
            return CaseOperationResult(
                status="rejected", dialogue=working, reason="case_update_not_confirmed"
            )
        return CaseOperationResult(
            status="prepared",
            dialogue=working,
            operation=StagedCaseUpdate(
                operation_id=pending.operation_id,
                case_id=case.id,
                public_id=case.public_id,
                update=pending.update,
                fields=pending.fields,
                reopen=case.status == "closed",
                evidence_group=pending.evidence_group,
                prompt_id=prompt.id,
                confirmed=True,
            ),
        )
    if update_reference is None:
        return CaseOperationResult(
            status="rejected", dialogue=working, reason="case_update_reference_required"
        )
    try:
        update_field, update = references.resolve_current(update_reference)
        if update_field not in {"description", "ride_details"}:
            raise ValueError("case update must reference the current customer message")
        fields = {}
        for field, reference in (field_references or {}).items():
            if field in {"description", "ride_details"}:
                raise ValueError("case narrative belongs in the case update")
            value = references.resolve(reference, field)[1]
            if field == "email":
                value = normalize_email(value)
                if not value:
                    raise ValueError("invalid email")
            elif field == "phone_number":
                value = normalize_phone_number(value)
                if not value:
                    raise ValueError("invalid phone number")
            elif not value.strip():
                raise ValueError("empty case field")
            fields[field] = value
    except ValueError as exc:
        return CaseOperationResult(status="rejected", dialogue=working, reason=str(exc))
    if not update.strip() or len(update) > MAX_DETAILS_LENGTH:
        return CaseOperationResult(status="rejected", dialogue=working, reason="invalid_case_update")

    operation_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"case:{case.id}:{case.version}:{update.strip()}:{sorted(fields.items())}",
        )
    )
    if customer_input is None:
        if prompt and prompt.purpose == "case_confirmation":
            if (
                prompt.case_id != case.id
                or prompt.version != case.version
                or prompt.operation_id != operation_id
            ):
                return CaseOperationResult(
                    status="rejected", dialogue=working, reason="conflicting_case_update"
                )
        else:
            working.pending_prompt = PendingPrompt(
                purpose="case_confirmation",
                case_id=case.id,
                version=case.version,
                operation_id=operation_id,
                originating_turn=originating_turn,
            )
            prompt = working.pending_prompt
        operation = StagedCaseUpdate(
            operation_id=operation_id,
            case_id=case.id,
            public_id=case.public_id,
            update=update.strip(),
            fields=fields,
            reopen=case.status == "closed",
            evidence_group=working.evidence_group,
            prompt_id=prompt.id,
        )
        working.pending_case_update = PendingCaseUpdate(
            operation_id=operation.operation_id,
            case_id=case.id,
            case_reference=case.public_id,
            case_version=case.version,
            update=operation.update,
            fields=operation.fields,
            evidence_group=operation.evidence_group,
            originating_turn=originating_turn,
        )
        return CaseOperationResult(status="prepared", dialogue=working, operation=operation)

    if (
        not prompt
        or prompt.purpose != "case_confirmation"
        or prompt.id != prompt_id
        or prompt.case_id != case.id
        or prompt.version != case.version
        or prompt.operation_id != operation_id
        or prompt.originating_turn != originating_turn
        or consent_answer(customer_input, language) is not True
    ):
        return CaseOperationResult(status="rejected", dialogue=working, reason="case_update_not_confirmed")
    operation = StagedCaseUpdate(
        operation_id=operation_id,
        case_id=case.id,
        public_id=case.public_id,
        update=update.strip(),
        fields=fields,
        reopen=case.status == "closed",
        evidence_group=working.evidence_group,
        prompt_id=prompt.id,
        confirmed=True,
    )
    return CaseOperationResult(status="prepared", dialogue=working, operation=operation)
