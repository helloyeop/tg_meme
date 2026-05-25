#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-data/app.db}"
BACKUP_DIR="${2:-data/backups}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

BACKUP_PATH="$(bash scripts/backup_sqlite.sh "$DB_PATH" "$BACKUP_DIR")"
echo "Backup created: $BACKUP_PATH"

sqlite3 "$DB_PATH" <<'SQL'
BEGIN;
UPDATE token_market_snapshots SET raw_json = NULL WHERE raw_json IS NOT NULL;
UPDATE token_security_snapshots SET raw_json = NULL WHERE raw_json IS NOT NULL;
UPDATE wallet_activity_snapshots SET raw_json = NULL WHERE raw_json IS NOT NULL;
COMMIT;
PRAGMA wal_checkpoint(TRUNCATE);
VACUUM;
SQL

echo "Removed stored raw snapshot payloads and reclaimed SQLite free space."
