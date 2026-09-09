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
"${compose[@]}" run --rm api alembic upgrade head
"${compose[@]}" run --rm api python -m app.workers.retention --once
"${compose[@]}" up --detach --remove-orphans

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

echo "Deployment failed readiness; use the rollback runbook" >&2
exit 1
