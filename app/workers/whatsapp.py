import time

from app.database import SessionLocal
from app.services.whatsapp import process_outbox
from app.services.inbound import process_inbox


def main() -> None:
    while True:
        processed = process_inbox(SessionLocal)
        with SessionLocal() as db:
            processed = process_outbox(db) or processed
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    main()
