#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/memetrading}"
CONFIRMATION="${LIVE_ACTIVATION_CONFIRM:-}"

if [[ "$CONFIRMATION" != "ENABLE_0_5_SOL_LIVE_TRADING" ]]; then
  echo "Refusing activation without LIVE_ACTIVATION_CONFIRM=ENABLE_0_5_SOL_LIVE_TRADING"
  exit 1
fi

cd "$APP_DIR"
test -f .env || { echo "Missing .env"; exit 1; }
test -f wallet-secrets/live-wallet.json || { echo "Missing live wallet keypair"; exit 1; }

docker compose --profile live up -d signer
readiness="$(docker compose exec -T signer curl -fsS http://localhost:8787/readiness)"
python3 - "$readiness" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "ready":
    raise SystemExit(f"Signer is not ready: {payload}")
if not payload.get("jupiter_api_key_configured"):
    raise SystemExit("JUPITER_API_KEY is not configured.")
print("Signer readiness passed.")
PY

python3 - .env <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "LIVE_ORDER_STAGING_ENABLED": "true",
    "LIVE_EXECUTION_ADAPTER": "signer_service",
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
echo "Live trading enabled. Use scripts/disable_live_trading.sh for emergency stop."
