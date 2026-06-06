# Live Control Bot

The live control bot lets the configured Telegram alert chat inspect and control live positions.
It uses the existing alert bot token and chat id.

## Safety model

- The bot never calls the signer directly.
- Manual exits are staged as `live_orders` rows and executed by the existing live executor.
- New live entries can be paused through `live_control_state`.
- Manual sell refuses to create a second pending SELL order for the same position.
- Only `TELEGRAM_ALERT_CHAT_ID` is authorized.

## Commands

- `/live` lists active live positions and whether new entries are paused.
- `/pos <id>` shows details for one live position.
- `/pause_live` pauses new live entries. Existing positions can still be sold.
- `/resume_live` resumes new live entries.
- `/sell <id>` stages a full manual SELL for an OPEN position.
- `/tp <id> <pct>` updates a position take-profit percent.
- `/sl <id> <pct>` updates a position stop-loss percent, for example `/sl 5 -70`.

Partial sell commands are intentionally not supported yet because the current live executor sells
the full confirmed token amount for a position.
