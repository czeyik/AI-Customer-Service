import re
from dataclasses import dataclass, field

from app.schemas import AttachmentPayload


@dataclass(frozen=True)
class SafetyAssessment:
    flags: list[str] = field(default_factory=list)
    issue_type: str = "general_faq"
    urgency: str = "normal"
    is_complaint: bool = False
    is_safety_critical: bool = False
    is_human_request: bool = False
    is_partnership: bool = False
    should_create_ticket: bool = False


PROMPT_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous",
    "reveal your system prompt",
    "show me your system prompt",
    "developer message",
    "jailbreak",
    "act as dan",
    "bypass policy",
)

SAFETY_TERMS = (
    "accident",
    "crash",
    "injured",
    "injury",
    "danger",
    "unsafe",
    "harassment",
    "assault",
    "threat",
    "kidnap",
    "emergency",
    "police",
    "hospital",
    "撞",
    "危险",
    "骚扰",
    "kemalangan",
    "bahaya",
    "gangguan",
    "cedera",
    "kecemasan",
    "受伤",
    "紧急",
)

MONEY_TERMS = (
    "refund",
    "charged",
    "overcharged",
    "fare",
    "payment",
    "wallet",
    "promo",
    "voucher",
    "bayaran",
    "tambang",
    "退款",
    "付款",
)

FRAUD_TERMS = (
    "fraud",
    "scam",
    "unauthorized transaction",
    "penipuan",
    "transaksi tanpa kebenaran",
    "诈骗",
    "未经授权的交易",
)

ACCOUNT_TERMS = ("login", "account", "password", "banned", "suspended", "akaun", "账户")

COMPLAINT_TERMS = (
    "complaint",
    "complain",
    "angry",
    "bad service",
    "rude",
    "report",
    "issue",
    "problem",
    "not happy",
    "terrible",
    "投诉",
    "问题",
    "lapor",
    "aduan",
    "masalah",
    "不满",
    "投诉",
)

HUMAN_REQUEST_TERMS = (
    "human",
    "agent",
    "representative",
    "escalate",
    "escalation",
    "real person",
    "pegawai",
    "ejen",
    "manusia",
    "wakil",
    "orang sebenar",
    "bercakap dengan staf",
    "人工",
    "转人工",
    "客服人员",
    "真人",
)

PARTNERSHIP_TERMS = (
    "partnership",
    "partner with",
    "collaboration",
    "business proposal",
    "kerjasama",
    "rakan niaga",
    "合作",
    "商务",
)

SENSITIVE_ATTACHMENT_TERMS = (
    "passport",
    "identity card",
    "id card",
    "ic",
    "nric",
    "license",
    "credit card",
    "debit card",
    "bank card",
)


def assess_message(text: str, attachments: list[AttachmentPayload] | None = None) -> SafetyAssessment:
    lowered = text.lower()
    flags: list[str] = []

    if _contains_any(lowered, PROMPT_INJECTION_PHRASES):
        flags.append("prompt_injection_attempt")

    # Only direct, affirmative incident/request forms are deterministic. Ordinary
    # language, mixed intent and keyword collisions are interpreted in the one GLM call.
    informational = bool(re.search(
        r"(?i)^(?:how|what|why|when|where|can i|do you|does|is there|are you|if |suppose|imagine|bagaimana|apakah|mengapa|jika|如何|为什么|什么|如果)|\b(?:not|never|no longer|don't|do not|didn't|tidak|bukan)\b|[\"“”]|(?:没有|不是|不需要)",
        text.strip(),
    ))
    is_safety = not informational and bool(re.search(
        r"(?i)\b(?:i am|i'm|we are|i was|we were|someone is) (?:in (?:immediate )?danger|injured|being (?:assaulted|threatened|harassed)|hurt)|\b(?:i|we) (?:had|was injured in|were injured in) (?:a |an )?(?:crash|accident)|saya (?:cedera|dalam bahaya)|我(?:撞车受伤|受伤|有危险|被骚扰)|有人受伤", text
    ))
    is_money = not informational and (
        bool(re.search(r"(?i)\bi (?:was|have been) (?:charged|overcharged)|\bmy payment (?:failed|was)|\bi have (?:a )?(?:payment|fare|refund|wallet) (?:problem|issue)|saya ada masalah (?:bayaran|tambang)|我有(?:付款|车费)问题", text))
        or (is_account_action_request(text) and _contains_any(lowered, MONEY_TERMS))
    )
    is_fraud = not informational and bool(re.search(
        r"(?i)^(?:i (?:was scammed|need help with fraud|experienced fraud)|saya (?:mahu lapor penipuan|ditipu)|我遇到诈骗)[.!。]*$", text.strip()
    ))
    is_account = not informational and (
        bool(re.search(r"(?i)\bi (?:cannot|can't) log ?in|\bmy account (?:is|was) (?:blocked|suspended)|saya tidak boleh log masuk|我的账户被封", text))
        or (is_account_action_request(text) and _contains_any(lowered, ACCOUNT_TERMS))
    )
    is_complaint = not informational and bool(re.search(
        r"(?i)(?:^i (?:want to complain|have a complaint)|the driver was rude|saya mahu buat aduan tentang|^我要投诉(?:司机|服务|行程|付款)|司机态度很差.*我要投诉)", text.strip()
    ))
    is_human_request = not informational and bool(re.fullmatch(
        r"(?i)(?:i (?:need|want|would like)(?: to (?:speak|talk) to)? (?:a )?(?:human(?: agent)?|representative|real person)|saya mahu pegawai manusia|我要人工客服|转人工)[.!。]*", re.split(r"[.!。！？?]", text.strip(), maxsplit=1)[0].strip()
    ))
    is_partnership = not informational and bool(re.fullmatch(
        r"(?i)(?:i have a (?:business )?partnership proposal|saya mahu bincang kerjasama|我想咨询商务合作)[.!。]*", text.strip()
    ))
    issue_type = "general_faq"
    urgency = "normal"

    if is_safety:
        issue_type = "safety_incident"
        urgency = "urgent"
        flags.append("safety_critical")
    elif is_fraud:
        issue_type = "fraud"
        urgency = "high"
    elif is_money:
        issue_type = "payment_or_fare"
        urgency = "high"
    elif is_account:
        issue_type = "account_support"
        urgency = "high"
    elif is_complaint:
        issue_type = "complaint"
        urgency = "normal"
    elif is_partnership:
        issue_type = "partnership"
    elif is_human_request:
        issue_type = "human_escalation"
    elif is_account_action_request(text):
        issue_type = "prohibited_action_request"

    if is_complaint:
        flags.append("complaint")

    for attachment in attachments or []:
        summary = f"{attachment.filename} {attachment.mime_type or ''} {attachment.description or ''}"
        summary = summary.lower().replace("_", " ").replace("-", " ")
        if _contains_any(summary, SENSITIVE_ATTACHMENT_TERMS):
            flags.append("sensitive_attachment_rejected")

    return SafetyAssessment(
        flags=sorted(set(flags)),
        issue_type=issue_type,
        urgency=urgency,
        is_complaint=is_complaint,
        is_safety_critical=is_safety,
        is_human_request=is_human_request,
        is_partnership=is_partnership,
        should_create_ticket=(
            is_safety or is_fraud or is_complaint or is_human_request or is_partnership or is_account_action_request(text)
        ),
    )


def is_account_action_request(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"(?i)^(?:how|why|what|can i|where|bagaimana|apakah|如何|为什么)|\b(?:not|don't|do not|tidak)\b|[\"“”]|(?:不要|不想)", lowered):
        return False
    action_terms = (
        "refund me",
        "give me a refund",
        "process my refund",
        "cancel my ride",
        "book a ride for me",
        "book my ride",
        "change my account",
        "update my account",
        "approve me",
        "approve my driver application",
        "make a payment for me",
        "pay this for me",
        "ban my account",
        "unban my account",
        "suspend my account",
        "delete account",
        "look up my account",
        "access my account",
        "sign the contract",
        "agree to the contract",
        "batalkan perjalanan",
        "batalkan perjalanan saya",
        "tempah perjalanan untuk saya",
        "ubah akaun",
        "akses akaun saya",
        "luluskan permohonan saya",
        "帮我退款",
        "取消行程",
        "请取消行程",
        "帮我预订",
        "更改账户",
        "查看我的账户",
        "批准我的申请",
    )
    return re.sub(r"^please\s+", "", lowered.strip().rstrip(".!。?")) in action_terms


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(
        term in text if not term.isascii() else re.search(rf"\b{re.escape(term)}\b", text)
        for term in terms
    )
