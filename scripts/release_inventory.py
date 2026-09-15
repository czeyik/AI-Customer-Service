"""Compare durable row counts across a quiesced migration without exposing customer data."""

import argparse
import json

from sqlalchemy import func, inspect, select

from app.database import engine
from app.models import Base, Conversation
from app.services.ticket_drafts import DialogueData


def migration_inventory(connection, *, validate_dialogue: bool = False) -> dict[str, int]:
    tables = set(inspect(connection).get_table_names())
    counts = {}
    for table, metadata in Base.metadata.tables.items():
        counts[table] = (
            connection.execute(select(func.count()).select_from(metadata)).scalar_one()
            if table in tables else 0
        )
    if validate_dialogue:
        for row in connection.execute(select(
            Conversation.dialogue_data, Conversation.dialogue_revision
        )).mappings():
            try:
                DialogueData.model_validate(row["dialogue_data"])
                if row["dialogue_revision"] < 0:
                    raise ValueError("negative revision")
            except (TypeError, ValueError):
                raise SystemExit("Invalid migrated dialogue; writers must remain stopped") from None
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-dialogue", action="store_true")
    args = parser.parse_args()
    with engine.connect() as connection:
        print(json.dumps(migration_inventory(connection, validate_dialogue=args.validate_dialogue),
                         sort_keys=True))
