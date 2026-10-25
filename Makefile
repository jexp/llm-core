.PHONY: up down env install lint test run smoke verify

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

env:
	@test -f .env || cp .env.example .env
	@echo "ensure LLMC_API_KEY / TYPESAFE_API_KEY are set in .env"

install:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest -q

run:
	uv run uvicorn kernel.server:app --port 8000

smoke:
	uv run python -m personas.clients.smoke_client --base-url $${BASE_URL:-http://localhost:8000}

verify:
	uv run python -m kernel.auditor --base-url $${BASE_URL:-http://localhost:8000}
