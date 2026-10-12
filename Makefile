.PHONY: install data import up down setup api ui test lint eval

install:            ## Python deps (uv venv) + frontend deps
	uv sync
	cd frontend && npm install

data:               ## Download PrimeKG and convert it to Neo4j import CSVs
	./data/scripts/download_primekg.sh
	uv run python data/scripts/build_import.py

import:             ## Bulk-load the CSVs into Neo4j (wipes the database)
	docker compose down
	docker compose run --rm --no-deps neo4j neo4j-admin database import full neo4j \
		--overwrite-destination --id-type=integer \
		--nodes=/import/nodes.csv --relationships=/import/relationships.csv

up:                 ## Start Neo4j and wait until healthy
	docker compose up -d --wait

down:
	docker compose down

setup:              ## Indexes + name embeddings for entity linking (~15 min)
	cd backend && uv run python -m app.graph.setup

api:                ## FastAPI on :8000
	uv run uvicorn app.api.main:app --app-dir backend --port 8000 --reload

ui:                 ## React UI on :5173
	cd frontend && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check backend eval

eval:               ## Run the evaluation suite (see eval/README)
	uv run python eval/run_eval.py
