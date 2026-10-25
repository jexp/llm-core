.PHONY: up down env install lint test test-watch run smoke verify

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

test-watch:
	@touch .llmc_watch_marker; \
	echo "watching kernel/ modules/ tests/ contracts/ (Ctrl-C to stop)"; \
	while true; do \
	  changed=$$(find kernel modules tests contracts \( -name '*.py' -o -name '*.yaml' \) -newer .llmc_watch_marker | head -1); \
	  if [ -n "$$changed" ]; then \
	    touch .llmc_watch_marker; \
	    uv run pytest -q --tb=short; \
	  fi; \
	  sleep 1; \
	done

run:
	uv run uvicorn kernel.server:app --port 8000

smoke:
	uv run python -m personas.clients.smoke_client --base-url $${BASE_URL:-http://localhost:8000}

verify:
	uv run python -m kernel.auditor --base-url $${BASE_URL:-http://localhost:8000}
