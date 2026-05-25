.PHONY: install install-dev init-db test run-pipeline run-collector run-dashboard gmgn-version docker-config deploy-local backup

install:
	python -m pip install -e .
	npm install --prefix .

install-dev:
	python -m pip install -e ".[dev]"
	npm install --prefix .

init-db:
	python -m app.main --mode init-db

test:
	PYTHONPATH=src pytest -q

run-pipeline:
	python -m app.main --mode pipeline --limit 100

run-collector:
	python -m app.main --mode collector

run-dashboard:
	streamlit run src/dashboard/streamlit_app.py

gmgn-version:
	./node_modules/.bin/gmgn-cli --version

docker-config:
	docker compose config

deploy-local:
	bash scripts/deploy_vps.sh

backup:
	bash scripts/backup_sqlite.sh data/app.db data/backups
