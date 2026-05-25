#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-data/app.db}"
BACKUP_DIR="${2:-data/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/app_$STAMP.db'"
echo "$BACKUP_DIR/app_$STAMP.db"
