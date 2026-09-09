import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app.models import AdminUser
from app.services.knowledge import ingest_knowledge
from app.services.website_knowledge import extract_page, sitemap_urls


def document_key(url: str) -> str:
    path = urlparse(url).path.strip("/") or "home"
    return "website-" + re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage DUDU website pages for CCO review")
    parser.add_argument("--cco-username", required=True)
    parser.add_argument("--url", action="append", help="Stage only this approved duducar.co URL")
    args = parser.parse_args()
    urls = args.url or sitemap_urls()
    with SessionLocal() as db:
        actor = db.query(AdminUser).filter_by(username=args.cco_username).first()
        if not actor:
            raise SystemExit("CCO administrator not found")
        for url in urls:
            page = extract_page(url)
            document = ingest_knowledge(
                db,
                actor=actor,
                document_key=document_key(url),
                title=str(page["title"]),
                source_type="website",
                source_uri=url,
                language=str(page["language"]),
                chunks=list(page["chunks"]),
                tags=["website"],
            )
            print(f"Staged {document.document_key} v{document.version}: {url}")


if __name__ == "__main__":
    main()
