# Guarded Live-Trading Path

Paper trading remains the analytics baseline. Live trading uses separate SQLite
tables and a separate engine so experiments do not change paper-trading history.

## Strategy

- A paper entry decision may stage a live `BUY` intent when explicitly enabled.
- The live entry size is `0.5 SOL`.
- An `OPEN` live position stages one `SELL` intent at `+10%` profit or `-70%`
  emergency stop-loss.
- The app refuses new entries after `1 SOL` of realized live losses in a day.
- The isolated signer applies an additional `1 SOL` daily BUY spend ceiling.

## Signer Isolation

The main collector, pipeline, and dashboard containers never mount the wallet
keypair. Only the opt-in `signer` service can read
`/run/wallet-secrets/live-wallet.json`. Keep that file outside Git and create it
directly on the VPS with restrictive permissions.

The default environment keeps transaction execution disabled:

```dotenv
LIVE_ORDER_STAGING_ENABLED=false
LIVE_EXECUTION_ADAPTER=disabled
LIVE_WALLET_PUBLIC_KEY=4fLfmQfC6zjKxsiPknHtnNt32bRztfZYbkoYL3t5RTzx
LIVE_SIGNER_BASE_URL=http://signer:8787
LIVE_SIGNER_AUTH_TOKEN=
LIVE_SIGNER_KEYPAIR_PATH=/run/wallet-secrets/live-wallet.json
JUPITER_API_KEY=
JUPITER_SWAP_BASE_URL=https://api.jup.ag/swap/v2
```

## Activation Gate

Do not paste a keypair into chat or commit it. On the VPS:

1. Create `wallet-secrets/live-wallet.json` directly on the VPS and set mode
   `600`.
2. Set a long random `LIVE_SIGNER_AUTH_TOKEN` in `.env`.
3. Keep `LIVE_EXECUTION_ADAPTER=disabled` while running staged-order QA.
4. Start the isolated service with `docker compose --profile live up -d signer`.
5. Verify `docker compose exec signer curl -fsS http://localhost:8787/health`.
6. Enable `LIVE_EXECUTION_ADAPTER=signer_service` only after the staging review.

Jupiter Swap V2 `/order` returns an assembled transaction. A signer must sign
that transaction before `/execute` can submit it. Helius `sendTransaction` also
requires a fully signed transaction and must be followed by confirmation
tracking.
