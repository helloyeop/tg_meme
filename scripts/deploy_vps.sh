#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/memetrading}"
BRANCH="${BRANCH:-main}"
COMPOSE="${COMPOSE:-docker compose}"
BACKUP_BEFORE_DEPLOY="${BACKUP_BEFORE_DEPLOY:-true}"

cd "$APP_DIR"

echo "==> Checking local-only files"
test -f .env || { echo "Missing .env in $APP_DIR"; exit 1; }
test -f config/channels.yaml || { echo "Missing config/channels.yaml"; exit 1; }
test -f config/strategy.yaml || { echo "Missing config/strategy.yaml"; exit 1; }
mkdir -p data sessions

if [[ "$BACKUP_BEFORE_DEPLOY" == "true" && -f data/app.db ]]; then
  echo "==> Backing up SQLite"
  bash scripts/backup_sqlite.sh data/app.db data/backups
fi

echo "==> Pulling latest code"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Building containers"
$COMPOSE build

echo "==> Initializing database"
$COMPOSE run --rm init-db

echo "==> Restarting services"
$COMPOSE up -d collector pipeline dashboard

echo "==> Status"
$COMPOSE ps
