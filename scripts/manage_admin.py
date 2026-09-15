"""Provision, disable, or recover named administrators from a trusted operator shell."""

import argparse
import getpass
import json
import sys
from pathlib import Path

import pyotp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.models import AdminUser
from app.security import verify_password, verify_totp
from app.services.admin_accounts import provision_admin, recover_admin, set_admin_active


def authenticated_admin(db, username: str) -> AdminUser:
    admin = db.query(AdminUser).filter_by(username=username.strip().lower(), is_active=True).first()
    password = getpass.getpass("Acting administrator password: ")
    code = getpass.getpass("Acting administrator 2FA code: ")
    if not admin or not verify_password(password, admin.password_hash) or not verify_totp(
        admin.totp_secret_ref, code
    ):
        raise SystemExit("Authentication failed")
    return admin


def new_password() -> str:
    password = getpass.getpass("New administrator password: ")
    if password != getpass.getpass("Repeat new administrator password: "):
        raise SystemExit("Passwords do not match")
    return password


def secret_material(username: str) -> tuple[str, str]:
    return f"admin/{username}/totp/{pyotp.random_base32()[:8].lower()}", pyotp.random_base32()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision")
    provision.add_argument("username")
    provision.add_argument("--display-name", required=True)
    provision.add_argument("--email", required=True)
    provision.add_argument("--phone-number")
    provision.add_argument("--actor")
    provision.add_argument("--cco", action="store_true")
    provision.add_argument("--recovery-approver", action="store_true")
    provision.add_argument("--notify-new", action="store_true")
    provision.add_argument("--notify-urgent", action="store_true")
    disable = subparsers.add_parser("disable")
    disable.add_argument("username")
    disable.add_argument("--actor", required=True)
    recover = subparsers.add_parser("recover")
    recover.add_argument("username")
    recover.add_argument("--actor", required=True)
    args = parser.parse_args()

    with SessionLocal() as db:
        actor = authenticated_admin(db, args.actor) if getattr(args, "actor", None) else None
        target = db.query(AdminUser).filter_by(username=args.username.strip().lower()).first()
        if args.command == "provision":
            if target:
                raise SystemExit("Administrator already exists")
            secret_ref, secret = secret_material(args.username)
            admin = provision_admin(
                db,
                actor=actor,
                username=args.username,
                display_name=args.display_name,
                email=args.email,
                phone_number=args.phone_number,
                password=new_password(),
                totp_secret_ref=secret_ref,
                is_cco=args.cco,
                is_recovery_approver=args.recovery_approver,
                notify_new_tickets=args.notify_new,
                notify_urgent_tickets=args.notify_urgent,
            )
        elif args.command == "disable":
            if not target:
                raise SystemExit("Administrator not found")
            set_admin_active(db, actor=actor, target=target, active=False)
            print(f"Disabled {target.username}; existing sessions are invalid.")
            return
        else:
            if not target:
                raise SystemExit("Administrator not found")
            secret_ref, secret = secret_material(args.username)
            recover_admin(
                db, actor=actor, target=target, password=new_password(), totp_secret_ref=secret_ref
            )
            admin = target

        result_username, result_email = admin.username, admin.email

    print(f"Account ready: {result_username}")
    print("Store this mapping in the approved secret manager; it is shown only now:")
    print(json.dumps({secret_ref: secret}))
    print("Authenticator URI:")
    print(pyotp.TOTP(secret).provisioning_uri(name=result_email, issuer_name="DUDU Car Support"))


if __name__ == "__main__":
    main()
