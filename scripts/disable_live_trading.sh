#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/memetrading}"
cd "$APP_DIR"
test -f .env || { echo "Missing .env"; exit 1; }

python3 - .env <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "LIVE_ORDER_STAGING_ENABLED": "false",
    "LIVE_EXECUTION_ADAPTER": "disabled",
}
lines = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
updated: list[str] = []
for line in lines:
    key = line.split("=", 1)[0]
    if key in updates:
        if key not in seen:
            updated.append(f"{key}={updates[key]}")
            seen.add(key)
    else:
        updated.append(line)
for key, value in updates.items():
    if key not in seen:
        updated.append(f"{key}={value}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

chmod 600 .env
docker compose --profile live up -d --force-recreate pipeline dashboard signer
echo "Live trading disabled."
