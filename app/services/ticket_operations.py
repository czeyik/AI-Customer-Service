from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog, SupportNotification, Ticket, TicketNote


ALLOWED_TRANSITIONS = {"open": "in_progress", "in_progress": "closed"}


def _audit(db: Session, actor: AdminUser, event_type: str, ticket: Ticket, **details: str) -> None:
    db.add(
        AuditLog(
            actor=actor.username,
            event_type=event_type,
            details={"ticket_id": ticket.id, "public_id": ticket.public_id, **details},
        )
    )


def _admin_email_notification(
    db: Session, ticket: Ticket, admin: AdminUser, event_type: str
) -> None:
    db.add(
        SupportNotification(
            ticket_id=ticket.id,
            recipient_admin_id=admin.id,
            channel="email",
            recipient=admin.email,
            event_type=event_type,
            payload={"public_id": ticket.public_id, "urgency": ticket.urgency},
        )
    )


def queue_new_ticket_notifications(db: Session, ticket: Ticket) -> None:
    recipients = (
        db.query(AdminUser)
        .filter(
            AdminUser.is_active.is_(True),
            (
                AdminUser.notify_new_tickets.is_(True)
                if ticket.urgency != "urgent"
                else AdminUser.notify_urgent_tickets.is_(True)
            ),
        )
        .all()
    )
    event_type = "urgent_ticket_created" if ticket.urgency == "urgent" else "ticket_created"
    for admin in recipients:
        _admin_email_notification(db, ticket, admin, event_type)
        if ticket.urgency == "urgent" and admin.phone_number:
            db.add(
                SupportNotification(
                    ticket_id=ticket.id,
                    recipient_admin_id=admin.id,
                    channel="whatsapp",
                    recipient=admin.phone_number,
                    event_type=event_type,
                    payload={
                        "public_id": ticket.public_id,
                        "urgency": ticket.urgency,
                        "language": "en",
                    },
                )
            )


def reopen_closed_ticket_for_customer(
    db: Session, *, channel: str, external_user_id: str
) -> Ticket | None:
    ticket = (
        db.query(Ticket)
        .filter_by(channel=channel, external_user_id=external_user_id, status="closed")
        .order_by(Ticket.closed_at.desc())
        .first()
    )
    if not ticket:
        return None
    ticket.status = "open"
    ticket.closed_at = None
    db.add(
        AuditLog(
            actor=f"{channel}:{external_user_id}",
            event_type="ticket_reopened_by_customer",
            details={"ticket_id": ticket.id, "public_id": ticket.public_id},
        )
    )
    for admin in db.query(AdminUser).filter_by(is_active=True, notify_new_tickets=True).all():
        _admin_email_notification(db, ticket, admin, "ticket_reopened")
    return ticket


def assign_ticket(db: Session, *, ticket: Ticket, assignee: AdminUser, actor: AdminUser) -> None:
    if not actor.is_active or not assignee.is_active:
        raise ValueError("acting and assigned administrators must be active")
    previous = ticket.assigned_admin_id
    if previous == assignee.id:
        return
    ticket.assigned_admin_id = assignee.id
    _audit(
        db,
        actor,
        "ticket_assigned",
        ticket,
        previous_admin_id=previous or "",
        assigned_admin_id=assignee.id,
    )
    for admin in db.query(AdminUser).filter_by(is_active=True, notify_new_tickets=True).all():
        _admin_email_notification(db, ticket, admin, "ticket_reassigned")
    db.commit()


def update_ticket_status(
    db: Session, *, ticket: Ticket, new_status: str, actor: AdminUser
) -> None:
    if not actor.is_active:
        raise ValueError("the acting administrator must be active")
    if new_status == ticket.status:
        return
    if ALLOWED_TRANSITIONS.get(ticket.status) != new_status:
        raise ValueError(f"invalid ticket transition: {ticket.status} -> {new_status}")
    previous = ticket.status
    ticket.status = new_status
    ticket.closed_at = datetime.utcnow() if new_status == "closed" else None
    _audit(db, actor, "ticket_status_changed", ticket, previous=previous, status=new_status)
    db.add(
        SupportNotification(
            ticket_id=ticket.id,
            channel="whatsapp",
            recipient=ticket.phone_number,
            event_type="ticket_status_changed",
            payload={
                "public_id": ticket.public_id,
                "status": new_status,
                "language": ticket.language,
            },
        )
    )
    db.commit()


def add_ticket_note(db: Session, *, ticket: Ticket, body: str, actor: AdminUser) -> TicketNote:
    body = body.strip()
    if not actor.is_active or not body or len(body) > 4000:
        raise ValueError("an active administrator and a note of 1-4000 characters are required")
    note = TicketNote(ticket_id=ticket.id, author_admin_id=actor.id, body=body)
    db.add(note)
    _audit(db, actor, "ticket_note_added", ticket)
    db.commit()
    return note
