# Guarded Live-Trading Path

Paper trading remains the analytics baseline. Live trading uses separate SQLite
tables and a separate engine so experiments do not change paper-trading history.

## Strategy

- A paper entry decision may stage a live `BUY` intent when explicitly enabled.
- The live entry size is `0.5 SOL`.
- An `OPEN` live position stages one `SELL` intent using the target fixed at
  entry time: `+30%` below `$500K`, `+20%` from `$500K` to below `$1M`, or
  `+10%` at or above `$1M`. The emergency stop-loss remains `-70%`.
- The app refuses new entries after `1 SOL` of realized live losses in a day.
- The isolated signer applies an additional `1 SOL` daily BUY spend ceiling.
- Before staging a live entry, the pipeline requests a Jupiter
  `SOL -> token -> SOL` round-trip quote. Entries are refused when the
  immediately executable recovery is below `90%`.
- Open live positions are also monitored with Jupiter full-position SELL
  quotes. A quote-based `-20%` executable loss stages a protective SELL before
  the market-cap-based `-70%` emergency stop-loss.

## Signer Isolation

The main collector, pipeline, and dashboard containers never mount the wallet
keypair. Only the opt-in `signer` service can read
`/run/wallet-secrets/live-wallet.json`. Keep that file outside Git and create it
directly on the VPS with restrictive permissions.

The default environment keeps transaction execution disabled:

```dotenv
LIVE_ORDER_STAGING_ENABLED=false
LIVE_EXECUTION_ADAPTER=disabled
LIVE_WALLET_PUBLIC_KEY=FFDuhHWsDuoUrFAY3Ggk8gty8EeNjArrXLC21UcULvvh
LIVE_SIGNER_BASE_URL=http://signer:8787
LIVE_SIGNER_AUTH_TOKEN=
LIVE_SIGNER_KEYPAIR_PATH=/run/wallet-secrets/live-wallet.json
LIVE_FEE_RESERVE_SOL=0.05
JUPITER_API_KEY=
JUPITER_SWAP_BASE_URL=https://api.jup.ag/swap/v2
```

## Activation Gate

Do not paste a keypair into chat or commit it. Do not create the keypair file on
the MacBook and upload it later. Create it directly inside the VPS terminal so
the secret is not duplicated across devices, cloud-synced folders, backups, or
shell history.

1. Export the dedicated Phantom Solana account private key with Phantom's
   `Show Private Key` action. Never use or reveal the recovery phrase.
2. In the VPS terminal, use hidden input to convert the Base58 private key into
   the Solana CLI JSON keypair format:

   ```bash
   cd /opt/memetrading
   read -s -p "Paste Phantom private key: " PHANTOM_KEY
   echo
   docker run --rm -i node:22-alpine node -e '
   const fs = require("fs");
   const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
   const input = fs.readFileSync(0, "utf8").trim();
   let bytes = [0];
   for (const char of input) {
     const value = alphabet.indexOf(char);
     if (value < 0) throw new Error("Invalid Base58 character");
     let carry = value;
     for (let i = 0; i < bytes.length; i++) {
       carry += bytes[i] * 58;
       bytes[i] = carry & 255;
       carry >>= 8;
     }
     while (carry) {
       bytes.push(carry & 255);
       carry >>= 8;
     }
   }
   for (const char of input) {
     if (char !== "1") break;
     bytes.push(0);
   }
   const result = bytes.reverse();
   if (result.length !== 64) throw new Error(`Expected 64 bytes, got ${result.length}`);
   console.log(JSON.stringify(result));
   ' <<< "$PHANTOM_KEY" > wallet-secrets/live-wallet.json
   unset PHANTOM_KEY
   chmod 600 wallet-secrets/live-wallet.json
   ```

3. Check only the file metadata. Never print the file contents:

   ```bash
   ls -lh wallet-secrets/live-wallet.json
   ```

4. Set a long random `LIVE_SIGNER_AUTH_TOKEN` in `.env`.
   Local `.env` changes are not copied to the VPS during Git deployment. Set
   `JUPITER_API_KEY` directly in `/opt/memetrading/.env` on the VPS as well.
   After deployments that add environment settings, reconcile missing VPS
   defaults without overwriting existing secrets:

   ```bash
   cd /opt/memetrading
   LIVE_WALLET_PUBLIC_KEY_VALUE=FFDuhHWsDuoUrFAY3Ggk8gty8EeNjArrXLC21UcULvvh \
     bash scripts/reconcile_vps_env.sh .env
   ```
5. Keep `LIVE_EXECUTION_ADAPTER=disabled` while running staged-order QA.
6. Start the isolated service with `docker compose --profile live up -d signer`.
7. Verify `docker compose exec signer curl -fsS http://localhost:8787/health`.
8. Enable `LIVE_EXECUTION_ADAPTER=signer_service` only after the staging review.

## Quote-Only QA

Before funding the dedicated wallet, verify Jupiter authentication and routing
without signing or submitting a transaction:

```bash
cd /opt/memetrading
docker compose exec -T signer python -m signer.quote_qa
```

This sends a `0.5 SOL -> USDC` quote request without a `taker`. Jupiter returns
quote data without a signable transaction. The command refuses the result if a
transaction is unexpectedly returned. It never calls `/execute`.

## Balance Readiness

Check the dedicated wallet balance after funding:

```bash
cd /opt/memetrading
docker compose exec -T signer curl -fsS http://localhost:8787/readiness
```

The signer calls Solana RPC `getBalance` with `confirmed` commitment. A live BUY
is refused unless the wallet contains the requested amount plus the configured
`LIVE_FEE_RESERVE_SOL`. The default reserve is `0.05 SOL`.

## Manual Live Swap QA

After balance readiness succeeds, run one explicitly confirmed real-money
round trip before enabling automated live trading:

```bash
cd /opt/memetrading
docker compose exec -T signer \
  python -m signer.live_swap_qa --confirm-live-swap
```

This performs a real `0.01 SOL -> USDC -> SOL` round trip. It is never called by
the pipeline and refuses to run without `--confirm-live-swap`. Network fees,
priority fees, slippage, and price movement can produce a small loss. If the
SELL leg fails, USDC can remain in the dedicated wallet and must be reviewed
before retrying.

The signer stores the Jupiter request id and signed transaction in its isolated
ledger before `/execute`. If the network disconnects while the submission
result is unknown, retrying the same `client_order_id` resumes the same signed
transaction instead of assembling a duplicate swap.

For take-profit exits, the pipeline sends a minimum SOL output to the signer.
The signer refuses to execute the SELL if Jupiter's output quote is below the
position's configured profit target. Emergency stop-loss exits intentionally do
not apply this minimum. Live realized PnL uses actual wallet lamport changes
when available, including network fees and token-account rent.

If a take-profit SELL is refused because the quote is below the minimum output,
the order remains recorded as `FAILED` and the position returns to `OPEN`.
The pipeline keeps monitoring the position and may stage a new SELL after the
configured retry cooldown, which defaults to 30 seconds.

Jupiter quote previews are authenticated signer calls that do not sign or
submit transactions. Sanitized quote details are written to `live_quote_audits`
without the assembled transaction so operators can compare DexScreener market
cap with the actually executable SOL recovery.

## Enable And Emergency Stop

After the manual round-trip QA succeeds, enable automated live trading:

```bash
cd /opt/memetrading
LIVE_ACTIVATION_CONFIRM=ENABLE_0_5_SOL_LIVE_TRADING \
  bash scripts/activate_live_trading.sh
```

This refuses activation unless signer readiness succeeds and the explicit
confirmation value is provided. To stop new live entries and execution:

```bash
cd /opt/memetrading
bash scripts/disable_live_trading.sh
```

## Operational Record

Decision recorded on `2026-05-30`:

- The Phantom private key is entered only through hidden input in the VPS
  terminal.
- The private key is converted to a 64-byte Solana CLI JSON keypair directly on
  the VPS.
- The keypair file must remain at
  `/opt/memetrading/wallet-secrets/live-wallet.json` with mode `600`.
- `wallet-secrets/` and `signer-data/` are excluded from Git.
- Only the optional signer container mounts `wallet-secrets/`; collector,
  pipeline, and dashboard containers do not.
- The dedicated live wallet must contain only the limited funds required for
  automated trading.
- Never print, log, commit, upload, or paste the private key, recovery phrase,
  or keypair JSON into chat.

Activation record on `2026-05-30`:

- Helius RPC free-tier routing was verified with signer readiness.
- Jupiter quote-only QA succeeded without transaction submission.
- A manually approved `0.01 SOL -> USDC -> SOL` round-trip swap succeeded.
- Automated live trading was enabled with `0.5 SOL` entries, take-profit quote
  protection, `-70%` emergency stop-loss, and `1 SOL` daily realized-loss
  limit. On `2026-06-01`, take-profit targets were changed for new positions to
  `+30%` below `$500K`, `+20%` from `$500K` to below `$1M`, and `+10%` at or
  above `$1M`.
- The signer still enforces a `1 SOL` daily BUY ceiling and `0.05 SOL` fee
  reserve.
- Emergency stop command:

  ```bash
  cd /opt/memetrading
  bash scripts/disable_live_trading.sh
  ```

Jupiter Swap V2 `/order` returns an assembled transaction. A signer must sign
that transaction before `/execute` can submit it. Helius `sendTransaction` also
requires a fully signed transaction and must be followed by confirmation
tracking.
