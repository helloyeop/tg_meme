# QA Checklist Prompt

Use this prompt when asking a reviewer or another AI agent to validate this project.

```text
You are QA testing a Solana-only Telegram meme coin call analytics and paper-trading system.

Validate only Version 1 behavior:
- No real trading.
- DRY_RUN defaults to true.
- No private key is required or used.
- Telethon user account collection is used for Telegram reading.
- Public and user-accessible private channels can be configured.
- All Telegram messages are stored and analyzed.
- OpenAI `gpt-5.4-nano` is the default classifier; low-confidence high-impact intents can be reviewed by `gpt-5.4-mini`.
- LLM usage tokens, latency, and whether a message was reviewed are stored for cost monitoring.
- A CA-only post can combine with exactly one unused same-channel action-bearing message from the prior configured context window (default 60 seconds).
- A self-contained CA post never inherits earlier context, and multiple candidate action messages are marked ambiguous without contextual auto-entry.
- Only Solana contract addresses are extracted.
- Same channel + same CA is merged into one Call Event, including re-entry/round 2/back in messages.
- Data source priority is GMGN -> DexScreener -> Helius.
- SQLite is used.
- Paper entry size defaults to 0.5 SOL.
- Daily max paper loss defaults to 0.5 SOL.
- Market cap, rather than unit token price, drives Call Event performance multiples and paper PnL/exit decisions.
- Once a BUY_CALL exists, time-decay scoring is based on its first actionable call time rather than an earlier non-actionable CA observation.
- Open paper positions are rechecked every 5 seconds with DexScreener market-cap data.
- Closed paper positions are observed after exit on a slower default 15-minute interval for counterfactual low/peak/latest results, reusing a recent existing token snapshot instead of making an extra request.
- The fast monitor batches no more than 30 Solana token addresses per DexScreener request and keeps an internal request budget below the documented API limit.
- Large market/security API raw snapshot JSON storage defaults to off while normalized fields remain available.
- Telegram alerts are outbound personal bot messages only.
- Streamlit dashboard reads from SQLite and does not run the collector; Context Links exposes contextual classification audit records and Closed Trades exposes post-exit counterfactual outcomes.

Open qa/checklist.html in a browser and mark each item Pass, Fail, or N/A.
For failures, write the exact command, screen, file, or observed behavior.
```
