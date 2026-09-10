from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.models import RateLimitBucket


class _LimitExceeded(Exception):
    pass


class DatabaseRateLimiter:
    def allow_all(
        self,
        db: Session,
        limits: list[tuple[str, int, int]],
        *,
        max_keys: int = 10_000,
        now: datetime | None = None,
    ) -> list[int] | None:
        """Consume several limits together or consume none of them."""
        if not limits or max_keys < 1 or any(
            not key or limit < 1 or window_seconds < 1
            for key, limit, window_seconds in limits
        ):
            raise ValueError("Invalid rate-limit parameters")
        now = now or datetime.utcnow()
        try:
            with db.begin_nested():
                counts = []
                for key, limit, window_seconds in limits:
                    key_hash = sha256(key.encode()).hexdigest()
                    if not self._allow(db, key_hash, limit, window_seconds, max_keys, now):
                        raise _LimitExceeded
                    counts.append(db.get(RateLimitBucket, key_hash).request_count)
            return counts
        except _LimitExceeded:
            return None

    def allow(
        self,
        db: Session,
        key: str,
        limit: int,
        *,
        window_seconds: int = 60,
        max_keys: int = 10_000,
        now: datetime | None = None,
    ) -> bool:
        if not key or limit < 1 or window_seconds < 1 or max_keys < 1:
            raise ValueError("Invalid rate-limit parameters")
        now = now or datetime.utcnow()
        key_hash = sha256(key.encode()).hexdigest()
        dialect = db.get_bind().dialect.name
        if dialect != "postgresql":
            return self._allow(db, key_hash, limit, window_seconds, max_keys, now)

        shared_db = Session(bind=db.get_bind())
        try:
            allowed = self._allow(
                shared_db, key_hash, limit, window_seconds, max_keys, now
            )
            shared_db.commit()
            return allowed
        except Exception:
            shared_db.rollback()
            raise
        finally:
            shared_db.close()

    def _allow(
        self,
        db: Session,
        key_hash: str,
        limit: int,
        window_seconds: int,
        max_keys: int,
        now: datetime,
    ) -> bool:
        dialect = db.get_bind().dialect.name

        if dialect == "postgresql":
            # Serialize only this identity while its counter is inspected.
            lock_key = int.from_bytes(bytes.fromhex(key_hash[:16]), signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        bucket = db.get(RateLimitBucket, key_hash)
        if bucket:
            if bucket.expires_at <= now:
                bucket.request_count = 1
                bucket.window_started_at = now
                bucket.expires_at = now + timedelta(seconds=window_seconds)
                db.flush()
                return True
            if bucket.request_count >= limit:
                return False
            bucket.request_count += 1
            return True

        if dialect == "postgresql":
            # ponytail: new identities briefly share one lock; split the cap into shards if
            # first-contact throughput grows beyond the invitation-only pilot.
            db.execute(text("SELECT pg_advisory_xact_lock(873204911)"))
            bucket = db.get(RateLimitBucket, key_hash)
            if bucket:
                return self._allow(db, key_hash, limit, window_seconds, max_keys, now)

        db.execute(delete(RateLimitBucket).where(RateLimitBucket.expires_at <= now))
        active_keys = db.scalar(select(func.count()).select_from(RateLimitBucket)) or 0
        if active_keys >= max_keys:
            return False
        db.add(
            RateLimitBucket(
                key_hash=key_hash,
                request_count=1,
                window_started_at=now,
                expires_at=now + timedelta(seconds=window_seconds),
            )
        )
        db.flush()
        return True


rate_limiter = DatabaseRateLimiter()
