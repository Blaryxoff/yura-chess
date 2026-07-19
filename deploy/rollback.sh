#!/usr/bin/env bash
# Put the previous application image back.
#
#   deploy/rollback.sh <staging|production> [image-tag]
#
# Without a tag the last known good one recorded by deploy.sh is used. Only the
# application is rolled back: the schema is not migrated down, so a release that
# needs a schema rollback is restored from a backup instead (deploy/README.md).
set -Eeuo pipefail

ENVIRONMENT="${1:?usage: rollback.sh <staging|production> [image-tag]}"
REQUESTED_TAG="${2:-}"

case "$ENVIRONMENT" in
  staging) DEFAULT_PORT=8081 ;;
  production) DEFAULT_PORT=8082 ;;
  *) echo "unknown environment: $ENVIRONMENT" >&2; exit 2 ;;
esac

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${YURA_CHESS_STATE_DIR:-/srv/yura-chess}"
COMPOSE_FILE="$DEPLOY_DIR/compose.$ENVIRONMENT.yml"
PROJECT="yura-chess-$ENVIRONMENT"
IMAGE_REPOSITORY="${YURA_CHESS_IMAGE_REPOSITORY:-ghcr.io/blaryxoff/yura-chess}"
HEALTH_URL="${YURA_CHESS_HEALTH_URL:-http://127.0.0.1:${YURA_CHESS_PORT:-$DEFAULT_PORT}/health/ready}"
CURRENT_FILE="$STATE_DIR/$ENVIRONMENT.current-image"
PREVIOUS_FILE="$STATE_DIR/$ENVIRONMENT.previous-image"
HEALTH_ATTEMPTS="${YURA_CHESS_HEALTH_ATTEMPTS:-30}"

install -d -m 0750 "$STATE_DIR"
exec 9>"$STATE_DIR/$ENVIRONMENT.deploy.lock"
if ! flock -n 9; then
  echo "another deploy or rollback is already running for $ENVIRONMENT" >&2
  exit 3
fi

if [[ -n "$REQUESTED_TAG" && ! "$REQUESTED_TAG" =~ ^([0-9a-f]{7,40}|v[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "refusing a mutable rollback tag: use a git sha or vMAJOR.MINOR.PATCH" >&2
  exit 2
fi

if [[ -n "$REQUESTED_TAG" ]]; then
  TARGET_IMAGE="$IMAGE_REPOSITORY:$REQUESTED_TAG"
elif [[ -f "$PREVIOUS_FILE" ]]; then
  TARGET_IMAGE="$(cat "$PREVIOUS_FILE")"
else
  echo "no previous image recorded in $PREVIOUS_FILE; pass the tag explicitly" >&2
  exit 2
fi

export YURA_CHESS_IMAGE="$TARGET_IMAGE"

compose() {
  docker compose --project-name "$PROJECT" --file "$COMPOSE_FILE" "$@"
}

echo "==> rolling $ENVIRONMENT back to $TARGET_IMAGE"
compose pull --quiet app
compose up --detach --wait app

for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  if curl --fail --silent --max-time 3 "$HEALTH_URL" >/dev/null; then
    install -d -m 0750 "$STATE_DIR"
    printf '%s\n' "$TARGET_IMAGE" >"$CURRENT_FILE"
    echo "==> rolled back to $TARGET_IMAGE"
    exit 0
  fi
  sleep 2
done

echo "rollback target is not healthy; inspect 'docker compose --project-name $PROJECT logs app'" >&2
exit 1
