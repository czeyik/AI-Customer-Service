import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.retrieval import embed_text


def main() -> None:
    init_db()
    seed_path = Path("data/knowledge_seed.jsonl")
    with SessionLocal() as db:
        for line in seed_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            existing = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.title == item["title"]
            ).first()
            if existing:
                continue
            document = KnowledgeDocument(
                title=item["title"],
                source_type="seed",
                source_uri=str(seed_path),
                language=item.get("language", "en"),
                is_approved=True,
            )
            db.add(document)
            db.flush()
            for chunk in item["chunks"]:
                db.add(
                    KnowledgeChunk(
                        document_id=document.id,
                        content=chunk,
                        language=item.get("language", "en"),
                        tags=item.get("tags", []),
                        embedding=embed_text(chunk),
                    )
                )
        db.commit()


if __name__ == "__main__":
    main()

