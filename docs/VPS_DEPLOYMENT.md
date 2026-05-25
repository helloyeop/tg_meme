# VPS Deployment

This project is designed so local development can move to a VPS without changing application code.

## Persisted Paths

Persist and back up:

- `data/`: SQLite database.
- `sessions/`: Telethon user session.
- `.env`: runtime secrets and API keys.
- `config/channels.yaml` and `config/strategy.yaml`: local runtime config.

Do not commit `.env`, `sessions/`, or local config files.

## Option A: Docker Compose

On the VPS:

```bash
git clone <repo> /opt/memetrading
cd /opt/memetrading
cp .env.example .env
cp config/channels.example.yaml config/channels.yaml
cp config/strategy.example.yaml config/strategy.yaml
mkdir -p data sessions
docker compose build
docker compose run --rm init-db
docker compose up -d collector pipeline dashboard
```

For repeated deploys after pushing code to GitHub:

```bash
cd /opt/memetrading
bash scripts/deploy_vps.sh
```

Open:

```text
http://<vps-ip>:8501
```

For the first Telegram login, run the collector interactively:

```bash
docker compose run --rm collector
```

After the session is created under `sessions/`, start services normally.

## Option B: systemd

Recommended target directory:

```text
/opt/memetrading
```

Setup:

```bash
cd /opt/memetrading
python3.10 -m venv .venv
.venv/bin/pip install -e .
npm install --prefix .
cp .env.example .env
cp config/channels.example.yaml config/channels.yaml
cp config/strategy.example.yaml config/strategy.yaml
mkdir -p data sessions
.venv/bin/python -m app.main --mode init-db
```

Copy services:

```bash
sudo cp deploy/systemd/memetrading-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memetrading-collector memetrading-pipeline memetrading-dashboard
```

Logs:

```bash
journalctl -u memetrading-collector -f
journalctl -u memetrading-pipeline -f
journalctl -u memetrading-dashboard -f
```

## Backup

SQLite backup:

```bash
bash scripts/backup_sqlite.sh data/app.db data/backups
```

Existing databases that contain repeated full API payloads can be compacted after
stopping collector and pipeline services:

```bash
bash scripts/compact_sqlite_raw_payloads.sh data/app.db data/backups
```

New installations keep normalized snapshot fields while raw market/security API JSON
storage is off by default.

Suggested cron:

```cron
0 * * * * cd /opt/memetrading && bash scripts/backup_sqlite.sh data/app.db data/backups >/dev/null
```

## Operational Notes

- Real trading remains disabled in v1.
- `DRY_RUN=true` should stay enabled.
- Do not set or store private keys for this version.
- Keep firewall access to Streamlit restricted to your IP or tunnel it over SSH.
- Run exactly one `memetrading-pipeline` instance so the DexScreener request budget and fast paper-position observations remain singular.
- Leave raw snapshot payload storage disabled unless temporary API diagnostics require it.
- See `docs/GITHUB_DEPLOYMENT.md` for the GitHub pull-based workflow.
