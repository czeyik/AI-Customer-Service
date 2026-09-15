#!/bin/bash
set -euo pipefail

app_image=${1:?app image digest required}
clamav_image=${2:?ClamAV image digest required}
case "$app_image $clamav_image" in
  *@sha256:*@sha256:*) ;;
  *) echo "Both images must use immutable sha256 digests" >&2; exit 2 ;;
esac

token=$(curl -fsS -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  http://169.254.169.254/latest/api/token)
tag_url=http://169.254.169.254/latest/meta-data/tags/instance
environment=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" "$tag_url/Environment")
domain=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" "$tag_url/Domain")
account=$(aws sts get-caller-identity --query Account --output text)
region=ap-southeast-5
bucket=dudu-support-$environment-$account
secret=dudu-support/$environment/runtime-env

install -d -m 0750 /run/dudu /opt/dudu/releases
aws secretsmanager get-secret-value --region "$region" --secret-id "$secret" \
  --query SecretString --output text >/run/dudu/runtime.env
chmod 0600 /run/dudu/runtime.env
grep -Eiq '^META_SEND_ENABLED=(false|0)$' /run/dudu/runtime.env || {
  echo "Dark deployment requires META_SEND_ENABLED=false" >&2
  exit 3
}

app_release=${app_image##*@sha256:}
clamav_release=${clamav_image##*@sha256:}
release_dir=/opt/dudu/releases/$app_release-$clamav_release
install -d -m 0750 "$release_dir"
cp -a "$(dirname "$0")/." "$release_dir/"
cat >/run/dudu/platform.env <<EOF
APP_IMAGE=$app_image
CLAMAV_IMAGE=$clamav_image
DEPLOY_ENV=$environment
APP_DOMAIN=$domain
MEDIA_BUCKET=$bucket
AWS_DEFAULT_REGION=$region
EOF
chmod 0600 /run/dudu/platform.env

compose=(docker compose --project-name "dudu-$environment" --env-file /run/dudu/runtime.env \
  --env-file /run/dudu/platform.env --file "$release_dir/compose.yml")
"${compose[@]}" config --quiet
# Download and validate before the maintenance window. Keep Caddy serving retryable
# upstream failures while every old application writer drains or stops.
"${compose[@]}" pull
"${compose[@]}" run --rm --no-deps api python -c \
  'from app.config import get_settings
try:
    get_settings()
except Exception:
    raise SystemExit("Runtime configuration invalid; deployment refused") from None'
"${compose[@]}" stop --timeout 90 api worker
for service in api worker; do
  if [[ -n $(docker ps -q --filter "label=com.docker.compose.project=dudu-$environment" \
    --filter "label=com.docker.compose.service=$service") ]]; then
    echo "Application writer still running; migration refused" >&2
    exit 4
  fi
done
"${compose[@]}" up --detach --wait --wait-timeout 90 db
"${compose[@]}" run --rm --no-deps -T api python - <<'PY'
import time
from sqlalchemy import text
from app.database import engine

for attempt in range(46):
    with engine.connect() as db:
        writers = db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE "
            "datname=current_database() AND pid<>pg_backend_pid() "
            "AND backend_type='client backend' AND state<>'idle'")).scalar_one()
        has_inbox = db.execute(text("SELECT to_regclass('whatsapp_inbound_messages')")).scalar()
        claims = db.execute(text("SELECT count(*) FROM whatsapp_inbound_messages "
            "WHERE status='processing' AND lease_until>CURRENT_TIMESTAMP AT TIME ZONE 'UTC'"
        )).scalar_one() if has_inbox else 0
    if not writers and not claims:
        break
    if attempt == 45:
        raise SystemExit("Active database writer or inbound lease; migration refused")
    time.sleep(2)
PY
"${compose[@]}" run --rm --no-deps -T api python -c \
  'from app.workers.backup import run_backup; print(run_backup())' \
  >"$release_dir/pre-migration-backup.s3.tmp"
test -s "$release_dir/pre-migration-backup.s3.tmp"
mv "$release_dir/pre-migration-backup.s3.tmp" "$release_dir/pre-migration-backup.s3"
"${compose[@]}" run --rm --no-deps api python scripts/release_inventory.py \
  >"$release_dir/pre-migration-state.json"
# The dialogue migration preflights every legacy row before conversion. On any
# failure leave writers stopped and retain the backup; never restart an old image.
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" run --rm --no-deps api alembic check
"${compose[@]}" run --rm --no-deps api python scripts/release_inventory.py --validate-dialogue \
  >"$release_dir/migrated-state.json"
cmp "$release_dir/pre-migration-state.json" "$release_dir/migrated-state.json" || {
  echo "Migration changed durable record counts; writers remain stopped" >&2
  exit 5
}
"${compose[@]}" run --rm --no-deps api python -m app.workers.retention --once
"${compose[@]}" up --detach --wait --wait-timeout 300 --remove-orphans

for attempt in $(seq 1 24); do
  if curl -fsS --max-time 5 "https://$domain/ready" >/dev/null; then
    ln -sfn "$release_dir" /opt/dudu/current
    if [[ -s /opt/dudu/last-good-images ]]; then
      install -m 0600 /opt/dudu/last-good-images /opt/dudu/rollback-images
    fi
    umask 077
    printf '%s\n%s\n' "$app_image" "$clamav_image" >/opt/dudu/last-good-images.tmp
    mv /opt/dudu/last-good-images.tmp /opt/dudu/last-good-images
    exit 0
  fi
  sleep 5
done

echo "Deployment failed readiness; use the compatible-runtime recovery runbook" >&2
exit 1
