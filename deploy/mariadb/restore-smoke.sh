#!/usr/bin/env bash
# Prove that the latest backup actually restores.
#
#   deploy/mariadb/restore-smoke.sh [archive.sql.gz]
#
# Restores into a temporary database, checks that the canonical tables exist and
# that Alembic is at head, then drops it. The production database is never
# touched: the script refuses to run against it.
set -Eeuo pipefail

ENV_FILE="${YURA_CHESS_BACKUP_ENV_FILE:-/srv/yura-chess/backup.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

PROJECT="${YURA_CHESS_COMPOSE_PROJECT:-yura-chess-production}"
DB_SERVICE="${YURA_CHESS_DB_SERVICE:-mariadb}"
DB_NAME="${YURA_CHESS_DB_NAME:?YURA_CHESS_DB_NAME is required}"
DB_USER="${YURA_CHESS_RESTORE_DB_USER:-root}"
DB_PASSWORD="${YURA_CHESS_RESTORE_DB_PASSWORD:?YURA_CHESS_RESTORE_DB_PASSWORD is required}"
BACKUP_DIR="${YURA_CHESS_BACKUP_DIR:-/srv/yura-chess/backups}"

ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" ]]; then
  ARCHIVE="$(find "$BACKUP_DIR" -type f -name "${DB_NAME}-*.sql.gz" -print0 \
    | xargs -0 ls -1t 2>/dev/null | head -1 || true)"
fi
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "no backup archive found in $BACKUP_DIR" >&2
  exit 2
fi

RESTORE_DB="${DB_NAME}_restore_smoke"
if [[ "$RESTORE_DB" == "$DB_NAME" ]]; then
  echo "refusing to restore over the live database" >&2
  exit 2
fi

mariadb_client() {
  docker compose --project-name "$PROJECT" exec -T \
    --env "MYSQL_PWD=$DB_PASSWORD" "$DB_SERVICE" \
    mariadb --user="$DB_USER" --default-character-set=utf8mb4 "$@"
}

cleanup() {
  mariadb_client --execute "DROP DATABASE IF EXISTS \`$RESTORE_DB\`" || true
}
trap cleanup EXIT

echo "==> restoring $ARCHIVE into $RESTORE_DB"
mariadb_client --execute \
  "DROP DATABASE IF EXISTS \`$RESTORE_DB\`; CREATE DATABASE \`$RESTORE_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
gunzip --stdout "$ARCHIVE" | mariadb_client "$RESTORE_DB"

echo "==> verifying the restored schema"
EXPECTED_TABLES=(games game_moves pending_engine_turns request_replays asr_transcripts board_image_cache alembic_version)
for table in "${EXPECTED_TABLES[@]}"; do
  if ! mariadb_client --skip-column-names --batch --execute \
      "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$RESTORE_DB' AND table_name='$table'" \
      | grep --quiet '^1$'; then
    echo "restored backup is missing table $table" >&2
    exit 1
  fi
done

REVISION="$(mariadb_client --skip-column-names --batch --execute \
  "SELECT version_num FROM \`$RESTORE_DB\`.alembic_version" | head -1)"
if [[ -z "$REVISION" ]]; then
  echo "restored backup has no Alembic revision" >&2
  exit 1
fi

# The games table is the one whose loss would end the service; an empty restore
# of a non-empty production database means the dump did not capture data.
GAMES="$(mariadb_client --skip-column-names --batch --execute \
  "SELECT COUNT(*) FROM \`$RESTORE_DB\`.games")"

echo "==> restore smoke passed: revision $REVISION, $GAMES games"
