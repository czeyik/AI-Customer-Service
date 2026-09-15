import re

from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog
from app.security import hash_password
from app.services.tickets import normalize_phone_number


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,119}$")


def provision_admin(
    db: Session,
    *,
    actor: AdminUser | None,
    username: str,
    display_name: str,
    email: str,
    phone_number: str | None = None,
    password: str,
    totp_secret_ref: str,
    is_cco: bool = False,
    is_recovery_approver: bool = False,
    notify_new_tickets: bool = False,
    notify_urgent_tickets: bool = False,
) -> AdminUser:
    username = username.strip().lower()
    display_name = display_name.strip()
    email = email.strip().lower()
    supplied_phone = phone_number
    phone_number = normalize_phone_number(phone_number) if phone_number else None
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("username must be 3-120 lowercase letters, numbers, '.', '_' or '-'")
    if not display_name or "@" not in email or not totp_secret_ref.strip():
        raise ValueError("display name, email, and TOTP secret reference are required")
    if supplied_phone and not phone_number:
        raise ValueError("phone number is invalid")
    if len(password.encode()) < 12 or len(password.encode()) > 72:
        raise ValueError("password must be 12-72 bytes")
    if db.query(AdminUser).filter(
        (AdminUser.username == username)
        | (AdminUser.email == email)
        | (AdminUser.totp_secret_ref == totp_secret_ref.strip())
    ).first():
        raise ValueError("username, email, and TOTP secret reference must be unique")
    if actor is None and db.query(AdminUser).filter_by(is_active=True).count():
        raise ValueError("an authenticated active administrator must provision this account")
    if actor is not None and not actor.is_active:
        raise ValueError("the provisioning administrator must be active")

    admin = AdminUser(
        username=username,
        display_name=display_name,
        email=email,
        phone_number=phone_number,
        password_hash=hash_password(password),
        totp_secret_ref=totp_secret_ref.strip(),
        is_cco=is_cco,
        is_recovery_approver=is_recovery_approver,
        notify_new_tickets=notify_new_tickets,
        notify_urgent_tickets=notify_urgent_tickets,
    )
    db.add(admin)
    db.flush()
    db.add(
        AuditLog(
            actor=actor.username if actor else "bootstrap",
            event_type="admin_provisioned",
            details={"admin_id": admin.id, "username": admin.username},
        )
    )
    db.commit()
    return admin


def set_admin_active(
    db: Session, *, actor: AdminUser, target: AdminUser, active: bool
) -> None:
    if not actor.is_active:
        raise ValueError("the acting administrator must be active")
    if actor.id == target.id and not active:
        raise ValueError("administrators cannot disable their own account")
    if not active and db.query(AdminUser).filter_by(is_active=True).count() <= 1:
        raise ValueError("the last active administrator cannot be disabled")
    if target.is_active == active:
        return
    target.is_active = active
    target.auth_version += 1
    db.add(
        AuditLog(
            actor=actor.username,
            event_type="admin_enabled" if active else "admin_disabled",
            details={"admin_id": target.id, "username": target.username},
        )
    )
    db.commit()


def recover_admin(
    db: Session,
    *,
    actor: AdminUser,
    target: AdminUser,
    password: str,
    totp_secret_ref: str,
) -> None:
    if not actor.is_active or not actor.is_recovery_approver:
        raise ValueError("an active recovery approver is required")
    if actor.id == target.id:
        raise ValueError("a recovery approver cannot recover their own account")
    if len(password.encode()) < 12 or len(password.encode()) > 72:
        raise ValueError("password must be 12-72 bytes")
    if not totp_secret_ref.strip():
        raise ValueError("TOTP secret reference is required")

    target.password_hash = hash_password(password)
    target.totp_secret_ref = totp_secret_ref.strip()
    target.is_active = True
    target.auth_version += 1
    db.add(
        AuditLog(
            actor=actor.username,
            event_type="admin_recovered",
            details={"admin_id": target.id, "username": target.username},
        )
    )
    db.commit()
