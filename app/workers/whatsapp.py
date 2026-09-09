import time

from app.database import SessionLocal
from app.services.whatsapp import process_outbox


def main() -> None:
    while True:
        with SessionLocal() as db:
            processed = process_outbox(db)
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    main()
