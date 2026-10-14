# LLM Budget Manager — единая точка входа (12_plan.md §8). Список целей: make help
#
# Тесты без Docker (локальный PostgreSQL):  make test-unit, make test
# Тесты в Docker (чистый postgres:18):      make test-docker
# Поставка (T087):                          make build, make up, make smoke, make down

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

BACKEND      := app/backend
UV_RUN       := uv run --project $(BACKEND) --frozen --all-extras
COMPOSE      := docker compose
COMPOSE_TEST := $(COMPOSE) -f deploy/docker-compose.test.yml
COMPOSE_MAIN := $(COMPOSE) -f deploy/docker-compose.yml --env-file deploy/.env
IMAGE        ?= bm:local

# Тестовая БД для make test без Docker. Переопределяется: make test DATABASE_URL=postgresql://...
# Значение по умолчанию — локальный тестовый PostgreSQL стенда разработки. Имя БД обязано содержать «test».
DATABASE_URL ?= postgresql://postgres@127.0.0.1:5433/bm_test
export DATABASE_URL

.PHONY: help sync test-unit test test-docker lint fmt lint-imports check egress-host-check \
        build up down smoke logs backup restore load failover e2e clean

help: ## Показать цели
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sync: ## Установить зависимости backend из uv.lock (dev + extras)
	uv sync --project $(BACKEND) --frozen --all-extras

# ---------- тесты и качество (без Docker) ----------

test-unit: sync ## Юнит-тесты без БД и Docker: pytest tests/unit + vitest
	bash deploy/run-tests.sh unit

test: sync ## Все тесты на локальном PostgreSQL (DATABASE_URL) + import-linter + vitest
	bash deploy/run-tests.sh full

lint: sync lint-imports ## ruff (check + format --check) и import-linter
	$(UV_RUN) ruff check --config $(BACKEND)/pyproject.toml $(BACKEND) tests deploy/ci
	$(UV_RUN) ruff format --config $(BACKEND)/pyproject.toml --check $(BACKEND) tests deploy/ci

fmt: sync ## Автоформатирование ruff
	$(UV_RUN) ruff check --config $(BACKEND)/pyproject.toml --fix $(BACKEND) tests deploy/ci
	$(UV_RUN) ruff format --config $(BACKEND)/pyproject.toml $(BACKEND) tests deploy/ci

lint-imports: sync ## Архитектурные контракты (app/backend/importlinter.ini)
	cd $(BACKEND) && uv run --frozen --all-extras lint-imports --config importlinter.ini --no-logo

check: sync ## SDL: SBOM CycloneDX, лицензии, pip-audit, npm audit, Trivy, import-linter (T088)
	IMAGE=$(IMAGE) bash deploy/ci/check.sh

egress-host-check: ## Стенд QA (root): 0 пакетов наружу во время make smoke (T088)
	bash deploy/ci/egress-host-check.sh

# ---------- Docker ----------

test-docker: ## make test в контейнере против чистого postgres:18 (нужен Docker Hub или зеркало)
	rc=0; $(COMPOSE_TEST) run --rm --build tests || rc=$$?; $(COMPOSE_TEST) down -v --remove-orphans; exit $$rc

build: ## Собрать образ api/worker (стадия python-runtime)
	docker build -f deploy/Dockerfile --target python-runtime -t $(IMAGE) .

define need_compose
	@test -f deploy/docker-compose.yml || { echo "deploy/docker-compose.yml появится в T087"; exit 2; }
	@test -f deploy/.env || { echo "нет deploy/.env: cp deploy/.env.example deploy/.env и заполните секреты"; exit 2; }
endef

up: ## Поднять стек и дождаться readyz (T087)
	$(need_compose)
	$(COMPOSE_MAIN) up -d --build --wait --wait-timeout 120

down: ## Остановить стек (данные в томах сохраняются)
	$(need_compose)
	$(COMPOSE_MAIN) down

logs: ## Логи стека
	$(need_compose)
	$(COMPOSE_MAIN) logs -f --tail=200

smoke: ## Smoke против поднятого стека (BASE_URL, по умолчанию http://127.0.0.1:8080)
	BASE_URL=$${BASE_URL:-http://127.0.0.1:8080} $(UV_RUN) pytest tests/smoke

backup restore load failover e2e: ## Появятся в T087 (backup/restore), T086 (load), QA (failover, e2e)
	@echo "make $@: ещё не реализовано (см. 12_tasks.md)"; exit 2

clean: ## Удалить локальные артефакты (venv, кэши, deploy/ci/out)
	rm -rf $(BACKEND)/.venv deploy/ci/out .pytest_cache .ruff_cache
