import logging
import time

from app.config import get_settings
from app.database import SessionLocal
from app.services.media import process_next_media, reconcile_orphaned_media
from app.services.notifications import process_notifications
from app.services.retention import run_retention
from app.services.whatsapp import process_outbox
from app.services.inbound import process_inbox
from app.workers.backup import run_backup


def _schedule(operation, *, now: float, interval: int, failure_event: str) -> float:
    try:
        operation()
        return now + interval
    except Exception:
        logging.exception(failure_event)
        return now + 5 * 60


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    next_backup = time.monotonic() + settings.backup_interval_minutes * 60
    next_retention = time.monotonic() + 24 * 60 * 60
    while True:
        processed = process_inbox(SessionLocal)
        for operation in (process_outbox, process_next_media, process_notifications):
            with SessionLocal() as db:
                processed = operation(db) or processed
        now = time.monotonic()
        if now >= next_backup:
            def backup() -> None:
                logging.info("database_backup_completed key=%s", run_backup(settings))

            next_backup = _schedule(
                backup,
                now=now,
                interval=settings.backup_interval_minutes * 60,
                failure_event="database_backup_failed",
            )
        if now >= next_retention:
            def lifecycle() -> None:
                with SessionLocal() as db:
                    result = run_retention(db)
                logging.info("retention_run_completed result=%s", result)
                if result.failures:
                    raise RuntimeError("retention run failed")
                with SessionLocal() as db:
                    removed = reconcile_orphaned_media(db)
                logging.info("media_reconciliation_completed removed=%s", removed)

            next_retention = _schedule(
                lifecycle,
                now=now,
                interval=24 * 60 * 60,
                failure_event="retention_or_reconciliation_failed",
            )
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    main()
