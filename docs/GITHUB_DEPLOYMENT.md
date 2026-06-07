# GitHub Deployment Workflow

Recommended flow while the project is still changing:

```text
MacBook -> git commit -> git push -> VPS git pull -> docker compose restart
```

Keep GitHub as the source of truth for code only. Keep runtime state on the VPS.

## Commit These

- `src/`
- `tests/`
- `docs/`
- `qa/`
- `config/*.example.yaml`
- `.env.example`
- `Dockerfile`
- `docker-compose.yml`
- `package.json`
- `package-lock.json`
- `pyproject.toml`
- deployment scripts

## Never Commit These

- `.env`
- `data/`
- `sessions/`
- `wallet-secrets/`
- `signer-data/`
- `config/channels.yaml`
- `config/strategy.yaml`
- Telegram `.session` files
- SQLite `.db`, `.sqlite`, `-wal`, and `-shm` files
- SSH keys, wallet keypairs, private keys, seed phrases, API keys, bot tokens
- local logs

## First-Time Local Git Setup

```bash
git init
git branch -M main
git add .
git status
git commit -m "Initial v1 paper trading system"
```

Create a private GitHub repository, then:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

## First-Time VPS Setup

```bash
sudo mkdir -p /opt/memetrading
sudo chown "$USER":"$USER" /opt/memetrading
git clone git@github.com:<owner>/<repo>.git /opt/memetrading
cd /opt/memetrading
cp .env.example .env
cp config/channels.example.yaml config/channels.yaml
cp config/strategy.example.yaml config/strategy.yaml
mkdir -p data sessions
docker compose build
docker compose run --rm init-db
docker compose run --rm collector
docker compose up -d collector pipeline dashboard
```

The interactive collector run creates the Telethon session under `sessions/`.
The dashboard is intentionally available only on the VPS loopback address. From
your Mac, connect with:

```bash
ssh -N -L 8501:127.0.0.1:8501 deploy@<vps-ip>
```

Then browse to `http://127.0.0.1:8501`.

## Repeated Manual Deploy

On the VPS:

```bash
cd /opt/memetrading
bash scripts/deploy_vps.sh
```

This script:

1. Checks local-only config exists.
2. Backs up SQLite if `data/app.db` exists.
3. Pulls the latest `main`.
4. Rebuilds Docker images.
5. Runs DB initialization.
6. Restarts collector, pipeline, and dashboard.

## Optional Manual GitHub Actions Deploy

The workflow at `.github/workflows/deploy-vps.yml` is manual only.

Required GitHub repository secrets:

- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`

Optional secrets:

- `VPS_PORT`
- `VPS_APP_DIR`

Do not add application API keys to GitHub Actions. Keep `.env` only on the VPS.

## Public Repository Notes

If the repository is made public, keep it as code and documentation only.
Runtime state and credentials must remain on the operator's machine or VPS.
Before changing visibility, run the checks in `docs/PUBLIC_RELEASE_CHECKLIST.md`.
