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


@pytest.mark.parametrize("failure", [
    "", "pull", "config", "writer", "drain", "backup", "migration", "schema",
    "inventory", "invalid_state", "retention", "health",
])
def test_deploy_drains_and_backs_up_before_migration(tmp_path, failure):
    import json
    import os
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    script = source / "deploy.sh"
    script.write_text(Path("infra/production/deploy.sh").read_text().replace(
        "/run/dudu", str(tmp_path / "run")
    ).replace("/opt/dudu", str(tmp_path / "opt")))
    commands = tmp_path / "commands.jsonl"
    backup_file = tmp_path / "opt/releases/abc-def/pre-migration-backup.s3"
    backup_file.parent.mkdir(parents=True)
    backup_file.write_text("backups/postgresql/previous.dump\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = '''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "curl":
    print("staging" if args[-1].endswith("/Environment") else "support.example.invalid")
elif name == "aws":
    print("META_SEND_ENABLED=false" if "get-secret-value" in args else "123456789012")
elif name == "docker":
    failure = os.environ["DEPLOY_FAILURE"]
    if args[0] == "ps" and failure == "writer":
        print("old-writer")
    if (failure == "pull" and "pull" in args or
        failure == "config" and any("get_settings()" in arg for arg in args) or
        failure == "drain" and args[-2:] == ["python", "-"] or
        failure == "backup" and any("run_backup" in arg for arg in args) or
        failure == "migration" and "upgrade" in args or
        failure == "schema" and "check" in args or
        failure == "invalid_state" and "--validate-dialogue" in args or
        failure == "retention" and "app.workers.retention" in args or
        failure == "health" and "--remove-orphans" in args):
        sys.exit(9)
    if any("run_backup" in arg for arg in args):
        print("backups/postgresql/synthetic.dump")
    if "scripts/release_inventory.py" in args:
        print(json.dumps({"tickets": 0 if failure == "inventory" and "--validate-dialogue" in args else 1}))
'''
    for name in ("docker", "curl", "aws"):
        command = binary / name
        command.write_text(stub)
        command.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "app@sha256:abc", "clamav@sha256:def"],
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}",
             "COMMAND_LOG": str(commands), "DEPLOY_FAILURE": failure},
        capture_output=True, text=True,
    )
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    docker = [call for call in calls if call[0] == "docker"]
    index = lambda word: next(i for i, call in enumerate(docker) if word in call)
    backup = [i for i, call in enumerate(docker) if any("run_backup" in arg for arg in call)]
    start = [i for i, call in enumerate(docker) if "--remove-orphans" in call]
    assert result.returncode == {"": 0, "writer": 4, "inventory": 5}.get(failure, 9)
    if failure in {"pull", "config"}:
        assert not any("stop" in call for call in docker)
    else:
        assert index("pull") < index("stop")
    if failure in {"pull", "config", "writer", "drain", "backup"}:
        assert not any("upgrade" in call for call in docker)
        assert backup_file.read_text() == "backups/postgresql/previous.dump\n"
    else:
        assert backup_file.read_text() == "backups/postgresql/synthetic.dump\n"
    if failure and failure != "health":
        assert not start
    if not failure:
        assert index("stop") < backup[0] < index("upgrade") < index("check") < start[0]
        assert "--wait" in docker[start[0]]
    assert (tmp_path / "opt/last-good-images").exists() == (not failure)


def test_release_inventory_preserves_counts_and_rejects_invalid_private_state():
    from app.models import Conversation
    from scripts.release_inventory import migration_inventory

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        empty = migration_inventory(connection)
        Base.metadata.create_all(connection)
        assert migration_inventory(connection, validate_dialogue=True) == empty
        connection.execute(Conversation.__table__.insert().values(
            channel="web", external_user_id="inventory-check",
            dialogue_data={"schema_version": 1},
        ))
        before = migration_inventory(connection)
        assert before["conversations"] == 1
        assert migration_inventory(connection, validate_dialogue=True) == before
        connection.execute(Conversation.__table__.update().values(
            dialogue_data={"schema_version": 999, "private": "customer-secret"},
        ))
        with pytest.raises(SystemExit, match="Invalid migrated dialogue") as error:
            migration_inventory(connection, validate_dialogue=True)
        assert "customer-secret" not in str(error.value)


@pytest.mark.parametrize("failure", [True, False])
def test_worker_readiness_requires_a_complete_cycle(tmp_path, monkeypatch, failure):
    from contextlib import nullcontext
    from app.workers import all as worker

    marker = tmp_path / "worker-ready"
    marker.touch()  # A restart must discard any previous readiness marker.
    monkeypatch.setattr(worker, "READY_FILE", marker)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(worker, "SessionLocal", nullcontext)

    def inbox(_):
        assert not marker.exists()
        if failure:
            raise RuntimeError("worker failed")
        return False

    def end_cycle(_):
        raise RuntimeError("cycle complete")

    monkeypatch.setattr(worker, "process_inbox", inbox)
    for name in ("process_outbox", "process_next_media", "process_notifications"):
        monkeypatch.setattr(worker, name, lambda _: False)
    monkeypatch.setattr(worker.time, "sleep", end_cycle)
    with pytest.raises(RuntimeError, match="worker failed" if failure else "cycle complete"):
        worker.main()
    assert marker.exists() == (not failure)


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
    assert 'IpProtocol: "-1"' not in environment
    assert "SSEAlgorithm: aws:kms" in environment
    assert "KmsMasterKeyId: alias/aws/sns" in environment
    assert "Amount: 30" in budget
    assert "BudgetName: dudu-support-aws-monthly-beta" in budget
    assert all(f"Threshold: {percentage}" in budget for percentage in (66.67, 83.33, 93.33))
    assert "ThresholdType: PERCENTAGE" in budget
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
