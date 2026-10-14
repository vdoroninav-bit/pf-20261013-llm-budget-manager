# 14. DevOps — отчёт

Роль: DevOps. Входы: `12_plan.md` (§5.2, §8), `12_tasks.md`, `12_arch_decision.md`, `10_constitution.md`, `12_approval_log.md` (архитектура согласована 2026-10-14).

## Статус задач

| ID | Задача | Статус | Доказательство |
|---|---|---|---|
| T001 | Каркас репозитория и команд | **Готово** в рамках среды: `make test` и `make test-unit` дают exit 0 без Docker. Docker-цели написаны, но не запускались: Docker Hub недоступен | Вывод ниже |
| T088 | SDL-проверки | **Частично**. Python-часть (SBOM, лицензии, pip-audit, import-linter) работает, `make check` дал exit 0. npm-ветка проверена на пробном проекте вне репозитория. Trivy не проверен, `egress-host-check` не запускался | Вывод ниже |

Оговорка: T088 зависит только от T001, поэтому сделан сразу. npm-часть заработает на реальном фронтенде после T002, Trivy — после T087 (образ) и установки trivy.

---

## T001

### Что сделано (файлы)

| Файл | Назначение |
|---|---|
| `Makefile` | Единая точка входа. Цели: `help`, `sync`, `test-unit`, `test`, `lint`, `fmt`, `lint-imports`, `check`, `egress-host-check`, `test-docker`, `build`, `up`, `down`, `logs`, `smoke`, `clean`. Цели `backup`, `restore`, `load`, `failover`, `e2e` пока завершаются с exit 2 и сообщением «ещё не реализовано» (T086, T087, QA) |
| `deploy/run-tests.sh` | Общий прогон тестов локально и в контейнере. Режим `unit`: pytest `tests/unit` + vitest. Режим `full`: проверка БД, pytest (`unit`, `contract`, `integration`, `acceptance`, `security`), import-linter, vitest |
| `app/backend/pyproject.toml`, `app/backend/uv.lock` | Пакет `bm`, Python `>=3.13,<3.14`. Прямые зависимости закреплены точно, extra `mcp`, группа `dev`, настройки ruff. В lock-файле 69 пакетов |
| `app/backend/importlinter.ini` | 3 контракта: httpx импортирует только `bm.egress` (принцип IV); `bm.domain` чистый (без БД, сети, фреймворков и других модулей `bm`); ядро не импортирует адаптеры `http`, `mcp`, `worker` |
| `app/backend/bm/__init__.py` + `__init__.py` подпакетов из `12_plan.md` §5.2 | Скелет пакетов. Он нужен import-linter: контракт по несуществующему модулю падает с ошибкой |
| `deploy/Dockerfile` | Стадии `python-base` (uv 0.11.32 из PyPI), `python-deps` (`uv sync --frozen --no-dev`), **`python-runtime`** (без uv, пользователь `bm` uid 10001, `HEALTHCHECK` на `/healthz`, `uvicorn bm.main:create_app --factory`), `tests` (для `make test-docker`) |
| `deploy/docker-compose.test.yml` | Сервисы `postgres-test` (`postgres:18`, tmpfs, healthcheck, `fsync=off`) и `tests` (`depends_on: service_healthy`). Сеть `internal: true` |
| `deploy/.env.example` | Все 25 переменных из `12_plan.md` §8 с комментариями. Секреты пустые: без них сервис не стартует |
| `tests/conftest.py` | Пустой, с описанием фикстур, которые добавит T014 |
| `tests/{unit,contract,integration,acceptance,security,smoke}/.gitkeep` | Каталоги, которые читает `make test` |
| `pytest.ini` (корень) | `testpaths=tests`, `pythonpath=.`, `--import-mode=importlib`, `asyncio_mode=auto`, `--strict-markers`, маркер `db` |
| `.gitignore`, `.dockerignore` | `.env`, `node_modules`, `__pycache__`, `.venv`, кэши, `deploy/ci/out/`. `.dockerignore` пропускает в контекст сборки только нужное (белый список) |

### Как запустить

```bash
make test-unit                      # без БД и Docker
make test                           # локальный PostgreSQL; по умолчанию DATABASE_URL=postgresql://postgres@127.0.0.1:5433/bm_test
make test DATABASE_URL=postgresql://user@host:5432/my_test_db
make lint                           # ruff + import-linter
make test-docker                    # то же в контейнере против чистого postgres:18 (нужен доступ к Docker Hub или зеркалу)
```

Защита от ошибки: `make test` отказывается работать, если в имени БД нет `test` (exit 2). Фикстура T014 будет пересоздавать схему, и случайный прогон по рабочей базе её бы стёр. Обойти можно явно: `ALLOW_ANY_TEST_DB=1`.

### Проверка (вывод команд)

`make test-unit`:
```
uv sync --project app/backend --frozen --all-extras
Checked 66 packages in 0.98ms
bash deploy/run-tests.sh unit
=== pytest tests/unit ===
platform linux -- Python 3.13.16, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/claude/product_farm/projects/pf-20261013-llm-budget-manager
configfile: pytest.ini
plugins: asyncio-1.4.0, anyio-4.15.1
collected 0 items
============================ no tests ran in 0.01s =============================
pytest: тесты не найдены (exit 5) — для каркаса это допустимо, считаем успехом
=== frontend ===
vitest: нет app/frontend/package.json (фронтенд появится в T002) — пропуск
run-tests.sh unit: OK
EXIT make test-unit = 0
```

`make test` (локальный PostgreSQL 16.15, порт 5433):
```
=== БД ===
БД доступна: PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
ВНИМАНИЕ: целевая версия PostgreSQL 18 (12_plan.md), тестовая — 16. Не используйте возможности только PG 18, пока стенд не обновлён.
=== pytest ===
collected 0 items
============================ no tests ran in 0.02s =============================
pytest: тесты не найдены (exit 5) — для каркаса это допустимо, считаем успехом
=== import-linter ===
httpx импортирует только bm.egress KEPT
bm.domain не зависит от ввода-вывода и других модулей bm KEPT
ядро не импортирует адаптеры (http, mcp, worker) KEPT
Contracts: 3 kept, 0 broken.
=== frontend ===
vitest: нет app/frontend/package.json (фронтенд появится в T002) — пропуск
run-tests.sh full: OK
EXIT make test = 0
```

Отрицательные проверки: цели должны падать, когда есть проблема.
```
--- имя БД без test (DATABASE_URL=.../postgres)
ОТКАЗ: имя БД 'postgres' не содержит 'test'. Тесты пересоздают схему; укажите тестовую БД.   exit=2
--- БД недоступна (порт 5999)
БД недоступна: ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 5999)      exit=2
--- временный падающий тест tests/unit/test_zz_red.py (assert 1 == 2), make test-unit
1 failed in 0.03s                                                                                exit=2
--- тот же файл с assert 1 == 1, make test
БД доступна: PostgreSQL 16.15 ... / 1 passed in 0.03s / run-tests.sh full: OK                    exit=0
--- import-linter: временный `import httpx` в bm/http/__init__.py
httpx импортирует только bm.egress BROKEN / Contracts: 2 kept, 1 broken.                         exit=1
```
Временные файлы удалены после проверки.

`make lint`:
```
Contracts: 3 kept, 0 broken.
ruff check ... All checks passed!
ruff format --check ... 18 files already formatted
EXIT make lint = 0
```

Docker-цели в этой среде не проверены: pull с Docker Hub запрещён.
```
$ docker compose -f deploy/docker-compose.test.yml config --quiet      → exit=0 (файл валиден)
$ make build
ERROR: failed to build: failed to solve: python:3.13-slim: failed to resolve source metadata for docker.io/library/python:3.13-slim: ... Forbidden
$ make test-docker
Error response from daemon: failed to resolve reference "docker.io/library/postgres:18": ... Forbidden
```

### Отклонения от плана и почему

1. **`make test` работает без Docker.** По §8 плана `make test` — это `docker compose ... run --rm tests`. Здесь Docker Hub недоступен, поэтому `make test` идёт на локальном PostgreSQL через `DATABASE_URL`, а прогон в контейнере вынесен в `make test-docker`. Оба вызывают один и тот же `deploy/run-tests.sh full`, так что набор проверок совпадает. Если техлид захочет, чтобы `make test` по умолчанию шёл в Docker, — это перестановка двух целей.
2. **PostgreSQL 16 вместо 18 на локальном стенде.** `make test` печатает предупреждение. Совместимость сохраняем: в DDL T014 не использовать то, что есть только в PG 18 (например, `uuidv7()` — [Вывод: функция появилась в PG 18], генерировать UUID v7 в приложении). В `docker-compose.test.yml` и CI — `postgres:18`.
3. **openapi-spec-validator: 0.8.5 вместо 0.9.0.** openapi-core 0.23.1 требует `openapi-spec-validator>=0.8.0,<0.9.0` [Факт: метаданные PyPI]. В pyproject стоит диапазон `>=0.8,<0.9`, точную версию фиксирует uv.lock. **Для T015:** у openapi-core 0.23.1 extra `fastapi` требует `fastapi<0.140` [Факт: метаданные PyPI], а мы закрепили 0.143.0. Значит, интеграцию openapi-core с FastAPI не используем: проверяем ответ через базовый API openapi-core, хелпер `assert_matches_contract` пишем сами.
4. **Exit 5 у pytest («тестов нет») считается успехом** в `run-tests.sh`. Иначе T001 не выполнить: «0 тестов и exit 0». Как только появятся тесты, это правило перестанет срабатывать.
5. **Добавлены файлы вне списка T001:** `pytest.ini` в корне (тесты лежат в корневом `tests/`, а venv — в `app/backend`), `.dockerignore`, `deploy/run-tests.sh`, скелет `__init__.py` подпакетов `bm` (нужен import-linter).
6. **Контракт egress — типа `forbidden`, а не `protected`.** `protected` падает, пока httpx никто не импортирует. Модули верхнего уровня `bm.main` и `bm.settings` в контракт не внесены, потому что их ещё нет. **Для T015:** добавить их в `source_modules` контракта `egress-only`.
7. **Образы закреплены по тегу (`python:3.13-slim`, `postgres:18`), а не по digest.** Получить digest без доступа к реестру нельзя. Перенос на `@sha256:` — в T087. Версии с полным patch-уровнем (`3.13.16-slim`, `18.6`) не проверены: Docker Hub API недоступен.
8. **Директива `# syntax=docker/dockerfile:1` удалена.** Она тянет frontend BuildKit с Docker Hub и в закрытом контуре сломала бы сборку.

### Версии (проверены по PyPI 2026-10-14)

[Факт] fastapi 0.143.0 (MIT), asyncpg 0.32.0 (Apache-2.0), mcp 2.3.0 (MIT), uvicorn 0.54.0, pydantic 2.14.1, pydantic-settings 2.15.0, httpx 0.28.1, argon2-cffi 25.1.0, prometheus-client 0.26.0, tzdata 2026.5. Dev: pytest 9.1.1, pytest-asyncio 1.4.0, openapi-core 0.23.1, openapi-spec-validator 0.8.5, import-linter 2.15, ruff 0.17.0. Инструменты: uv 0.11.32, Python 3.13.16, Node 22.22.0 / npm 10.9.4 в среде. По плану нужен Node 24 LTS: зафиксировать в T002 и T087.

---

## T088

### Что сделано

| Файл | Назначение |
|---|---|
| `deploy/ci/check.sh` | `make check`, шаги по порядку. (1) Окружение поставки: `uv sync --frozen --no-dev --all-extras`. (2) SBOM CycloneDX для Python (`cyclonedx-bom` 7.5.0). (3) Лицензии. (4) pip-audit 2.10.1 по экспортированному из uv.lock `requirements` с хэшами. (5) import-linter. (6) npm: запрет `@react-admin/ra-*` в lock-файле, SBOM (`@cyclonedx/cyclonedx-npm` 6.0.1), лицензии, `npm audit --omit=dev --audit-level=critical`. (7) Trivy: SBOM образа и `--severity CRITICAL --exit-code 1`. Сводка — `deploy/ci/out/summary.txt` |
| `deploy/ci/check_licenses.py` | Проверка лицензий по SBOM CycloneDX (Python и npm), только stdlib. Разбирает SPDX-выражения (OR, AND, WITH). Категории нарушений: COPYLEFT (GPL, AGPL, LGPL, SSPL), ENTERPRISE (`@react-admin/ra-*`, исключения на них не действуют), UNKNOWN, NOT_ALLOWED |
| `deploy/ci/licenses-allow.txt` | Список из T088 и дополнительно **MIT-0** (у `cffi`). Подтвердить у юриста |
| `deploy/ci/licenses-exceptions.txt` | **`pypi:certifi` (MPL-2.0)**: транзитивная зависимость httpx. Требует решения техлида и юриста, см. «Открытые вопросы» |
| `deploy/ci/vuln-ignore.txt` | Принятые уязвимости для pip-audit. Пока пусто |
| `deploy/ci/egress-host-check.sh` | `make egress-host-check`. Работает так: цепочка `BM_EGRESS` из `DOCKER-USER` для IP контейнеров compose-проекта; частные сети пропускаются; счётчик пакетов во внешние адреса считается во время `make smoke`; ожидается 0; правила снимаются через `trap` |

Режим `CHECK_STRICT=1` (для CI и релиза): любой пропущенный шаг считается ошибкой.

### Проверка (вывод команд)

`make check`:
```
=== Python: лицензии ===
deploy/ci/out/sbom-python.cdx.json: компонентов 40, нарушений 0
Исключения (deploy/ci/licenses-exceptions.txt): pypi:certifi
ЛИЦЕНЗИИ: OK
=== Python: уязвимости (pip-audit) ===
No known vulnerabilities found
pip-audit: пакетов 40, уязвимостей 0
...
=== Итог make check ===
OK    python: uv sync --frozen (lock-файл согласован)
OK    python: SBOM deploy/ci/out/sbom-python.cdx.json
OK    python: лицензии по белому списку
OK    python: pip-audit — известных уязвимостей нет
OK    python: import-linter
SKIP  npm: нет app/frontend/package.json (появится в T002)
SKIP  trivy: не установлен
make check: OK (SBOM и отчёты — deploy/ci/out/)
EXIT make check = 0
```

Отрицательные проверки:
```
--- без файла исключений
  NOT_ALLOWED  pypi:certifi@2026.7.22  [MPL-2.0]
ЛИЦЕНЗИИ: FAIL (1 нарушений)                                   exit=1
--- синтетический SBOM (GPL-3.0-only; MIT OR GPL-2.0-only; MIT AND LGPL-2.1-only; @react-admin/ra-rbac; без лицензии; классификатор MIT; SSPL-1.0)
  COPYLEFT     pypi:a@1  [GPL-3.0-only]
  COPYLEFT     pypi:c@1  [MIT AND LGPL-2.1-only]
  ENTERPRISE   npm:@react-admin/ra-rbac@5  [SEE LICENSE IN LICENSE.md]
  UNKNOWN      npm:d@1  [-]
  COPYLEFT     npm:f@1  [SSPL-1.0]
ЛИЦЕНЗИИ: FAIL (5 нарушений)                                   exit=1
  («MIT OR GPL-2.0-only» и классификатор MIT пропущены — так и задумано)
--- CHECK_STRICT=1 make check
FAIL  npm: нет app/frontend/package.json (появится в T002) (пропуск запрещён: CHECK_STRICT=1)
FAIL  trivy: не установлен (пропуск запрещён: CHECK_STRICT=1)
make check: FAIL                                               exit=1
```

npm-ветка проверена на пробном проекте вне репозитория (`FRONTEND_DIR=<scratch>`, зависимость `ms@2.1.3`):
```
OK    npm: нет @react-admin/ra-*
OK    npm: SBOM deploy/ci/out/sbom-npm.cdx.json
OK    npm: лицензии по белому списку
OK    npm: audit — критичных уязвимостей нет
make check: OK                                                 exit=0
```
Артефакты пробного прогона из `deploy/ci/out/` удалены.

### Что не проверено и почему

- **Trivy.** Бинарника нет в среде, а релизы на GitHub из сессии недоступны. Образа тоже нет: Docker Hub закрыт. Код шага написан, но не запускался.
- **`egress-host-check`.** Нужны root, iptables, поднятый стек (T087) и `make smoke`. Не запускался.
- **В закрытом контуре** `uvx` и `npx` берут инструменты из локального кэша. Кэш нужно прогреть заранее или поставить инструменты в образ сборки — это сделать в T087 вместе с офлайн-поставкой.
- **Покрытие SDL (ГОСТ Р 56939 / реестр).** Закрыто: SBOM Python, SCA (pip-audit), лицензионный контроль, архитектурные контракты, воспроизводимость по lock-файлам. Не закрыто: SBOM npm и образа (после T002 и T087), Trivy, статический анализ безопасности кода (сейчас только правила `S` (bandit) в ruff), поиск секретов (gitleaks), CI-пайплайн `.github/workflows/ci.yml`.

## Открытые вопросы для техлида

1. **certifi (MPL-2.0)** приходит через httpx. Варианты: (а) принять исключение — файловый копилефт, пакет не модифицируется; MPL-2.0 есть в белом списке `engineering-standards`, но не в списке T088; (б) убрать certifi из образа и использовать системное хранилище сертификатов — это решение backend по `bm.egress`. Сейчас действует (а), до решения техлида и юриста.
2. **MIT-0** добавлен в белый список (`cffi`). Нужно подтверждение.
3. **CI (`.github/workflows/ci.yml`)** в T001 и T088 не входит, и ни одной задачи с ним в `12_tasks.md` нет. Версии GitHub Actions проверить из сессии не удалось: API GitHub закрыт. Предлагаю отдельную задачу для DevOps: `make lint`, `make test` (service `postgres:18`), `CHECK_STRICT=1 make check`, gitleaks, SBOM как артефакт.
4. **Для T015:** добавить `bm.main` и `bm.settings` в контракт `egress-only`; не использовать extra `openapi-core[fastapi]` (несовместим с fastapi 0.143.0).
5. **Для T014:** DDL должен работать на PostgreSQL 16 (локальный стенд) и на 18 (поставка), либо локальный стенд нужно обновить.
