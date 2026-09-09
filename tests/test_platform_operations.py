from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import AuditLog, Base
from app.routers.health import ready
from app.routers.health import expected_revision
from app.services.media import reconcile_orphaned_media
from app.services.object_storage import delete_all_versions
from app.workers.backup import restore_backup, run_backup
from app.workers.all import _schedule


class FakeObjectStore:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.uploads = []
        self.deleted = []

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.uploads.append((Path(filename).read_bytes(), bucket, key, ExtraArgs))

    def list_objects_v2(self, **kwargs):
        return {
            "Contents": [
                {
                    "Key": "backups/postgresql/expired.dump",
                    "LastModified": self.now - timedelta(days=36),
                },
                {
                    "Key": "backups/postgresql/retained.dump",
                    "LastModified": self.now - timedelta(days=35),
                },
            ],
            "IsTruncated": False,
        }

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)

    def list_object_versions(self, **kwargs):
        return {"IsTruncated": False}


def test_readiness_checks_database() -> None:
    class Result:
        def scalar_one(self):
            return expected_revision()

    class Database:
        def execute(self, statement):
            return Result()

    assert ready(Database()) == {"status": "ready"}


def test_readiness_fails_closed_without_database() -> None:
    class Database:
        def execute(self, statement):
            raise OSError("unavailable")

    with pytest.raises(HTTPException, match="503: database unavailable"):
        ready(Database())


def test_readiness_fails_closed_on_pending_migration() -> None:
    class Result:
        def scalar_one(self):
            return "old-revision"

    class Database:
        def execute(self, statement):
            return Result()

    with pytest.raises(HTTPException, match="503: database migration required"):
        ready(Database())


def test_backup_uses_custom_dump_and_prunes_only_older_than_35_days(tmp_path) -> None:
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    store = FakeObjectStore(now)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://backup:secret@db:5432/dudu_support",
        media_bucket="dudu-production-media",
    )
    commands = []

    def dump(command, check, env):
        commands.append((command, check, env))
        Path(command[command.index("--file") + 1]).write_bytes(b"backup")

    key = run_backup(settings, now=now, client=store, runner=dump)

    assert key == "backups/postgresql/2026/09/09/120000.dump"
    assert commands[0][0][0] == "pg_dump"
    assert "secret" not in " ".join(commands[0][0])
    assert commands[0][2]["PGPASSWORD"] == "secret"
    assert store.uploads[0][0] == b"backup"
    assert store.deleted == [
        {"Bucket": "dudu-production-media", "Key": "backups/postgresql/expired.dump"}
    ]


def test_versioned_deletion_removes_object_versions_and_delete_markers() -> None:
    class Client:
        deleted = []

        def list_object_versions(self, **kwargs):
            return {
                "Versions": [
                    {"Key": "approved/item.png", "VersionId": "v2"},
                    {"Key": "approved/item.png.extra", "VersionId": "other"},
                ],
                "DeleteMarkers": [{"Key": "approved/item.png", "VersionId": "marker"}],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs):
            self.deleted.append(kwargs)

    client = Client()
    delete_all_versions(client, "private", "approved/item.png")

    assert client.deleted == [
        {
            "Bucket": "private",
            "Delete": {
                "Objects": [
                    {"Key": "approved/item.png", "VersionId": "v2"},
                    {"Key": "approved/item.png", "VersionId": "marker"},
                ],
                "Quiet": True,
            },
        }
    ]


def test_reconciliation_deletes_only_old_unlinked_media() -> None:
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)

    class Storage:
        deleted = []

        def list(self, prefix):
            return iter(
                [
                    {"Key": "approved/old.png", "LastModified": now - timedelta(hours=25)},
                    {"Key": "approved/new.png", "LastModified": now - timedelta(hours=23)},
                ]
            )

        def delete(self, key):
            self.deleted.append(key)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        storage = Storage()
        assert reconcile_orphaned_media(db, storage, now=now) == 1
        assert storage.deleted == ["approved/old.png"]
        assert db.query(AuditLog).one().event_type == "orphaned_media_deleted"


def test_production_platform_keeps_budget_access_and_traffic_guards() -> None:
    root = Path(__file__).parents[1]
    environment = (root / "infra/aws/environment.yml").read_text()
    budget = (root / "infra/aws/budget.yml").read_text()
    deployment = (root / "infra/production/deploy.sh").read_text()
    release = (root / ".github/workflows/release.yml").read_text()

    assert "Default: t4g.small" in environment
    assert "CPUCredits: standard" in environment
    assert "FromPort: 22" not in environment
    assert "HttpTokens: required" in environment
    assert "Amount: 20" in budget
    assert "META_SEND_ENABLED=(false|0)" in deployment
    assert "releases/$app_release-$clamav_release" in deployment
    assert "install -m 0600 /opt/dudu/last-good-images /opt/dudu/rollback-images" in deployment
    assert "mv /opt/dudu/last-good-images.tmp /opt/dudu/last-good-images" in deployment
    assert '"cloud-init status --wait"' in release


def test_scheduled_failure_retries_without_stopping_other_worker_operations() -> None:
    def fail():
        raise OSError("dependency unavailable")

    assert _schedule(fail, now=100, interval=3600, failure_event="backup_failed") == 400


def test_restore_requires_empty_target_and_keeps_password_out_of_arguments(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://restore:secret@db:5432/restored",
        media_bucket="private",
    )

    class Client:
        def download_file(self, bucket, key, filename):
            Path(filename).write_bytes(b"custom-dump")

    commands = []

    def restore(command, check, env):
        commands.append((command, check, env))

    restore_backup(
        "backups/postgresql/2026/09/10/120000.dump",
        settings,
        client=Client(),
        runner=restore,
        table_names=lambda: [],
    )

    assert commands[0][0][0] == "pg_restore"
    assert "secret" not in " ".join(commands[0][0])
    assert commands[0][2]["PGPASSWORD"] == "secret"

    with pytest.raises(RuntimeError, match="must be empty"):
        restore_backup(
            "backups/postgresql/2026/09/10/120000.dump",
            settings,
            client=Client(),
            runner=restore,
            table_names=lambda: ["tickets"],
        )
