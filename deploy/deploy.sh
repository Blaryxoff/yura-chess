#!/usr/bin/env bash
# Idempotent deploy of one immutable image tag.
#
#   deploy/deploy.sh <staging|production> <image-tag>
#
# Order matters: migrations run to completion as a separate release step before
# any new application container starts, so the running code never meets a schema
# it was not built for. A failed health smoke rolls back to the previous tag.
set -Eeuo pipefail

ENVIRONMENT="${1:?usage: deploy.sh <staging|production> <image-tag>}"
IMAGE_TAG="${2:?usage: deploy.sh <staging|production> <image-tag>}"

case "$ENVIRONMENT" in
  staging|production) ;;
  *) echo "unknown environment: $ENVIRONMENT" >&2; exit 2 ;;
esac

if [[ "$IMAGE_TAG" == "latest" || "$IMAGE_TAG" == *:latest ]]; then
  echo "refusing a mutable tag: deploy an immutable one (git sha or version)" >&2
  exit 2
fi

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${YURA_CHESS_STATE_DIR:-/srv/yura-chess}"
COMPOSE_FILE="$DEPLOY_DIR/compose.$ENVIRONMENT.yml"
PROJECT="yura-chess-$ENVIRONMENT"
IMAGE_REPOSITORY="${YURA_CHESS_IMAGE_REPOSITORY:-ghcr.io/blaryx/yura-chess}"
HEALTH_URL="${YURA_CHESS_HEALTH_URL:-http://127.0.0.1:${YURA_CHESS_PORT:-8080}/health/ready}"
HEALTH_ATTEMPTS="${YURA_CHESS_HEALTH_ATTEMPTS:-30}"
CURRENT_FILE="$STATE_DIR/$ENVIRONMENT.current-image"
PREVIOUS_FILE="$STATE_DIR/$ENVIRONMENT.previous-image"

export YURA_CHESS_IMAGE="$IMAGE_REPOSITORY:$IMAGE_TAG"

compose() {
  docker compose --project-name "$PROJECT" --file "$COMPOSE_FILE" "$@"
}

smoke() {
  local attempt
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if curl --fail --silent --max-time 3 "$HEALTH_URL" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

echo "==> deploying $YURA_CHESS_IMAGE to $ENVIRONMENT"
compose config --quiet

RUNNING_IMAGE=""
if [[ -f "$CURRENT_FILE" ]]; then
  RUNNING_IMAGE="$(cat "$CURRENT_FILE")"
fi

if [[ "$RUNNING_IMAGE" == "$YURA_CHESS_IMAGE" ]]; then
  echo "==> $YURA_CHESS_IMAGE is already the deployed tag; re-running the same steps"
fi

echo "==> pulling image"
compose pull --quiet

echo "==> starting dependencies"
compose up --detach --wait mariadb 2>/dev/null || true

echo "==> applying migrations"
compose --profile release run --rm migrate

echo "==> starting application"
compose up --detach --wait app

echo "==> health smoke: $HEALTH_URL"
if ! smoke; then
  echo "health smoke failed" >&2
  if [[ -n "$RUNNING_IMAGE" && "$RUNNING_IMAGE" != "$YURA_CHESS_IMAGE" ]]; then
    # Only the application goes back: a migration that already ran stays applied,
    # which is why every migration must be backwards compatible by one release.
    echo "==> rolling back to $RUNNING_IMAGE" >&2
    YURA_CHESS_IMAGE="$RUNNING_IMAGE" compose up --detach --wait app
  fi
  exit 1
fi

install -d -m 0750 "$STATE_DIR"
if [[ -n "$RUNNING_IMAGE" && "$RUNNING_IMAGE" != "$YURA_CHESS_IMAGE" ]]; then
  printf '%s\n' "$RUNNING_IMAGE" >"$PREVIOUS_FILE"
fi
printf '%s\n' "$YURA_CHESS_IMAGE" >"$CURRENT_FILE"

echo "==> deployed $YURA_CHESS_IMAGE"
