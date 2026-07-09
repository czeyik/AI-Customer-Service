import random
import string
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Ticket
from app.schemas import ChatRequest, TicketResponse


def generate_public_ticket_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"DUDU-{datetime.utcnow().strftime('%Y%m%d')}-{suffix}"


def create_ticket(
    db: Session,
    request: ChatRequest,
    description: str,
    issue_type: str,
    urgency: str,
    safety_flags: list[str],
) -> Ticket:
    public_id = generate_public_ticket_id()
    while db.query(Ticket).filter(Ticket.public_id == public_id).first():
        public_id = generate_public_ticket_id()

    ticket = Ticket(
        public_id=public_id,
        urgency=urgency,
        channel=request.channel,
        external_user_id=request.external_user_id,
        name=request.name,
        email=str(request.email) if request.email else None,
        account_id=request.account_id,
        user_role=request.user_role,
        issue_type=issue_type,
        language=request.preferred_language or "en",
        description=description,
        trip_id=request.trip_id,
        consent_given=request.consent_to_ticket,
        attachment_count=len(request.attachments),
        extra={"safety_flags": safety_flags},
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def to_ticket_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse(
        public_id=ticket.public_id,
        status=ticket.status,
        urgency=ticket.urgency,
        issue_type=ticket.issue_type,
    )

