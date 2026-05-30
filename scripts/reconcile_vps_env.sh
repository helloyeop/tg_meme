#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"
LIVE_WALLET_PUBLIC_KEY_VALUE="${LIVE_WALLET_PUBLIC_KEY_VALUE:-}"

test -f "$ENV_FILE" || { echo "Missing $ENV_FILE"; exit 1; }

set_if_missing() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$ENV_FILE"; then
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

if [[ -n "$LIVE_WALLET_PUBLIC_KEY_VALUE" ]]; then
  python3 - "$ENV_FILE" "$LIVE_WALLET_PUBLIC_KEY_VALUE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
wallet = sys.argv[2]
lines = [
    line
    for line in path.read_text(encoding="utf-8").splitlines()
    if not line.startswith("LIVE_WALLET_PUBLIC_KEY=")
]
lines.extend(
    [
        "",
        "# Dedicated live wallet public address",
        f"LIVE_WALLET_PUBLIC_KEY={wallet}",
    ]
)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi

set_if_missing TELEGRAM_SESSION_DIR /app/sessions
set_if_missing CONTEXT_LINKING_ENABLED true
set_if_missing CONTEXT_LINK_WINDOW_SECONDS 60
set_if_missing GMGN_CLI_PATH /app/node_modules/.bin/gmgn-cli
set_if_missing JUPITER_API_KEY ""
set_if_missing JUPITER_SWAP_BASE_URL https://api.jup.ag/swap/v2
set_if_missing STORE_MARKET_SNAPSHOT_RAW_JSON false
set_if_missing STORE_SECURITY_SNAPSHOT_RAW_JSON false
set_if_missing OPEN_EVENT_REFRESH_SECONDS 300
set_if_missing PAPER_FAST_MONITOR_ENABLED true
set_if_missing PAPER_FAST_MONITOR_SECONDS 5
set_if_missing PAPER_FAST_MONITOR_MAX_TOKENS 30
set_if_missing PAPER_CLOSED_MONITOR_ENABLED true
set_if_missing PAPER_CLOSED_MONITOR_SECONDS 900
set_if_missing PAPER_CLOSED_MONITOR_MAX_TOKENS 30
set_if_missing DEXSCREENER_REQUEST_BUDGET_PER_MINUTE 240

chmod 600 "$ENV_FILE"
echo "Reconciled $ENV_FILE without changing existing secret values."
