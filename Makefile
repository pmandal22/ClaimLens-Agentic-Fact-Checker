.PHONY: install lint test api ui eval up down

install:
	pip install -r requirements.txt && pip install -e .

lint:
	ruff check . && mypy src

test:
	pytest tests/unit tests/integration

api:
	uvicorn apps.api.main:app --reload

ui:
	cd apps/web && npm install && npm run dev

eval:
	python evals/run_eval.py

# Docker stack. --env-file makes compose read the root .env for ${...} values such as
# WORKER_COUNT; by default it would look for infra/.env.
COMPOSE = docker compose -f infra/docker-compose.yml $(if $(wildcard .env),--env-file .env)

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down
