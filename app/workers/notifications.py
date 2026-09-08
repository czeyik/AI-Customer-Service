import time

from app.database import SessionLocal
from app.services.notifications import process_notifications


def main() -> None:
    while True:
        with SessionLocal() as db:
            processed = process_notifications(db)
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    main()
