.PHONY: build up down scan-once test lint typecheck logs shell clean

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

scan-once:
	docker compose run --rm scan quanterback scan

test:
	docker compose run --rm scan pytest -v

lint:
	docker compose run --rm scan ruff check src tests

typecheck:
	docker compose run --rm scan mypy src

logs:
	docker compose logs -f

shell:
	docker compose run --rm scan bash

clean:
	docker compose down -v
	rm -rf data/cache/*.parquet
