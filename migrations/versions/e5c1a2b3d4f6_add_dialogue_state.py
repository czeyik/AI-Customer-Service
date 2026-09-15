"""Add typed dialogue state and convert legacy ticket drafts.

Revision ID: e5c1a2b3d4f6
Revises: d9010a1b2c3d
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op


revision = "e5c1a2b3d4f6"
down_revision = "d9010a1b2c3d"
branch_labels = None
depends_on = None

ACTIVE_STATES = {
    "awaiting_consent": ("consent", None),
    "awaiting_name": ("field", "name"),
    "awaiting_email": ("field", "email"),
    "awaiting_phone": ("field", "phone_number"),
    "awaiting_issue": ("field", "description"),
    "awaiting_ride_details": ("details", "ride_details"),
    "awaiting_additional_details": ("details", None),
    "awaiting_review": ("review", None),
}
KNOWN_STATES = {"idle", "paused", "awaiting_case_confirmation", *ACTIVE_STATES}
ISSUE_TYPES = {
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
}
PRIORITIES = {"normal", "high", "urgent"}
FIELD_LIMITS = {
    "name": 255,
    "email": 255,
    "phone_number": 32,
    "account_id": 255,
    "trip_id": 120,
    "description": 4096,
    "ride_details": 2000,
}
CASE_FIELD_NAMES = {"name", "email", "phone_number", "account_id", "trip_id"}
YES = {
    "yes",
    "i agree",
    "i consent",
    "agree",
    "consent",
    "ya",
    "saya setuju",
    "setuju",
    "同意",
    "我同意",
    "是",
}
CONSENT_PROMPT_MARKERS = ("privacy-notice", "notis privasi", "隐私声明")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+\d{8,15}$")


def _expiry_minutes() -> int:
    try:
        value = int(os.getenv("INTAKE_EXPIRY_MINUTES", "60"))
    except ValueError as exc:
        raise RuntimeError("INTAKE_EXPIRY_MINUTES must be an integer before migration") from exc
    if not 5 <= value <= 1440:
        raise RuntimeError("INTAKE_EXPIRY_MINUTES must be between 5 and 1440 before migration")
    return value


def _text(data: dict, key: str, limit: int, *, required: bool = False) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {key}")
    return value.strip()


def _boolean(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"invalid {key}")
    return value


def _string_list(data: dict, key: str) -> list[str]:
    values = data.get(key, [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError(f"invalid {key}")
    return values


def _started_at(row: dict, data: dict) -> datetime:
    raw = data.get("started_at")
    if raw is None:
        return row["created_at"]
    if not isinstance(raw, str):
        raise ValueError("invalid started_at")
    try:
        started = datetime.fromisoformat(raw)
        return started.astimezone(timezone.utc).replace(tzinfo=None) if started.tzinfo else started
    except ValueError as exc:
        raise ValueError("invalid started_at") from exc


def _latest_outbound(messages: list[dict]) -> dict | None:
    return messages[-1] if messages and messages[-1]["direction"] == "outbound" else None


def _originating_inbound(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message["direction"] == "inbound":
            return message["id"]
    raise ValueError("missing originating inbound message")


def _consent_evidence(messages: list[dict], draft_id: str, started_at: datetime) -> dict | None:
    for index in range(len(messages) - 2, -1, -1):
        prompt, reply = messages[index : index + 2]
        if (
            prompt["direction"] == "outbound"
            and started_at <= prompt["created_at"] <= reply["created_at"]
            and any(marker in prompt["content"].lower() for marker in CONSENT_PROMPT_MARKERS)
            and reply["direction"] == "inbound"
            and reply["content"].strip().lower().rstrip(".!。") in YES
        ):
            return {
                "prompt_id": prompt["id"],
                "draft_id": draft_id,
                "draft_version": 1,
                "originating_turn": prompt["id"],
                "customer_input": reply["content"].strip(),
                "recorded_at": reply["created_at"].isoformat(),
            }
    return None


def _owned_ticket(
    tickets: dict[str, dict], ticket_id: str | None, row: dict, *, required: bool = False
) -> dict | None:
    if ticket_id is None and not required:
        return None
    if not isinstance(ticket_id, str) or ticket_id not in tickets:
        raise ValueError("missing referenced ticket")
    ticket = tickets[ticket_id]
    if (
        ticket["channel"] != row["channel"]
        or ticket["external_user_id"] != row["external_user_id"]
    ):
        raise ValueError("referenced ticket is not owned by conversation")
    return ticket


def _draft(row: dict, data: dict, status: str, messages: list[dict]) -> dict:
    issue_type = data.get("issue_type")
    priority = data.get("urgency")
    if issue_type not in ISSUE_TYPES:
        raise ValueError("invalid issue_type")
    if priority not in PRIORITIES:
        raise ValueError("invalid urgency")
    fields = {
        key: value
        for key, limit in FIELD_LIMITS.items()
        if (value := _text(data, key, limit)) is not None
    }
    if "description" not in fields:
        raise ValueError("missing description")
    if fields.get("email") and not EMAIL_RE.fullmatch(fields["email"]):
        raise ValueError("invalid email")
    if fields.get("phone_number") and not PHONE_RE.fullmatch(fields["phone_number"]):
        raise ValueError("invalid phone_number")
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise ValueError("invalid evidence")
    attachment_count = data.get("attachment_count", len(evidence))
    if type(attachment_count) is not int or attachment_count < 0:
        raise ValueError("invalid attachment_count")
    started = _started_at(row, data)
    draft_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dialogue-draft:{row['id']}"))
    consent_given = _boolean(data, "consent")
    consent = (
        _consent_evidence(messages, draft_id, started)
        if consent_given and data.get("started_at") is not None else None
    )
    proposed_issue_type = data.get("proposed_issue_type")
    if proposed_issue_type is not None and proposed_issue_type not in ISSUE_TYPES:
        raise ValueError("invalid proposed_issue_type")
    return {
        "id": draft_id,
        "version": 1,
        "status": status,
        "started_at": started.isoformat(),
        "expires_at": (started + timedelta(minutes=_expiry_minutes())).isoformat(),
        "fields": fields,
        "issue_collected": _boolean(data, "issue_collected"),
        "ride_details_collected": _boolean(data, "ride_details_collected"),
        "details_complete": _boolean(data, "details_complete"),
        "issue_type": issue_type,
        "proposed_issue_type": proposed_issue_type,
        "priority": priority,
        "proposed_priority": "high" if proposed_issue_type and priority != "urgent" else None,
        "consent": consent,
        "review_required": _boolean(data, "review_required")
        or row["intake_state"] == "awaiting_review",
        "evidence_group": _text(data, "evidence_group", 36),
        "evidence": evidence,
        "attachment_count": attachment_count,
        "safety_flags": _string_list(data, "safety_flags"),
        "safety_notes": _string_list(data, "safety_notes"),
    }


def convert_legacy_dialogue(
    row: dict, messages: list[dict], tickets: dict[str, dict]
) -> dict:
    state, data = row["intake_state"], row["intake_data"]
    if state not in KNOWN_STATES:
        raise ValueError(f"unknown intake_state {state!r}")
    if not isinstance(data, dict):
        raise ValueError("intake_data is not an object")

    result = {
        "schema_version": 1,
        "draft": None,
        "pending_offer": None,
        "pending_prompt": None,
        "evidence_group": None,
        "last_case_reference": None,
        "pending_case_update": None,
        "last_receipt": None,
    }
    last_ticket = _owned_ticket(tickets, data.get("last_ticket_id"), row)
    if last_ticket:
        result["last_case_reference"] = last_ticket["public_id"]

    latest_outbound = _latest_outbound(messages)
    prompt_id = _text(data, "prompt_id", 64)
    if state in ACTIVE_STATES or state == "paused":
        if state == "paused" and data.get("resume_state") not in ACTIVE_STATES:
            raise ValueError("invalid resume_state")
        draft = _draft(row, data, "paused" if state == "paused" else "active", messages)
        result["draft"] = draft
        if state in ACTIVE_STATES and prompt_id and latest_outbound:
            purpose, field = ACTIVE_STATES[state]
            result["pending_prompt"] = {
                "id": prompt_id,
                "purpose": purpose,
                "field": field,
                "draft_id": draft["id"],
                "case_id": None,
                "version": draft["version"],
                "operation_id": None,
                "originating_turn": latest_outbound["id"],
            }
        return result

    evidence_group = _text(data, "evidence_group", 36)
    result["evidence_group"] = evidence_group
    if state == "idle" and data.get("pending_offer") is not None:
        offer = data["pending_offer"]
        if not isinstance(offer, dict) or offer.get("issue_type") not in ISSUE_TYPES:
            raise ValueError("invalid pending_offer")
        result["pending_offer"] = {
            "description": _text(offer, "description", 4096, required=True),
            "issue_type": offer["issue_type"],
            "originating_turn": _originating_inbound(messages),
        }
        if prompt_id and latest_outbound:
            result["pending_prompt"] = {
                "id": prompt_id,
                "purpose": "offer",
                "field": None,
                "draft_id": None,
                "case_id": None,
                "version": None,
                "operation_id": None,
                "originating_turn": latest_outbound["id"],
            }
        return result

    if state == "awaiting_case_confirmation":
        case = _owned_ticket(tickets, data.get("case_id"), row, required=True)
        update = _text(data, "case_update", 2000, required=True)
        case_fields = data.get("case_fields", {})
        if not isinstance(case_fields, dict) or set(case_fields) - CASE_FIELD_NAMES:
            raise ValueError("invalid case_fields")
        clean_fields = {
            key: _text(case_fields, key, FIELD_LIMITS[key], required=True)
            for key in case_fields
        }
        originating_turn = _originating_inbound(messages)
        operation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"case:{case['id']}:1:{update}:{sorted(clean_fields.items())}",
            )
        )
        result["last_case_reference"] = case["public_id"]
        result["pending_case_update"] = {
            "operation_id": operation_id,
            "case_id": case["id"],
            "case_reference": case["public_id"],
            "case_version": 1,
            "update": update,
            "fields": clean_fields,
            "evidence_group": evidence_group,
            "originating_turn": originating_turn,
        }
        if prompt_id and latest_outbound:
            result["pending_prompt"] = {
                "id": prompt_id,
                "purpose": "case_confirmation",
                "field": None,
                "draft_id": None,
                "case_id": case["id"],
                "version": 1,
                "operation_id": operation_id,
                "originating_turn": latest_outbound["id"],
            }
    return result


def _load_rows(bind) -> tuple[list[dict], dict[str, list[dict]], dict[str, dict]]:
    conversations = [
        dict(row._mapping)
        for row in bind.execute(
            sa.text(
                "SELECT id, channel, external_user_id, intake_state, intake_data, created_at "
                "FROM conversations ORDER BY id"
            )
        )
    ]
    messages: dict[str, list[dict]] = {}
    for row in bind.execute(
        sa.text(
            "SELECT id, conversation_id, direction, content, created_at FROM messages "
            "ORDER BY conversation_id, created_at, id"
        )
    ):
        item = dict(row._mapping)
        messages.setdefault(item.pop("conversation_id"), []).append(item)
    tickets = {
        item["id"]: item
        for row in bind.execute(
            sa.text("SELECT id, public_id, channel, external_user_id FROM tickets")
        )
        for item in [dict(row._mapping)]
    }
    return conversations, messages, tickets


def _converted_rows(bind) -> list[tuple[str, dict]]:
    rows, messages, tickets = _load_rows(bind)
    converted, errors = [], []
    for row in rows:
        try:
            converted.append(
                (row["id"], convert_legacy_dialogue(row, messages.get(row["id"], []), tickets))
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{row['id']}: {exc}")
    if errors:
        shown = "; ".join(errors[:25])
        suffix = f"; plus {len(errors) - 25} more" if len(errors) > 25 else ""
        raise RuntimeError(
            f"Dialogue migration preflight failed for {len(errors)} conversation(s): "
            f"{shown}{suffix}. Repair only the listed legacy intake_state/intake_data or "
            "restore the pre-migration backup, then rerun alembic upgrade; no rows were converted."
        )
    return converted


def upgrade() -> None:
    bind = op.get_bind()
    converted_rows = _converted_rows(bind)
    op.add_column(
        "conversations",
        sa.Column("dialogue_data", sa.JSON(), nullable=True, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "conversations",
        sa.Column("dialogue_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    for conversation_id, dialogue_data in converted_rows:
        bind.execute(
            sa.text(
                "UPDATE conversations SET dialogue_data = CAST(:dialogue_data AS JSON) "
                "WHERE id = :conversation_id"
            ),
            {
                "conversation_id": conversation_id,
                "dialogue_data": json.dumps(dialogue_data, ensure_ascii=False),
            },
        )
    op.alter_column("conversations", "dialogue_data", nullable=False, server_default=None)
    op.alter_column("conversations", "dialogue_revision", server_default=None)


def downgrade() -> None:
    op.drop_column("conversations", "dialogue_revision")
    op.drop_column("conversations", "dialogue_data")
