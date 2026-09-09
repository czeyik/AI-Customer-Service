import argparse
import logging
import time

from app.database import SessionLocal
from app.services.retention import run_retention


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.interval_hours <= 168:
        raise SystemExit("--interval-hours must be between 1 and 168")
    logging.basicConfig(level=logging.INFO)
    while True:
        with SessionLocal() as db:
            result = run_retention(db, dry_run=args.dry_run)
        logging.info("retention run: %s", result)
        if result.failures:
            raise SystemExit(1)
        if args.dry_run or args.once:
            return
        time.sleep(args.interval_hours * 3600)


if __name__ == "__main__":
    main()
