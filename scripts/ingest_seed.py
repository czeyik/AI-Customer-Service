import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AdminUser
from app.services.knowledge import ingest_knowledge


CORPUS_PATH = ROOT / "docs" / "wave-7-knowledge-corpus.md"
LANGUAGE_HEADINGS = {"English": "en", "Bahasa Malaysia": "ms", "Simplified Chinese": "zh"}
TAGS = {
    "accounts-login": ["account", "login", "password", "profile"],
    "bookings-cancellations": ["booking", "cancel", "ride", "driver", "rider"],
    "fares-payments": ["fare", "payment", "refund", "wallet", "promo"],
    "promotions-vouchers": ["promo", "voucher", "discount"],
    "driver-onboarding-basics": ["driver", "onboarding", "documents", "vehicle"],
    "safety-incidents": ["safety", "accident", "harassment", "emergency"],
    "support-ticket-response-targets": ["ticket", "support", "response", "priority", "human"],
    "business-collaboration-inquiries": ["business", "partner", "organization", "collaboration"],
}


def corpus_records(corpus_path: Path = CORPUS_PATH) -> list[dict]:
    text = corpus_path.read_text(encoding="utf-8")
    records = []
    topics = re.split(r"(?m)^## \d+\. ", text)[1:]
    for topic in topics:
        title, body = topic.split("\n", 1)
        key_match = re.search(r"(?m)^Proposed key: `([^`]+)`", body)
        if not key_match:
            raise ValueError(f"missing proposed key for {title}")
        key = key_match.group(1)
        for heading, language in LANGUAGE_HEADINGS.items():
            content_match = re.search(
                rf"(?ms)^### {re.escape(heading)}\n\n(.+?)(?=\n### |\n## |\Z)", body
            )
            if not content_match:
                raise ValueError(f"missing {heading} content for {key}")
            content = " ".join(content_match.group(1).split())
            records.append(
                {
                    "document_key": key,
                    "title": title.strip(),
                    "source_type": "cco_approved_corpus",
                    "source_uri": f"docs/wave-7-knowledge-corpus.md#{key}",
                    "language": language,
                    "chunks": [content],
                    "tags": TAGS[key],
                    "effective_at": datetime(2026, 9, 9),
                }
            )
    return records


def ingest_seed(db: Session, actor: AdminUser, corpus_path: Path = CORPUS_PATH) -> int:
    count = 0
    for item in corpus_records(corpus_path):
        ingest_knowledge(db, actor=actor, activate=True, **item)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the CCO-approved knowledge corpus")
    parser.add_argument("--cco-username", required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        actor = db.query(AdminUser).filter_by(username=args.cco_username).first()
        if not actor:
            raise SystemExit("CCO administrator not found")
        print(f"Published {ingest_seed(db, actor)} approved knowledge records.")


if __name__ == "__main__":
    main()
