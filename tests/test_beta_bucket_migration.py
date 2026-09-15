import importlib
from datetime import datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import RateLimitBucket
from app.services.rate_limit import DatabaseRateLimiter


migration = importlib.import_module(
    "migrations.versions.f7b2c3d4e5a6_rename_beta_total_bucket"
)
CURRENT_KEY = "beta-total:2026-09-10:2026-09-30"


def test_beta_total_bucket_migration_preserves_limits_and_is_reversible() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        transaction = connection.begin()
        RateLimitBucket.__table__.create(connection)
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            now = datetime(2026, 9, 15, 12, 0)
            expires_at = now + timedelta(days=15)
            db.add(
                RateLimitBucket(
                    key_hash=migration.LEGACY_KEY_HASH,
                    request_count=1,
                    window_started_at=now - timedelta(hours=1),
                    expires_at=expires_at,
                )
            )
            db.flush()

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            db.expire_all()
            bucket = db.get(RateLimitBucket, migration.CURRENT_KEY_HASH)
            assert bucket is not None
            assert (bucket.request_count, bucket.window_started_at, bucket.expires_at) == (
                1,
                now - timedelta(hours=1),
                expires_at,
            )
            assert db.get(RateLimitBucket, migration.LEGACY_KEY_HASH) is None

            limiter = DatabaseRateLimiter()
            assert limiter.allow_all(db, [(CURRENT_KEY, 2, 60)], now=now) == [2]
            assert limiter.allow_all(db, [(CURRENT_KEY, 2, 60)], now=now) is None
            assert db.get(RateLimitBucket, migration.CURRENT_KEY_HASH).request_count == 2
            db.commit()

            db.query(RateLimitBucket).delete()
            db.commit()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
            assert db.query(RateLimitBucket).count() == 0

            row = RateLimitBucket(
                key_hash=migration.LEGACY_KEY_HASH,
                request_count=7,
                window_started_at=now,
                expires_at=now + timedelta(hours=2),
            )
            db.add(row)
            db.flush()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
            db.expire_all()
            restored = db.get(RateLimitBucket, migration.LEGACY_KEY_HASH)
            assert restored is not None
            assert (restored.request_count, restored.window_started_at, restored.expires_at) == (
                7,
                now,
                now + timedelta(hours=2),
            )
            db.query(RateLimitBucket).delete()
            db.commit()

            db.add_all(
                [
                    RateLimitBucket(
                        key_hash=migration.LEGACY_KEY_HASH,
                        request_count=3,
                        window_started_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                    RateLimitBucket(
                        key_hash=migration.CURRENT_KEY_HASH,
                        request_count=9,
                        window_started_at=now - timedelta(hours=1),
                        expires_at=now + timedelta(hours=3),
                    ),
                ]
            )
            db.flush()
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            savepoint.rollback()
            db.expire_all()
            rows = {
                bucket.key_hash: (
                    bucket.request_count,
                    bucket.window_started_at,
                    bucket.expires_at,
                )
                for bucket in db.query(RateLimitBucket).all()
            }
            assert rows == {
                migration.LEGACY_KEY_HASH: (3, now, now + timedelta(hours=1)),
                migration.CURRENT_KEY_HASH: (
                    9,
                    now - timedelta(hours=1),
                    now + timedelta(hours=3),
                ),
            }
        transaction.rollback()
    engine.dispose()
