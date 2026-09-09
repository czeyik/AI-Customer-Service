import argparse
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.services.object_storage import delete_all_versions


def run_backup(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    client=None,
    runner=subprocess.run,
) -> str:
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    client = client or boto3.client("s3", region_name=settings.media_region)
    database = make_url(settings.database_url)
    if database.get_backend_name() != "postgresql":
        raise RuntimeError("backups require PostgreSQL")

    key = f"{settings.backup_prefix}/{now:%Y/%m/%d/%H%M%S}.dump"
    with tempfile.TemporaryDirectory() as directory:
        dump = Path(directory) / "database.dump"
        environment = os.environ.copy()
        if database.password:
            environment["PGPASSWORD"] = database.password
        command = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dump),
            "--host",
            database.host or "localhost",
            "--port",
            str(database.port or 5432),
            "--username",
            database.username or "postgres",
            database.database or "postgres",
        ]
        runner(command, check=True, env=environment)
        client.upload_file(
            str(dump),
            settings.media_bucket,
            key,
            ExtraArgs={"Metadata": {"created-at": now.isoformat(), "format": "pg-custom"}},
        )

    _prune_expired(client, settings, now)
    return key


def restore_backup(
    key: str,
    settings: Settings | None = None,
    *,
    client=None,
    runner=subprocess.run,
    table_names=None,
) -> None:
    settings = settings or get_settings()
    if not key.startswith(f"{settings.backup_prefix}/") or ".." in key:
        raise ValueError("restore key must be below the configured backup prefix")
    if table_names is None:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        try:
            table_names = lambda: inspect(engine).get_table_names()
            existing = table_names()
        finally:
            engine.dispose()
    else:
        existing = table_names()
    if existing:
        raise RuntimeError("restore target database must be empty")

    client = client or boto3.client("s3", region_name=settings.media_region)
    database = make_url(settings.database_url)
    if database.get_backend_name() != "postgresql":
        raise RuntimeError("restore requires PostgreSQL")
    with tempfile.TemporaryDirectory() as directory:
        dump = Path(directory) / "database.dump"
        client.download_file(settings.media_bucket, key, str(dump))
        environment = os.environ.copy()
        if database.password:
            environment["PGPASSWORD"] = database.password
        runner(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--host",
                database.host or "localhost",
                "--port",
                str(database.port or 5432),
                "--username",
                database.username or "postgres",
                "--dbname",
                database.database or "postgres",
                str(dump),
            ],
            check=True,
            env=environment,
        )


def _prune_expired(client, settings: Settings, now: datetime) -> None:
    cutoff = now - timedelta(days=settings.backup_retention_days)
    continuation = None
    while True:
        arguments = {"Bucket": settings.media_bucket, "Prefix": f"{settings.backup_prefix}/"}
        if continuation:
            arguments["ContinuationToken"] = continuation
        page = client.list_objects_v2(**arguments)
        for item in page.get("Contents", []):
            modified = item["LastModified"]
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            if modified < cutoff:
                delete_all_versions(client, settings.media_bucket, item["Key"])
        if not page.get("IsTruncated"):
            return
        continuation = page["NextContinuationToken"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--restore-key")
    parser.add_argument("--confirm-empty-target", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    if args.restore_key:
        if not args.confirm_empty_target:
            raise SystemExit("restore requires --confirm-empty-target")
        restore_backup(args.restore_key, settings)
        logging.info("database_restore_completed key=%s", args.restore_key)
        return
    while True:
        key = run_backup(settings)
        logging.info("database_backup_completed key=%s", key)
        if args.once:
            return
        time.sleep(settings.backup_interval_minutes * 60)


if __name__ == "__main__":
    main()
