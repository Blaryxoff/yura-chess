#!/usr/bin/env bash
# Scheduled logical backup of the production database.
#
#   deploy/mariadb/backup.sh
#
# Dumps to a local directory, copies the dump off-host to S3-compatible storage,
# prunes by retention and alerts on any failure. Intended for a systemd timer or
# cron entry on Firebat; configuration comes from the environment file, never
# from this script.
set -Eeuo pipefail

ENV_FILE="${YURA_CHESS_BACKUP_ENV_FILE:-/srv/yura-chess/backup.env}"
if [[ -f "$ENV_FILE" ]]; then
  mode="$(stat -c %a "$ENV_FILE")"
  if (( 10#$mode % 100 != 0 )); then
    echo "refusing group/world-readable secret file $ENV_FILE (mode $mode)" >&2
    exit 2
  fi
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

PROJECT="${YURA_CHESS_COMPOSE_PROJECT:-yura-chess-production}"
COMPOSE_FILE="${YURA_CHESS_COMPOSE_FILE:-/srv/yura-chess/repo/deploy/compose.production.yml}"
DB_SERVICE="${YURA_CHESS_DB_SERVICE:-mariadb}"
DB_NAME="${YURA_CHESS_DB_NAME:?YURA_CHESS_DB_NAME is required}"
DB_USER="${YURA_CHESS_BACKUP_DB_USER:?YURA_CHESS_BACKUP_DB_USER is required}"
DB_PASSWORD="${YURA_CHESS_BACKUP_DB_PASSWORD:?YURA_CHESS_BACKUP_DB_PASSWORD is required}"
BACKUP_DIR="${YURA_CHESS_BACKUP_DIR:-/srv/yura-chess/backups}"
RETENTION_DAYS="${YURA_CHESS_BACKUP_RETENTION_DAYS:-14}"
MIN_FREE_MB="${YURA_CHESS_BACKUP_MIN_FREE_MB:-2048}"
S3_TARGET="${YURA_CHESS_BACKUP_S3_TARGET:-}"
ALERT_COMMAND="${YURA_CHESS_BACKUP_ALERT_COMMAND:-}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/${DB_NAME}-${TIMESTAMP}.sql.gz"

alert() {
  local message="$1"
  echo "backup failure: $message" >&2
  logger --tag yura-chess-backup "failure: $message" 2>/dev/null || true
  if [[ -n "$ALERT_COMMAND" ]]; then
    # The operator's own notifier; its own failure must not mask the original one.
    "$ALERT_COMMAND" "yura-chess backup failed: $message" || true
  fi
}

trap 'rm -f "$ARCHIVE.partial"; alert "unexpected error on line $LINENO"' ERR

install -d -m 0700 "$BACKUP_DIR"

FREE_MB="$(df --output=avail -m "$BACKUP_DIR" | tail -1 | tr -d ' ')"
if (( FREE_MB < MIN_FREE_MB )); then
  alert "only ${FREE_MB} MB free in $BACKUP_DIR, need ${MIN_FREE_MB} MB"
  exit 1
fi

echo "==> dumping $DB_NAME"
# Passed by name, not as `--env NAME=value`: the value is read from this one
# command's environment instead of appearing in the host process list. Scoped to
# the command rather than exported, so `aws` and the alert hook never see it.
MYSQL_PWD="$DB_PASSWORD" docker compose --project-name "$PROJECT" --file "$COMPOSE_FILE" exec -T \
  --env MYSQL_PWD "$DB_SERVICE" \
  mariadb-dump --user="$DB_USER" --single-transaction --quick \
    --routines --events --default-character-set=utf8mb4 "$DB_NAME" \
  | gzip -9 >"$ARCHIVE.partial"

# Rename only after a complete dump, so a truncated file is never mistaken for a backup.
mv "$ARCHIVE.partial" "$ARCHIVE"
chmod 0600 "$ARCHIVE"

if ! gzip --test "$ARCHIVE"; then
  alert "archive $ARCHIVE is corrupt"
  exit 1
fi

SIZE_BYTES="$(stat -c %s "$ARCHIVE")"
if (( SIZE_BYTES < 1024 )); then
  alert "archive $ARCHIVE is implausibly small (${SIZE_BYTES} bytes)"
  exit 1
fi
echo "==> wrote $ARCHIVE (${SIZE_BYTES} bytes)"

if [[ -n "$S3_TARGET" ]]; then
  echo "==> copying to $S3_TARGET"
  s3_args=()
  if [[ -n "${YURA_CHESS_BACKUP_S3_ENDPOINT:-}" ]]; then
    s3_args+=(--endpoint-url "$YURA_CHESS_BACKUP_S3_ENDPOINT")
  fi
  if ! aws "${s3_args[@]}" s3 cp "$ARCHIVE" "$S3_TARGET/$(basename "$ARCHIVE")"; then
    alert "off-host copy to $S3_TARGET failed"
    exit 1
  fi
else
  # A backup that exists only on Firebat does not survive Firebat.
  alert "YURA_CHESS_BACKUP_S3_TARGET is not set: this backup has no off-host copy"
  exit 1
fi

echo "==> pruning local archives older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -type f -name "${DB_NAME}-*.sql.gz" -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name "${DB_NAME}-*.sql.gz.partial" -delete

trap - ERR
echo "==> backup complete"
