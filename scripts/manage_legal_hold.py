"""Create or release a legal hold from an authenticated privacy-owner shell."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.config import get_settings
from app.models import LegalHold, Ticket
from app.services.retention import create_legal_hold, release_legal_hold
from scripts.manage_admin import authenticated_admin


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("subject_type", choices=("conversation", "ticket"))
    create.add_argument("subject_id", help="conversation UUID or public ticket ID")
    create.add_argument("--reason", required=True)
    create.add_argument("--reference", required=True)
    create.add_argument("--expires-at", required=True, help="UTC ISO-8601 date/time")
    release = subparsers.add_parser("release")
    release.add_argument("hold_id")
    args = parser.parse_args()

    with SessionLocal() as db:
        actor = authenticated_admin(db, get_settings().privacy_owner_username)
        if args.command == "create":
            subject_id = args.subject_id
            if args.subject_type == "ticket":
                ticket = db.query(Ticket).filter_by(public_id=subject_id).one_or_none()
                if not ticket:
                    raise SystemExit("Ticket not found")
                subject_id = ticket.id
            try:
                expires_at = datetime.fromisoformat(args.expires_at.replace("Z", "+00:00"))
                if expires_at.tzinfo is not None:
                    expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError as exc:
                raise SystemExit("Invalid --expires-at value") from exc
            hold = create_legal_hold(
                db,
                actor=actor,
                subject_type=args.subject_type,
                subject_id=subject_id,
                reason=args.reason,
                reference=args.reference,
                expires_at=expires_at,
            )
            print(f"Legal hold active: {hold.id}")
        else:
            hold = db.get(LegalHold, args.hold_id)
            if not hold:
                raise SystemExit("Legal hold not found")
            release_legal_hold(db, actor=actor, hold=hold)
            print(f"Legal hold released: {hold.id}")


if __name__ == "__main__":
    main()
