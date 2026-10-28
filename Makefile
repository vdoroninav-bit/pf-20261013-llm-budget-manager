# LLM Budget Manager — единая точка входа (12_plan.md §8). Список целей: make help
#
# Тесты без Docker (локальный PostgreSQL):  make test-unit, make test
# Тесты в Docker (чистый postgres:18):      make test-docker
# Поставка (T087):                          make build, make up, make smoke, make backup, make restore, make down
# Стек из локальных процессов (без образов): make up-local; затем make smoke|backup|restore|failover BM_STACK=local
#                                           (BM_STACK=local указывается явно); make down-local
# Статическая проверка поставки:            make compose-check
# Нагрузка SC-001/SC-004 (T086):            make up && make load | make up-local && make load BM_STACK=local

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

BACKEND      := app/backend
UV_RUN       := uv run --project $(BACKEND) --frozen
PY_SRC       := $(BACKEND) app/client tests deploy
LOCAL_STACK  := bash deploy/local/stack.sh
# DATABASE_URL экспортируется ниже для make test (тестовая БД). docker compose отдаёт окружению приоритет над
# --env-file, поэтому без env -u тестовая строка подменила бы DATABASE_URL стека из deploy/.env.
COMPOSE      := env -u DATABASE_URL docker compose
COMPOSE_TEST := $(COMPOSE) -f deploy/docker-compose.test.yml
COMPOSE_MAIN := $(COMPOSE) -f deploy/docker-compose.yml --env-file deploy/.env
# Та же команда для проверок smoke/failover, которые сами зовут compose (tests/smoke, tests/failover/stack.py):
# иначе тестовый DATABASE_URL из окружения make попал бы в интерполяцию compose-файла.
BM_COMPOSE   ?= $(COMPOSE_MAIN)
export BM_COMPOSE
IMAGE        ?= bm:local
# compose — стек docker compose (по умолчанию); local — стек из локальных процессов (deploy/local/stack.sh).
BM_STACK     ?= compose
export BM_STACK
# В режиме local переменные стека (BASE_URL, DATABASE_URL кластера стека, учётка администратора) — из stack.sh env.
ifeq ($(BM_STACK),local)
STACK_ENV    := eval "$$($(LOCAL_STACK) env)";
else
STACK_ENV    :=
endif

# Тестовая БД для make test без Docker. Переопределяется: make test DATABASE_URL=postgresql://...
# Значение по умолчанию — локальный тестовый PostgreSQL стенда разработки. Имя БД обязано содержать «test».
DATABASE_URL ?= postgresql://postgres@127.0.0.1:5433/bm_test
export DATABASE_URL

.PHONY: help sync test-unit test test-docker lint fmt lint-imports check egress-host-check compose-check \
        build up down smoke logs backup restore load failover e2e clean up-local down-local appimage macos-app

help: ## Показать цели
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sync: ## Установить зависимости backend из uv.lock (с dev-группой)
	uv sync --project $(BACKEND) --frozen

# ---------- тесты и качество (без Docker) ----------

test-unit: sync ## Юнит-тесты без БД и Docker: pytest tests/unit + vitest
	bash deploy/run-tests.sh unit

test: sync ## Все тесты на локальном PostgreSQL (DATABASE_URL) + import-linter + vitest
	bash deploy/run-tests.sh full

lint: sync lint-imports ## ruff (check + format --check) по backend, app/client, tests, deploy и import-linter
	$(UV_RUN) ruff check --config $(BACKEND)/pyproject.toml $(PY_SRC)
	$(UV_RUN) ruff format --config $(BACKEND)/pyproject.toml --check $(PY_SRC)

fmt: sync ## Автоформатирование ruff (те же пути, что lint)
	$(UV_RUN) ruff check --config $(BACKEND)/pyproject.toml --fix $(PY_SRC)
	$(UV_RUN) ruff format --config $(BACKEND)/pyproject.toml $(PY_SRC)

lint-imports: sync ## Архитектурные контракты (app/backend/importlinter.ini)
	cd $(BACKEND) && uv run --frozen lint-imports --config importlinter.ini --no-logo

check: sync ## SDL: SBOM CycloneDX, лицензии, pip-audit, npm audit, Trivy, import-linter (T088)
	IMAGE=$(IMAGE) bash deploy/ci/check.sh

egress-host-check: ## Стенд QA (root): 0 пакетов наружу во время make smoke (T088)
	bash deploy/ci/egress-host-check.sh

compose-check: sync ## Статическая проверка поставки без образов: compose config --quiet + tests/deploy (3Д1(а))
	$(COMPOSE) -f deploy/docker-compose.yml --env-file deploy/.env.example config --quiet
	$(COMPOSE_TEST) config --quiet
	BM_REQUIRE_COMPOSE=1 $(UV_RUN) pytest tests/deploy -v

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
	$(COMPOSE_MAIN) up -d --build --wait --wait-timeout 180

down: ## Остановить стек (данные в томах сохраняются)
	$(need_compose)
	$(COMPOSE_MAIN) down

logs: ## Логи стека
	$(need_compose)
	$(COMPOSE_MAIN) logs -f --tail=200

smoke: sync ## Smoke против поднятого стека (BM_STACK=compose|local; BASE_URL, BM_ADMIN_PASSWORD — tests/smoke/conftest.py)
	$(STACK_ENV) BM_SMOKE=1 $(UV_RUN) pytest tests/smoke -v -ra

failover: sync ## Отказные тесты SC-002/SC-003 против поднятого стека (make up или up-local + BM_STACK=local) (T032, T093)
	$(STACK_ENV) BM_FAILOVER=1 BASE_URL=$${BASE_URL:-http://127.0.0.1:8080} $(UV_RUN) pytest tests/failover -v -ra

backup: ## Резервная копия pg_dump -Fc сейчас (compose: том backups; local: deploy/local/.state/backups)
ifeq ($(BM_STACK),local)
	$(LOCAL_STACK) backup
else
	$(need_compose)
	$(COMPOSE_MAIN) exec -T backup bash /backup/backup.sh
endif

restore: ## Восстановить последнюю копию в чистую БД (FILE=bm-....dump — конкретную); api/worker перезапускаются
ifeq ($(BM_STACK),local)
	$(LOCAL_STACK) restore $(FILE)
else
	$(need_compose)
	$(COMPOSE_MAIN) stop api worker
	$(COMPOSE_MAIN) exec -T backup bash /backup/restore.sh $(FILE)
	$(COMPOSE_MAIN) up -d --wait --wait-timeout 180 api worker
endif

# ---------- стек из локальных процессов (14_tech_rulings.md, пакет 3, 3Д1(в)) ----------

up-local: sync ## Поднять стек из локальных процессов: свой PostgreSQL (порт 55432), api (2 воркера), worker
	$(LOCAL_STACK) up

down-local: ## Остановить стек из локальных процессов (данные кластера сохраняются; удалить: deploy/local/stack.sh destroy)
	$(LOCAL_STACK) down

# ---------- Linux AppImage: запуск одним файлом без Docker (deploy/appimage) ----------

appimage: ## Собрать dist/LLM_Budget_Manager-<версия>-x86_64.AppImage (Python, PostgreSQL 16 и админка внутри)
	deploy/appimage/build.sh

macos-app: ## Собрать dist/LLM_Budget_Manager-<версия>-macos-arm64.zip (Mac-приложение для Apple Silicon, без Docker)
	deploy/macos/build.sh

# Цель от QA (T086), с пакета 5 (5Д8 (1)) — в зоне DevOps; поведение прежнее: генератор — tests/load/run_load.sh.
# Замер SC-001 — без параллельных make test и нагрузок (5Д2).
load: sync ## Нагрузка SC-001/SC-004 против поднятого стека (BM_STACK=compose|local; LOAD_RATE, LOAD_DURATION_S, VEGETA) (T086)
	$(STACK_ENV) bash tests/load/run_load.sh

e2e: ## Появится в задаче QA (e2e)
	@echo "make $@: ещё не реализовано (см. 12_tasks.md)"; exit 2

clean: ## Удалить локальные артефакты (venv, кэши, deploy/ci/out)
	rm -rf $(BACKEND)/.venv deploy/ci/out .pytest_cache .ruff_cache
