# Guarded Live-Trading Path

Paper trading remains the analytics baseline. Live trading uses separate SQLite
tables and a separate engine so experiments do not change paper-trading history.

## Current Phase

The current implementation stages isolated live order intents only:

- A paper entry decision may stage a live `BUY` intent when explicitly enabled.
- The default live entry size is `0.05 SOL`.
- A staged live position targets a full exit at `+10%` market cap.
- An `OPEN` live position stages one `SELL` intent when the target is reached.
- Transaction signing and submission are intentionally disabled.
- No private key is accepted or stored by the application.

Relevant environment defaults:

```dotenv
LIVE_ORDER_STAGING_ENABLED=false
LIVE_EXECUTION_ADAPTER=disabled
LIVE_WALLET_PUBLIC_KEY=
JUPITER_API_KEY=
JUPITER_SWAP_BASE_URL=https://api.jup.ag/swap/v2
```

## Next Activation Gate

Before any real transaction submission is implemented or enabled:

1. Choose an external signer that does not expose a private key to this app.
2. Confirm the dedicated wallet public key and fund it conservatively.
3. Confirm the live entry amount, maximum open positions, daily loss cap, and
   emergency stop-loss behavior.
4. Add transaction confirmation tracking and Telegram alerts.
5. Run staged-order QA before enabling a signer-backed adapter.

Jupiter Swap V2 `/order` returns an assembled transaction. A signer must sign
that transaction before `/execute` can submit it. Helius `sendTransaction` also
requires a fully signed transaction and must be followed by confirmation
tracking.
