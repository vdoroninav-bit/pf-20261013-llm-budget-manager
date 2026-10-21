# 14. DevOps — отчёт

Роль: DevOps. Входы: `12_plan.md` (§5.2, §8), `12_tasks.md`, `12_arch_decision.md`, `10_constitution.md`, `12_approval_log.md` (архитектура согласована 2026-10-14).

## Статус задач

| ID | Задача | Статус | Доказательство |
|---|---|---|---|
| T001 | Каркас репозитория и команд | **Готово** в рамках среды: `make test` и `make test-unit` дают exit 0 без Docker. Docker-цели написаны, но не запускались: Docker Hub недоступен | Вывод ниже |
| T088 | SDL-проверки | **Готово (2026-10-21)**, Trivy — НЕ ПРОВЕРЕНО (только CI) | Раздел «2026-10-21» ниже |
| T087 | Полная поставка | **Проверено локально** (`[ ]` до журнала CI) | Раздел «2026-10-21» ниже |
| T092 | CI GitHub Actions | **Написан, синтаксис проверен** (`[ ]` до журнала CI) | Раздел «2026-10-21» ниже |
| T090 | Руководство администратора | **Готово к приёмке** (`[ ]` до приёмки техлидом и прогона QA) | `docs/admin-guide.md` |

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


---

## 2026-10-21 — T087, T092, T088, T090 (пакет 3, 3Д1–3Д4)

Среда: Docker 29.8.2 / Compose 5.5.1, демон запущен, все реестры отвечают 403. PostgreSQL 16.15. Node 22.22.0.

### Итог

| ID | Отметка | Статус гейтов (3Д2) |
|---|---|---|
| T087 | `[ ]`, проверено локально | статика compose — **PASS**; `up/smoke/backup/restore/smoke` — **PASS-замена** (стек из локальных процессов); сборка образа и стек в Docker — **НЕ ПРОВЕРЕНО** (реестры 403, проверка — CI T092 или чек-лист admin-guide §14) |
| T092 | `[ ]` | workflow написан, `actionlint` 0 ошибок; **прогона нет** — журнала CI нет, все его пункты НЕ ПРОВЕРЕНО |
| T088 | **`[x]`** | SBOM Python/npm, лицензии, pip-audit, npm audit, import-linter — **PASS**; Trivy — **НЕ ПРОВЕРЕНО** (нет бинарника и образа; шаг выполняется в CI) |
| T090 | `[ ]` | документ готов; ждёт приёмки техлида и прогона QA на чистой машине |

`make test` (полный, БД `bm_test_devops` на общем кластере 5432): **721 passed, 1 xfailed; frontend 28 файлов / 363 теста passed; exit 0.**

### Что сделано (файлы)

| Файл | Что |
|---|---|
| `deploy/docker-compose.yml` | `api`, `worker`, `postgres:18` (`bm`/`bm`), `backup`. Сеть `core` с `internal: true` (в ней `postgres`, `backup`), `edge` (у `api`, `worker`). Порт `"${BIND_ADDR}:${API_PORT}:8080"`. Healthcheck и `restart: unless-stopped` у всех сервисов. Секреты — compose `secrets` с источником `environment`: значения из `deploy/.env` попадают в контейнер файлами `/run/secrets/*` и читаются через `*_FILE`. Принцип наименьших прав: ключи провайдеров есть только у `worker`, пароль первого администратора — только у `api`. `DATABASE_URL` без пароля. `PROMETHEUS_MULTIPROC_DIR=/run/bm-metrics` на tmpfs (uid 10001), только у `api`. `IMPORT_HTTPS_PROXY` — только у `worker` (импорт выполняет он; `runImport` в `api` ставит задачу в очередь) |
| `deploy/Dockerfile` | Стадия `node-build` (`npm ci` + `npm run build` + check-dist, `design/tokens.json`) → `COPY dist /app/static/admin`. Добавлены entrypoint и `healthcheck.py`. `UV_EXTRAS` и `--all-extras` убраны |
| `deploy/docker-entrypoint.sh` | Снимает `*_FILE`, если файла нет. Пустой секрет compose файлом не создаёт [Факт: проверено на образе-заглушке, см. ниже]. Пароль БД из `POSTGRES_PASSWORD_FILE` передаёт в `PGPASSWORD` (asyncpg берёт его, если в DSN нет пароля [Факт: asyncpg 0.32 `connect_utils.py`]). Очищает каталог метрик при старте |
| `deploy/healthcheck.py` | `api`: `/readyz`. `worker`: PID 1 — это `bm.worker`, и `SELECT 1` к БД |
| `deploy/backup/{backup.sh,restore.sh}` | `pg_dump -Fc` во временный файл, проверка `pg_restore --list`, ротация `BACKUP_KEEP`, `--loop` (раз в сутки), `--healthcheck` (копия не старше 26 ч). Восстановление — в чистую БД: `DROP DATABASE … WITH (FORCE)` → `CREATE` → `pg_restore --exit-on-error` |
| `deploy/local/stack.sh` | Стек из локальных процессов: свой кластер PG (initdb, порт 55432, только TCP 127.0.0.1, `bm`/`bm`, данные в `deploy/local/.state/` в `.gitignore`), `api` (uvicorn, 2 воркера, порт 18080), `worker`. Pid-файлы, каждый сервис в своей сессии (pid = pgid). `stop` = сигнал остановки (у postgres SIGINT, как STOPSIGNAL официального образа [Вывод]; у api/worker SIGTERM) → до 10 с → SIGKILL группы. `kill` = SIGKILL группы. Команды `up/down/destroy/status/start/stop/kill/wait/env/backup/restore/logs`. Порт 5432 запрещён явно |
| `Makefile` | `up-local`, `down-local`, `backup`, `restore` (compose и `BM_STACK=local`), `compose-check`. `smoke` и `failover` при `BM_STACK=local` берут переменные из `stack.sh env`. `lint`/`fmt` по `app/backend app/client tests deploy` (3Б9). `--all-extras` убран. Compose вызывается через `env -u DATABASE_URL`: экспорт тестового `DATABASE_URL` из Makefile иначе подменял бы `DATABASE_URL` стека (дефект найден тестом, см. ниже). `load e2e` переставлены в `e2e load`: строка, начинающаяся с `load`, — директива GNU make |
| `deploy/.env.example` | `SESSION_COOKIE_SECURE=false` и `IMPORT_HTTPS_PROXY=` с комментариями по Б2/Б4. Описано, как передаются секреты. `DATABASE_URL` без пароля. Пары `*_ADMIN_KEY_FILE` убраны (в compose это файлы `/run/secrets`). `BM_ADMIN_PASSWORD` — только для smoke и failover |
| `app/backend/pyproject.toml`, `uv.lock` | Extra `mcp` удалён (3К): из lock ушли 10 пакетов (mcp, mcp-types, httpx2, httpcore2, httpx2-jsfetch, sse-starlette, pyjwt, cryptography, truststore, pywin32). **certifi 2026.7.22 → 2026.10.19** — см. отклонение 1 |
| `tests/smoke/{conftest.py,test_smoke.py}` | healthz, readyz, metrics; ключевой сценарий: вход (при первом входе — смена временного пароля на `BM_ADMIN_PASSWORD`) → модель, подразделение, лимит, ключ → `allow` → отчёт 100,00 ₽ → остаток уменьшился ровно на сумму отчёта. `compose_only`: SPA `/admin/`, все 4 сервиса healthy, uid 10001, каталог метрик только у `api`, порт `postgres` не опубликован. Без `BM_SMOKE=1` тесты пропускаются; с ним при отсутствии стека — падают |
| `tests/deploy/test_compose_static.py` | Статические требования 3Д1(а) по `docker compose config --format json`, `.env.example`, Dockerfile, удалению MCP. Входит в `make test`. `BM_REQUIRE_COMPOSE=1` (CI) запрещает пропуск |
| `tests/deploy/test_check_licenses.py`, `deploy/ci/check_licenses.py`, `licenses-exceptions.txt` | Исключения только вида `<тип>:<имя>@<версия>` (3Д3). Запись без версии — exit 2. На копилефт исключения не действуют. Исключения: `pypi:certifi@2026.10.19`, `unknown:budget-client@0.1.0` (собственный клиент без purl) |
| `deploy/ci/check.sh` | Окружение поставки без extras плюс `budget_client` (3Б9). Команда Trivy задаётся через `TRIVY` (в CI — контейнер). Строка лицензии `@tanstack/react-query` из npm-SBOM (3Ф3) |
| `.github/workflows/ci.yml` | Задача `test`: сервис `postgres:18`, `make lint`, `make compose-check`, `make test`, фронтенд в `node:24-slim`. Задача `delivery`: `make build`, digest базовых образов, `make up && make smoke && make backup && make restore && make smoke`, `make failover`, `CHECK_STRICT=1 make check` с Trivy в контейнере, попытка `sudo make egress-host-check` (`continue-on-error`), журналы стека. Сторонний action — только `actions/checkout@v5`. Секретов нет: пароли стенда генерируются в прогоне |
| `docs/admin-guide.md` | Установка за ≤ 30 мин, переменные, секреты, HTTPS и `SESSION_COOKIE_SECURE`, сессия 12 ч, режимы и локальный акт перед `enforce`, правила денег, импорт и ключи, раздел «Импорт через корпоративный прокси», раздел «Webhook» (формат тела, повторы, статусы, IP вместо имени), резервные копии, закрытый контур, чек-лист «Проверка поставки на своём стенде» (§14), неполадки |

### Команды готовности и вывод

**T087, статика (3Д1(а)) — PASS**
```
$ docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config --quiet   → exit 0
$ docker compose -f deploy/docker-compose.test.yml config --quiet                             → exit 0
$ make compose-check
tests/deploy/test_compose_static.py::test_core_network_is_internal PASSED
tests/deploy/test_compose_static.py::test_db_and_backup_only_in_core[postgres|backup] PASSED
tests/deploy/test_compose_static.py::test_api_port_from_bind_addr_and_api_port PASSED
tests/deploy/test_compose_static.py::test_healthcheck_and_restart[api|backup|postgres|worker] PASSED
tests/deploy/test_compose_static.py::test_secret_values_never_in_rendered_config PASSED
tests/deploy/test_compose_static.py::test_prometheus_multiproc_dir_on_tmpfs_only_at_api PASSED
tests/deploy/test_compose_static.py::test_import_https_proxy_passed_to_worker PASSED
... ============================== 48 passed in 0.85s ==============================   EXIT 0
```
Отрицательные проверки (мутации compose-файла, по одной):
```
internal: true → false                       1 failed, 36 passed
tmpfs каталога метрик убран                  1 failed, 36 passed
IMPORT_HTTPS_PROXY у worker убран            1 failed, 36 passed
restart: unless-stopped → always             4 failed, 33 passed
postgres/backup добавлены в edge             2 failed, 35 passed
DATABASE_URL тестовой БД в окружении         test_database_url_has_no_password FAILED → найден дефект (compose
                                             берёт окружение раньше --env-file); исправлено в Makefile и тесте
```

**T087, стек из локальных процессов (3Д1(в)) — PASS-замена**
```
$ make up-local && make smoke BM_STACK=local && make backup BM_STACK=local && make restore BM_STACK=local && make smoke BM_STACK=local
stack.sh: postgres: 127.0.0.1:55432 (pid 14265)
stack.sh: готов: http://127.0.0.1:18080/readyz
========================= 4 passed, 6 skipped in 1.06s =========================
2026-10-21T05:37:37Z backup: OK .../deploy/local/.state/backups/bm-20261021T053737Z.dump (74432 байт)
2026-10-21T05:37:41Z restore: БД bm пересоздана (чистая)
2026-10-21T05:37:41Z restore: OK: восстановлено в bm, таблиц в public: 29
stack.sh: готов: http://127.0.0.1:18080/readyz
========================= 4 passed, 6 skipped in 1.05s =========================
CHAIN_EXIT=0
```
6 skipped — проверки `compose_only` (SPA из образа, healthy, uid ×2, каталог метрик, порт postgres), причина печатается в отчёте pytest.
Восстановление действительно возвращает состояние копии: после копии у 3 моделей `active=false` → после `make restore` `inactive=0`, `models=3`.
Сигналы (для драйвера T093):
```
api pgid 13316: 4 процессов → stack.sh kill api → 0 процессов; status: api stopped
stack.sh stop api       → 2,6 с, 0 процессов группы
stack.sh stop postgres  → 2,2 с; /readyz → {"status":"fail","db":"fail"}; start postgres → /readyz ok (api пережил перезапуск БД)
```
Чем замена отличается от compose: нет образа (SPA, uid 10001, HEALTHCHECK), нет сетей compose, PostgreSQL 16 вместо 18, процессы запущены от root (postgres — от пользователя postgres). Общий кластер 5432 не останавливался.

**T087, Docker — НЕ ПРОВЕРЕНО**
```
$ docker build --check -f deploy/Dockerfile .
ERROR: node:24-slim: failed to resolve source metadata ... registry-1.docker.io ...: Forbidden
```
Что удалось проверить без реестра:
- `docker build --check` с базовыми образами, подменёнными локальной заглушкой (`--build-arg PYTHON_IMAGE=… NODE_IMAGE=…`): `Check complete, no warnings found.` — синтаксис и линт Dockerfile в порядке, сборка не выполнялась;
- образ-заглушка (`docker import` из bash и libc хоста) и compose-файл с теми же конструкциями:
```
uid=10001 gid=10001
drwx------ 2 10001 10001 /run/bm-metrics           ← tmpfs с uid/gid работает
-r--r--r-- 1 0 0 6 s   S=[abc123]                  ← secrets.environment берёт значение из --env-file, файл 0444
E=[cat: /run/secrets/e: No such file or directory]  ← пустая переменная → файл не создаётся (обработано в entrypoint)
tmpfs-writable
```
- entrypoint на хосте: `*_FILE` без файла снимаются, `PGPASSWORD` выставлен, каталог метрик очищен; `healthcheck.py` (`_db_ok`) с `POSTGRES_PASSWORD_FILE` → `True`.

**T088 — `CHECK_STRICT=1 make check` на текущем дереве (с npm-веткой по `app/frontend`)**
```
=== Python: лицензии ===
deploy/ci/out/sbom-python.cdx.json: компонентов 28, нарушений 0
Исключения (deploy/ci/licenses-exceptions.txt): pypi:certifi@2026.10.19, unknown:budget-client@0.1.0
=== Python: уязвимости (pip-audit) ===
No known vulnerabilities found / pip-audit: пакетов 27, уязвимостей 0
deploy/ci/out/sbom-npm.cdx.json: компонентов 138, нарушений 0
npm SBOM: @tanstack/react-query@5.104.2 — MIT
7 moderate severity vulnerabilities  (decode-uri-component ← query-string ← ra-core; порог — critical)
=== Итог make check ===
OK    python: uv sync --frozen (lock-файл согласован)
OK    python: budget_client в окружении поставки (для SBOM)
OK    python: SBOM deploy/ci/out/sbom-python.cdx.json
OK    python: лицензии по белому списку
OK    python: pip-audit — известных уязвимостей нет
OK    python: import-linter
OK    npm: нет @react-admin/ra-*
OK    npm: SBOM deploy/ci/out/sbom-npm.cdx.json
OK    npm: лицензии по белому списку
OK    npm: audit — критичных уязвимостей нет
FAIL  trivy: не установлен (в CI — контейнер Trivy, T092) (пропуск запрещён: CHECK_STRICT=1)
make check: FAIL                                                     STRICT_EXIT=2
$ make check   →  SKIP trivy … / make check: OK (SBOM и отчёты — deploy/ci/out/)   EXIT 0
```
В строгом режиме единственный FAIL — Trivy: здесь он НЕ ПРОВЕРЕНО, по 3Т это допускается. SBOM лежат в `deploy/ci/out/`. В SBOM есть `budget-client`, `mcp*` нет (0 совпадений). Ветка Trivy проверена заглушкой команды (`TRIVY=<скрипт>`, `IMAGE=<образ-заглушка>`): при 0 critical — `OK … make check: OK`, exit 0; при critical — `FAIL trivy: есть critical`, exit 1. Сам Trivy при этом не запускался.
Проверки лицензий (`tests/deploy/test_check_licenses.py`, 11 passed): MIT-0 разрешён; MPL-2.0 без исключения — FAIL; исключение `certifi@<версия из uv.lock>` действует только на эту версию; запись без версии — exit 2; GPL/AGPL/LGPL/SSPL — FAIL даже при записи в исключениях (мутация «исключение без проверки копилефта» → 4 failed); `@react-admin/ra-*` — ENTERPRISE.

**T092 — синтаксис workflow (actionlint есть: `actionlint-py` 1.7.12 с PyPI, вместе с shellcheck 0.11.0)**
```
$ actionlint -verbose .github/workflows/ci.yml
verbose: Found 0 parse errors in 0 ms for .github/workflows/ci.yml
verbose: Found total 0 errors in 329 ms for .github/workflows/ci.yml        rc=0
$ actionlint ci-bad.yml   (runs-on → runs_on, незакрытая кавычка в run:)
… "runs-on" section is missing in job "test" [syntax-check]
… shellcheck reported issue in this script: SC1072:error … [shellcheck]
$ shellcheck -S warning deploy/local/stack.sh deploy/backup/*.sh deploy/docker-entrypoint.sh deploy/ci/*.sh deploy/run-tests.sh   rc=0
```
Прогона CI нет: репозиторий из сессии недоступен (API GitHub — 403). Журнала нет, поэтому все пункты CI — **НЕ ПРОВЕРЕНО**.

**Полный `make test` и `make lint`**
```
$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test_devops make test
БД доступна: PostgreSQL 16.15 …
================== 721 passed, 1 xfailed in 213.75s (0:03:33) ==================
Contracts: 3 kept, 0 broken.
 Test Files  28 passed (28)
      Tests  363 passed (363)
run-tests.sh full: OK                                                EXIT=0
$ make lint   → Contracts: 3 kept, 0 broken. / All checks passed! / 161 files already formatted   EXIT 0
```
1 xfail — `test_6_2_csv_injection_is_neutralized` (QA, 3Б5, T093). Прогон шёл на отдельной БД `bm_test_devops`, которую я создал на общем кластере (сам кластер не останавливался), чтобы не мешать параллельным прогонам на `bm_test`. Первый прогон завис на `test_us4_subscriptions` во время перезапуска общего кластера и был снят. Повтор — зелёный.

### Отклонения

1. **certifi поднят до 2026.10.19 (транзитивная зависимость, только `uv.lock`).** `pip-audit` нашёл в 2026.7.22 уязвимость PYSEC-2026-4320 / GHSA-6q48-6c7w-9gw2 (скомпрометированные промежуточные CA emSign, возможен MITM). Под удар попадает TLS импорта к провайдерам. Без обновления `make check` падает. Лицензия та же, MPL-2.0. Узкое исключение (3Д3) перенесено на новую версию. **Нужно подтверждение техлида** (вопрос 1).
2. **Артефакты CI без `actions/upload-artifact`.** По 3Д4 разрешён только checkout. Сводка `make check` и SBOM (gzip+base64, с sha256) печатаются в журнал прогона, а SBOM восстанавливается из журнала. Если техлид разрешит официальный `actions/upload-artifact`, это одна строка (вопрос 3).
3. **Версии в CI не проверены** [Гипотеза]: `actions/checkout@v5`, `aquasec/trivy:0.58.0`, `node:24-slim`. Реестры и API GitHub закрыты. Digest базовых образов печатает шаг CI, после первого прогона их нужно закрепить в Dockerfile.
4. **`make smoke` при первом входе меняет временный пароль** на `BM_ADMIN_PASSWORD`. Без этого ключевой сценарий smoke на чистой установке не пройти: API требует смены пароля. Это описано в admin-guide и в `.env.example`.
5. **Контейнер `backup` работает от root.** Том `/backups` создаётся с владельцем root. Контейнер только в `core` (internal), портов нет. Переход на `gosu postgres` — после проверки в CI.
6. **`IMPORT_HTTPS_PROXY` передаётся через окружение `worker`**, а логин и пароль прокси в URL видны в `docker inspect`. Варианта `*_FILE` для этой переменной в `Settings` нет (вопрос 4).

### Нужны правки в коде (не моя зона)

1. **Backend: раздача SPA.** Образ кладёт `dist` в `/app/static/admin`, но `bm.main` статику не отдаёт: `GET /admin/` на локальном стеке → **404** (проверено). `compose_only`-тест `test_admin_spa_served_from_image` в CI упадёт, и это правильно: гейт не должен быть зелёным без админки. Предложение: `StaticFiles(directory="/app/static/admin", html=True)` на `/admin`, если каталог есть. Путь можно вынести в константу или переменную; в compose это всегда `/app/static/admin`.
2. **Backend (T062): `IMPORT_HTTPS_PROXY`** в `Settings` и `bm.egress` пока не реализован: переменная есть только в списке маскирования журналов. Поставка её уже передаёт `worker`.

### Вопросы техлиду

1. Подтвердить обновление certifi до 2026.10.19 и перенос узкого исключения на эту версию (отклонение 1).
2. T087 и T092 остаются `[ ]`, пока нет журнала CI. Кто и когда запускает CI на репозитории проекта (нужен push `.github/workflows/ci.yml` и раннер с доступом к Docker Hub)? До этого в `16_release.md` уходят как НЕ ПРОВЕРЕНО: сборка образа, стек в Docker, Trivy, `egress-host-check`, PG 18, SC-009.
3. Разрешить ли `actions/upload-artifact` для SBOM (отклонение 2)?
4. Нужен ли `IMPORT_HTTPS_PROXY_FILE` (backend, `Settings`), чтобы учётные данные прокси не лежали в окружении контейнера?
5. Можно заранее, до CI, проверить compose-стек целиком на образах, собранных из файлов хоста (`docker import`; так уже проверены secrets и tmpfs). Статус будет PASS-замена, не PASS, работы примерно на 0,5 дня. Делать?
