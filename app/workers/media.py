import time

from app.database import SessionLocal
from app.services.media import process_next_media


def run() -> None:
    while True:
        with SessionLocal() as db:
            processed = process_next_media(db)
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    run()
