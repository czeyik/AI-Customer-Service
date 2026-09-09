from collections.abc import Generator
from http.cookies import SimpleCookie

import pyotp
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.models import (
    AdminUser,
    AuditLog,
    Base,
    RateLimitBucket,
    SupportNotification,
    Ticket,
    TicketNote,
)
from app.security import make_session_token, read_session_token, verify_password, verify_totp
from app.services.admin_accounts import provision_admin, recover_admin, set_admin_active
from app.services.ticket_operations import (
    add_ticket_note,
    assign_ticket,
    queue_new_ticket_notifications,
    reopen_closed_ticket_for_customer,
    update_ticket_status,
)
from app.routers import admin as admin_router_module
from app.routers.admin import assign, login
from app.services.notifications import process_next_notification


CZE_TOTP_FIXTURE = "JBSWY3DPEHPK3PXP"
JANE_TOTP_FIXTURE = "GEZDGNBVGY3TQOJQ"


@pytest.fixture()
def db_session(monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    monkeypatch.setenv(
        "ADMIN_TOTP_SECRETS",
        '{"admin/czeyik/totp":"JBSWY3DPEHPK3PXP","admin/jane/totp":"GEZDGNBVGY3TQOJQ",'
        '"admin/jane/recovered":"MZXW6YTBOI======"}',
    )
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield db
    finally:
        db.close()
        get_settings.cache_clear()


def provision_two_admins(db: Session) -> tuple[AdminUser, AdminUser]:
    cze = provision_admin(
        db,
        actor=None,
        username="czeyik",
        display_name="Cze Yik",
        email="czeyik@example.com",
        phone_number="+60111111111",
        password="owner-password-123",
        totp_secret_ref="admin/czeyik/totp",
        is_recovery_approver=True,
        notify_urgent_tickets=True,
    )
    jane = provision_admin(
        db,
        actor=cze,
        username="jane",
        display_name="Jane",
        email="jane@example.com",
        phone_number="+60222222222",
        password="support-password-123",
        totp_secret_ref="admin/jane/totp",
        is_cco=True,
        notify_new_tickets=True,
        notify_urgent_tickets=True,
    )
    return cze, jane


def ticket(db: Session, urgency: str = "normal") -> Ticket:
    value = Ticket(
        public_id=f"DUDU-20260909-{urgency.upper()}",
        urgency=urgency,
        channel="whatsapp",
        external_user_id="60123456789",
        name="Test Rider",
        email="rider@example.com",
        phone_number="+60123456789",
        user_role="rider",
        issue_type="complaint",
        language="en",
        description="A test issue",
        consent_given=True,
    )
    db.add(value)
    db.flush()
    return value


def test_two_named_admins_have_individual_2fa_and_audited_management(db_session: Session) -> None:
    cze, jane = provision_two_admins(db_session)

    assert cze.is_recovery_approver and jane.is_cco
    assert verify_password("owner-password-123", cze.password_hash)
    assert verify_password("support-password-123", jane.password_hash)
    assert verify_totp(cze.totp_secret_ref, pyotp.TOTP(CZE_TOTP_FIXTURE).now())
    assert verify_totp(jane.totp_secret_ref, pyotp.TOTP(JANE_TOTP_FIXTURE).now())
    assert not verify_totp(cze.totp_secret_ref, pyotp.TOTP(JANE_TOTP_FIXTURE).now())
    with pytest.raises(ValueError, match="authenticated active"):
        provision_admin(
            db_session,
            actor=None,
            username="third",
            display_name="Third Admin",
            email="third@example.com",
            password="third-password-123",
            totp_secret_ref="admin/third/totp",
        )

    session = make_session_token(jane.id, jane.auth_version, "csrf")
    set_admin_active(db_session, actor=cze, target=jane, active=False)
    assert read_session_token(session)["auth_version"] != jane.auth_version
    with pytest.raises(ValueError, match="own account"):
        set_admin_active(db_session, actor=cze, target=cze, active=False)

    recover_admin(
        db_session,
        actor=cze,
        target=jane,
        password="recovered-password-123",
        totp_secret_ref="admin/jane/recovered",
    )
    assert jane.is_active and verify_password("recovered-password-123", jane.password_hash)
    assert [row.event_type for row in db_session.query(AuditLog).order_by(AuditLog.created_at)] == [
        "admin_provisioned",
        "admin_provisioned",
        "admin_disabled",
        "admin_recovered",
    ]


def test_named_admin_login_and_ticket_mutation_require_csrf(db_session: Session) -> None:
    cze, jane = provision_two_admins(db_session)
    value = ticket(db_session)
    db_session.commit()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/login",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )
    response = login(
        request,
        username="czeyik",
        password="owner-password-123",
        totp_code=pyotp.TOTP(CZE_TOTP_FIXTURE).now(),
        db=db_session,
    )
    assert response.status_code == 303
    parsed_cookie = SimpleCookie(response.headers["set-cookie"])["dudu_admin_session"]
    assert parsed_cookie["httponly"] and parsed_cookie["samesite"] == "lax"
    cookie = parsed_cookie.value
    session = read_session_token(cookie)
    assert session["admin_id"] == cze.id

    mutation_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/admin/tickets/{value.public_id}/assign",
            "headers": [(b"cookie", f"dudu_admin_session={cookie}".encode())],
        }
    )
    with pytest.raises(HTTPException) as denied:
        assign(value.public_id, mutation_request, jane.id, "wrong", db_session, cze)
    assert denied.value.status_code == 403 and value.assigned_admin_id is None
    assigned = assign(
        value.public_id, mutation_request, jane.id, session["csrf"], db_session, cze
    )
    assert assigned.status_code == 303 and value.assigned_admin_id == jane.id
    assert db_session.query(AuditLog).filter_by(event_type="admin_login_succeeded").one()


def test_production_admin_cookie_is_secure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provision_two_admins(db_session)
    settings = Settings(_env_file=None)
    settings.environment = "production"
    monkeypatch.setattr(admin_router_module, "get_settings", lambda: settings)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/login",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )

    response = login(
        request,
        "czeyik",
        "owner-password-123",
        pyotp.TOTP(CZE_TOTP_FIXTURE).now(),
        db_session,
    )

    assert SimpleCookie(response.headers["set-cookie"])["dudu_admin_session"]["secure"]


def test_admin_login_is_rate_limited_without_storing_raw_identity(db_session: Session) -> None:
    provision_two_admins(db_session)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/login",
            "headers": [],
            "client": ("203.0.113.7", 1234),
        }
    )

    for _ in range(5):
        assert login(request, "czeyik", "wrong-password", "000000", db_session).status_code == 401
    response = login(request, "czeyik", "wrong-password", "000000", db_session)

    assert response.status_code == 429
    assert db_session.query(AuditLog).filter_by(event_type="admin_login_rate_limited").one()
    assert all(
        "czeyik" not in bucket.key_hash
        for bucket in db_session.query(RateLimitBucket).all()
    )


def test_ticket_lifecycle_assignment_notes_and_notifications_are_attributable(
    db_session: Session,
) -> None:
    cze, jane = provision_two_admins(db_session)
    normal = ticket(db_session)
    queue_new_ticket_notifications(db_session, normal)
    db_session.commit()

    created = db_session.query(SupportNotification).filter_by(event_type="ticket_created").all()
    assert [(item.recipient_admin_id, item.channel) for item in created] == [(jane.id, "email")]

    assign_ticket(db_session, ticket=normal, assignee=jane, actor=cze)
    add_ticket_note(db_session, ticket=normal, body="Waiting for the rider's receipt.", actor=jane)
    update_ticket_status(db_session, ticket=normal, new_status="in_progress", actor=jane)
    update_ticket_status(db_session, ticket=normal, new_status="closed", actor=jane)

    assert normal.assigned_admin_id == jane.id and normal.closed_at is not None
    assert db_session.query(TicketNote).one().author_admin_id == jane.id
    assert db_session.query(SupportNotification).filter_by(channel="whatsapp").count() == 2
    assert db_session.query(AuditLog).filter_by(event_type="ticket_assigned").one().actor == "czeyik"
    assert db_session.query(AuditLog).filter_by(event_type="ticket_note_added").one().actor == "jane"
    assert db_session.query(AuditLog).filter_by(event_type="ticket_status_changed").count() == 2
    with pytest.raises(ValueError, match="invalid ticket transition"):
        update_ticket_status(db_session, ticket=normal, new_status="open", actor=jane)
    reopened = reopen_closed_ticket_for_customer(
        db_session, channel=normal.channel, external_user_id=normal.external_user_id
    )
    db_session.commit()
    assert reopened is normal and normal.status == "open" and normal.closed_at is None
    assert db_session.query(AuditLog).filter_by(event_type="ticket_reopened_by_customer").one()

    urgent = ticket(db_session, "urgent")
    queue_new_ticket_notifications(db_session, urgent)
    db_session.commit()
    urgent_recipients = {
        item.recipient_admin_id
        for item in db_session.query(SupportNotification).filter_by(
            ticket_id=urgent.id, event_type="urgent_ticket_created"
        )
    }
    assert urgent_recipients == {cze.id, jane.id}


def test_notification_worker_sends_email_and_approved_whatsapp_template(
    db_session: Session,
) -> None:
    _, jane = provision_two_admins(db_session)
    value = ticket(db_session)
    email = SupportNotification(
        ticket_id=value.id,
        recipient_admin_id=jane.id,
        channel="email",
        recipient=jane.email,
        event_type="ticket_created",
        payload={"public_id": value.public_id, "urgency": value.urgency},
    )
    whatsapp = SupportNotification(
        ticket_id=value.id,
        channel="whatsapp",
        recipient=value.phone_number,
        event_type="ticket_status_changed",
        payload={"public_id": value.public_id, "status": "closed", "language": "en"},
    )
    db_session.add_all((email, whatsapp))
    db_session.commit()

    class FakeEmail:
        sent = []

        def send(self, recipient, event_type, payload):
            self.sent.append((recipient, event_type, payload))

    class FakeWhatsApp:
        sent = []

        def send_template(self, recipient, template_name, language, parameters):
            self.sent.append((recipient, template_name, language, parameters))
            return "wamid.test"

    settings = Settings(
        _env_file=None,
        notification_send_enabled=True,
        meta_send_enabled=True,
        smtp_host="smtp.example.com",
        smtp_username="user",
        smtp_password="password",
        smtp_from_address="support@example.com",
        notification_template_names={"ticket_status_changed.en": "ticket_status_en"},
    )
    fake_email, fake_whatsapp = FakeEmail(), FakeWhatsApp()
    assert process_next_notification(
        db_session, settings=settings, email_client=fake_email, whatsapp_client=fake_whatsapp
    )
    assert process_next_notification(
        db_session, settings=settings, email_client=fake_email, whatsapp_client=fake_whatsapp
    )
    assert fake_email.sent[0][:2] == (jane.email, "ticket_created")
    assert fake_whatsapp.sent == [
        (value.phone_number, "ticket_status_en", "en", [value.public_id, "closed"])
    ]
    assert email.status == whatsapp.status == "sent"

    failed = SupportNotification(
        ticket_id=value.id,
        channel="unsupported",
        recipient="nobody",
        event_type="ticket_created",
        payload={"public_id": value.public_id},
    )
    db_session.add(failed)
    db_session.commit()
    assert process_next_notification(
        db_session,
        settings=settings.model_copy(update={"notification_max_attempts": 1}),
        email_client=fake_email,
        whatsapp_client=fake_whatsapp,
    )
    assert failed.status == "dead_letter" and failed.last_error == "ValueError"
