from dataclasses import dataclass, field

from app.schemas import AttachmentPayload


@dataclass(frozen=True)
class SafetyAssessment:
    flags: list[str] = field(default_factory=list)
    issue_type: str = "general_faq"
    urgency: str = "normal"
    is_complaint: bool = False
    is_safety_critical: bool = False
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

    if any(phrase in lowered for phrase in PROMPT_INJECTION_PHRASES):
        flags.append("prompt_injection_attempt")

    is_safety = any(term in lowered for term in SAFETY_TERMS)
    is_money = any(term in lowered for term in MONEY_TERMS)
    is_account = any(term in lowered for term in ACCOUNT_TERMS)
    is_complaint = any(term in lowered for term in COMPLAINT_TERMS)

    issue_type = "general_faq"
    urgency = "normal"

    if is_safety:
        issue_type = "safety_incident"
        urgency = "urgent"
        flags.append("safety_critical")
    elif is_money:
        issue_type = "payment_or_fare"
        urgency = "high"
    elif is_account:
        issue_type = "account_support"
        urgency = "high"
    elif is_complaint:
        issue_type = "complaint"
        urgency = "normal"

    if is_complaint:
        flags.append("complaint")

    for attachment in attachments or []:
        summary = f"{attachment.filename} {attachment.mime_type or ''} {attachment.description or ''}"
        summary = summary.lower().replace("_", " ").replace("-", " ")
        if any(term in summary for term in SENSITIVE_ATTACHMENT_TERMS):
            flags.append("sensitive_attachment_rejected")

    return SafetyAssessment(
        flags=sorted(set(flags)),
        issue_type=issue_type,
        urgency=urgency,
        is_complaint=is_complaint,
        is_safety_critical=is_safety,
        should_create_ticket=is_safety or is_complaint,
    )


def is_account_action_request(text: str) -> bool:
    lowered = text.lower()
    action_terms = ("refund me", "cancel my ride", "change my account", "ban", "unban", "delete account")
    return any(term in lowered for term in action_terms)

