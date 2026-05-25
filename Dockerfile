FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md package.json package-lock.json ./
COPY src ./src
COPY config ./config
COPY docs ./docs
COPY qa ./qa

RUN pip install -e . \
    && npm ci --omit=dev

RUN mkdir -p /app/data /app/sessions

ENV DATABASE_URL=sqlite:////app/data/app.db \
    TELEGRAM_SESSION_DIR=/app/sessions \
    GMGN_CLI_PATH=/app/node_modules/.bin/gmgn-cli

CMD ["python", "-m", "app.main", "--mode", "pipeline"]
