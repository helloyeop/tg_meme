# Public Release Checklist

Use this checklist before changing the GitHub repository visibility to public.

## Repository Safety

- Confirm these files are not tracked:
  - `.env`
  - `config/channels.yaml`
  - `config/strategy.yaml`
  - `data/`
  - `sessions/`
  - `wallet-secrets/`
  - `signer-data/`
  - Telegram `.session` files
  - SQLite database and backup files
- Confirm `.env.example` contains placeholders only.
- Confirm live wallet public keys are placeholders in docs and examples.
- Confirm no private key, seed phrase, bot token, API key, SSH key, Telegram
  session, SQLite database, or VPS IP address appears in tracked files.
- Confirm GitHub repository secrets contain only deployment infrastructure
  values, not application API keys.

Suggested local checks:

```bash
git status --short
git ls-files | rg '(^|/)(\.env|data|sessions|wallet-secrets|signer-data|.*\.session|.*\.db|.*\.sqlite)'
git grep -n -I -E 'OPENAI_API_KEY|TELEGRAM_API_HASH|TELEGRAM_ALERT_BOT_TOKEN|GMGN_API_KEY|HELIUS_API_KEY|JUPITER_API_KEY|PRIVATE_KEY|BEGIN .* PRIVATE KEY|sk-[A-Za-z0-9]|ghp_|github_pat_'
git log --all --name-only --pretty=format: | sort -u | rg '(^|/)(\.env|data|sessions|wallet-secrets|signer-data|.*\.session|.*\.db|.*\.sqlite)'
```

If any real secret was ever committed, do not just delete it in a new commit.
Rotate the secret first, then rewrite history or create a fresh public
repository from a clean export.

## Public Project Description

Short description:

```text
Solana Telegram call analytics, paper trading, and guarded live-trading research system for memecoin channels.
```

Suggested topics:

```text
solana, telegram, telethon, memecoin, paper-trading, streamlit, sqlite, openai, dexscreener, gmgn, jupiter
```

Suggested README positioning:

- Analytics and paper trading are the default behavior.
- Live trading is optional, guarded, isolated, and disabled by default.
- The project is experimental trading infrastructure, not financial advice.
- Operators must use their own API keys, Telegram credentials, and dedicated
  limited-fund wallet.

## Operational Safety

- Keep the production VPS `.env` and runtime config outside Git.
- Keep the dashboard behind SSH or Tailscale unless intentionally publishing it.
- Use a dedicated live wallet with limited funds only.
- Never mount `wallet-secrets/` into collector, pipeline, or dashboard services.
- Run tests before release:

```bash
pytest -q
ruff check .
python -m compileall -q src
```

