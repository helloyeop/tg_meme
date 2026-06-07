# Memecoin Telegram Call Bot

Solana-only Telegram call analytics, market-cap tracking, paper trading, and
guarded live-trading research system for memecoin call channels.

The project is designed around auditability and safety:

- `DRY_RUN=true` and live execution disabled by default.
- Paper trading remains the baseline analytics ledger.
- Optional live trading is isolated behind a signer service that is not mounted
  by the collector, pipeline, or dashboard containers.
- No private key, seed phrase, Telegram session, SQLite database, or runtime
  `.env` file belongs in Git.
- Telegram reading uses Telethon user-account login for channels the operator
  can already access.
- Alerts and live controls use a personal Telegram bot.
- Solana contract addresses are the only supported token identifiers.
- Market cap is the primary performance reference; price is retained as market
  context.
- Open paper positions are market-cap monitored every 5 seconds through bounded
  DexScreener batch requests.
- Closed paper positions continue counterfactual market-cap tracking every 15
  minutes for strategy analysis.
- Message classification defaults to OpenAI `gpt-5.4-nano`, with selective
  low-confidence high-impact review by `gpt-5.4-mini`.
- A CA-only post can inherit one unused same-channel action message from the
  preceding 60 seconds; ambiguous candidates never trigger contextual entry.

This is experimental trading infrastructure, not financial advice. Use a
dedicated wallet with limited funds if enabling live trading.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
cp config/channels.example.yaml config/channels.yaml
cp config/strategy.example.yaml config/strategy.yaml
npm install --prefix .
PYTHONPATH=src python -m app.main --mode init-db
```

Fill `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env` before collector or history modes.
Set `LLM_API_KEY` to enable the default OpenAI classifier. If it is missing or the configured
provider fails, local Ollama can be kept as fallback through `LLM_FALLBACK_TO_OLLAMA=true`.
Keep `.env`, `sessions/`, `data/`, `wallet-secrets/`, and `signer-data/` local only.

The default LLM flow classifies every message with `gpt-5.4-nano`. Results classified as
`BUY_CALL`, `WARNING`, `SOLD`, or `TAKE_PROFIT` below confidence `0.75` are reviewed once
with `gpt-5.4-mini`. Usage tokens, latency, and review status are stored in `message_analysis`.
For split posts such as `entry.` followed by a CA, `CONTEXT_LINKING_ENABLED=true`
links exactly one unused same-channel action-bearing message within
`CONTEXT_LINK_WINDOW_SECONDS=60`. A message that already includes its own CA is
classified on its own, and multiple pending action messages are stored as ambiguous
instead of producing an automatic contextual `BUY_CALL`.

## Run

Initialize SQLite:

```bash
PYTHONPATH=src python -m app.main --mode init-db
```

Collect Telegram history once:

```bash
PYTHONPATH=src python -m app.main --mode history --limit 50
```

Run live collector:

```bash
PYTHONPATH=src python -m app.main --mode collector
```

Process unanalyzed stored messages:

```bash
PYTHONPATH=src python -m app.main --mode pipeline --limit 100
```

Refresh market/security data for a small set of open events:

```bash
PYTHONPATH=src python -m app.main --mode refresh --limit 5
```

Run one forced fast refresh for open paper positions:

```bash
PYTHONPATH=src python -m app.main --mode position-refresh
```

Run one forced post-exit refresh for closed paper positions:

```bash
PYTHONPATH=src python -m app.main --mode closed-position-refresh
```

Continuous `--mode all` operation automatically evaluates open paper positions every
`PAPER_FAST_MONITOR_SECONDS` seconds, default `5`. It uses DexScreener's documented
multi-token Solana endpoint with at most `30` tokens in one request. With defaults,
the fast monitor issues at most `12` requests per minute; the shared client budget is
set to `240` requests per minute to retain headroom below the documented `300` per
minute endpoint limit for new call ingestion and routine refreshes.
Run only one continuous pipeline worker per database; launching duplicate `--mode all`
workers would create independent API request windows and duplicate paper observations.
Closed positions do not use the 5-second monitor. With defaults they receive a dedicated
DexScreener observation at most once per `PAPER_CLOSED_MONITOR_SECONDS=900`; if a currently
open position already provides a recent snapshot for that token, no additional request is made.
The `Closed Trades` dashboard view compares realized PnL with observed post-exit low, peak,
and latest market-cap outcomes.

Snapshot raw payloads are disabled by default through
`STORE_MARKET_SNAPSHOT_RAW_JSON=false` and `STORE_SECURITY_SNAPSHOT_RAW_JSON=false`.
Normalized market-cap, liquidity, holder, and risk fields remain stored for scoring and
dashboard use. To reclaim space from a database created before this setting:

```bash
bash scripts/compact_sqlite_raw_payloads.sh data/app.db data/backups
```

The compaction command first creates a SQLite backup, then removes stored raw snapshot
payloads and runs `VACUUM`. Stop writer services while running it on a live VPS.

Paper entries, exits, PnL, and dashboard multiples are evaluated from `market_cap_usd`.
For an existing SQLite database, `--mode init-db` adds the market-cap tracking
columns. Historical events keep a blank first-seen market cap unless it was
captured at that event's original processing time; a later token snapshot is
not treated as a channel's first-call value.
Scoring time decay starts from `first_actionable_call_time` once a `BUY_CALL`
is actually recognized, so an earlier discussion-only CA mention does not make
a later actionable call appear stale. An explicit same-channel recall after the
default 60-minute cooldown stays in the same Call Event but adds a
`token_actionable_signals` anchor. Scoring restarts from that anchor while retaining
a chase-risk penalty based on the market-cap increase since the first observation.
Recall-based paper and live entries use half the normal size by default (`0.25 SOL`).
The Streamlit `Context Links` page records which preceding message was linked, or
rejected as ambiguous, for audit.

GMGN read-only data uses the local CLI by default:

```bash
./node_modules/.bin/gmgn-cli --version
```

Run dashboard:

```bash
PYTHONPATH=src streamlit run src/dashboard/streamlit_app.py
```

Open QA checklist:

```bash
open qa/checklist.html
```

Open the visual implementation overview:

```bash
open docs/current_implementation_overview.html
```

## VPS Readiness

Persist these paths on a VPS:

- `data/` for SQLite.
- Telethon session file from `TELEGRAM_SESSION_NAME`.
- `.env` and `config/*.yaml`.
- `wallet-secrets/` and `signer-data/` only when explicitly enabling the live
  signer profile.

Recommended services:

- collector service: `python -m app.main --mode collector`
- pipeline service: `python -m app.main --mode all`
- dashboard service: `streamlit run src/dashboard/streamlit_app.py`

Deployment docs:

- `docs/VPS_DEPLOYMENT.md`
- `docs/GITHUB_DEPLOYMENT.md`
- `docs/LIVE_TRADING.md`
- `docs/PUBLIC_RELEASE_CHECKLIST.md`
