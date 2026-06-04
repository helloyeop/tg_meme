# QA Checklist Prompt

Use this prompt when asking a reviewer or another AI agent to validate this project.

```text
You are QA testing a Solana-only Telegram meme coin call analytics system with
paper-trading analytics and an isolated, explicitly enabled live-trading extension.

Validate the current behavior:
- DRY_RUN defaults to true.
- Live trading is disabled until explicitly enabled.
- A live private key is mounted only into the isolated signer service and is never exposed to collector, pipeline, or dashboard containers.
- Telethon user account collection is used for Telegram reading.
- Public and user-accessible private channels can be configured.
- All Telegram messages are stored and analyzed.
- OpenAI `gpt-5.4-nano` is the default classifier; low-confidence high-impact intents can be reviewed by `gpt-5.4-mini`.
- LLM usage tokens, latency, and whether a message was reviewed are stored for cost monitoring.
- A CA-only post can combine with exactly one unused same-channel action-bearing message from the prior configured context window (default 60 seconds).
- A self-contained CA post never inherits earlier context, and multiple candidate action messages are marked ambiguous without contextual auto-entry.
- In monitored call channels, a Solana CA post is treated as an actionable BUY_CALL unless it is explicitly warning, bearish, sold, or take-profit related.
- Only Solana contract addresses are extracted.
- Same channel + same CA is merged into one Call Event, including re-entry/round 2/back in messages.
- An explicit same-channel recall after the default 60-minute cooldown remains in the same Call Event but creates a new actionable signal anchor.
- Recall scoring restarts timing and market-cap position from the newest actionable anchor while retaining a chase-risk penalty based on the increase since the first observation.
- Recall-based paper and live entries use half the normal size by default: 0.25 SOL instead of 0.5 SOL.
- Data source priority is GMGN -> DexScreener -> Helius.
- SQLite is used.
- Paper entry size defaults to 0.5 SOL.
- Daily max paper loss defaults to 2 SOL.
- Market cap, rather than unit token price, drives Call Event performance multiples and paper PnL/exit decisions.
- Before any eligible recall, time-decay scoring is based on the first actionable call time rather than an earlier non-actionable CA observation.
- Open paper positions are rechecked every 5 seconds with DexScreener market-cap data.
- Closed paper positions are observed after exit on a slower default 15-minute interval for counterfactual low/peak/latest results, reusing a recent existing token snapshot instead of making an extra request.
- A new live position fixes its take-profit target from entry-time market cap: +30% below $500K, +20% from $500K to below $1M, and +10% at or above $1M.
- Existing live positions keep their stored target_profit_pct and target_market_cap_usd; strategy edits are not applied retroactively.
- New live entries require a Jupiter SOL -> token -> SOL preview with at least 90% immediately executable recovery.
- Open live positions receive Jupiter full-position SELL previews for executable take-profit evaluation and audit records. The only live stop-loss is the market-cap-based -70% emergency stop.
- Sanitized Jupiter quote audit rows persist recovery, price impact, slippage, and fee fields without persisting an assembled transaction.
- The fast monitor batches no more than 30 Solana token addresses per DexScreener request and keeps an internal request budget below the documented API limit.
- Large market/security API raw snapshot JSON storage defaults to off while normalized fields remain available.
- Telegram alerts are outbound personal bot messages only.
- Streamlit dashboard reads from SQLite and does not run the collector; Context Links exposes contextual classification audit records and Closed Trades exposes post-exit counterfactual outcomes.

Open qa/checklist.html in a browser and mark each item Pass, Fail, or N/A.
For failures, write the exact command, screen, file, or observed behavior.
```
