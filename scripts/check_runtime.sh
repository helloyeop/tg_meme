#!/usr/bin/env bash
set -euo pipefail

echo "==> Python"
python --version

echo "==> App import"
python -m app.main --mode init-db

echo "==> Node"
node --version
npm --version

echo "==> GMGN CLI"
if [[ -x ./node_modules/.bin/gmgn-cli ]]; then
  ./node_modules/.bin/gmgn-cli --version
else
  echo "gmgn-cli is not installed. Run: npm install --prefix ."
fi

echo "==> Docker Compose config"
if command -v docker >/dev/null 2>&1; then
  docker compose config >/dev/null
  echo "docker compose config OK"
else
  echo "docker is not installed; skipping"
fi
