# Implementation Plan: LLM Budget Manager v1

**Ветка**: `001-llm-budget-utility` | **Дата**: 2026-10-13 | **Спецификация**: `10_tech_spec.md`
**Вход**: `12_arch_decision.md` (гибрид на базе classic), `12_adr/`, `12_openapi.yaml`, `10_constitution.md`.
**Статус**: ждёт согласия заказчика. До записи «согласен» в `12_arch_decision.md` → «Согласование» задачи из `12_tasks.md` не стартуют.

> **Для агентов-исполнителей.** Читайте этот план вместе с `10_tech_spec.md` и `12_openapi.yaml`. Каждая задача из `12_tasks.md` проходит цикл TDD: сначала тест, он падает, потом реализация, тест проходит, коммит. Работа сделана, когда приложен вывод `make test` (принцип VII).

**Цель.** Внутренняя утилита: решения allow/deny/redirect до вызова модели, учёт факта, подписок и импорта Anthropic/OpenAI в бюджетах подразделений и сотрудников в ₽.

**Архитектура.** Модульный монолит: один Python-образ, процессы `api` и `worker`, PostgreSQL 18 — единственный источник истины. Деньги считает один глубокий модуль `bm.budget` поверх чистых функций `bm.domain`. HTTP, MCP и worker — тонкие адаптеры.

**Стек.** Python 3.13, FastAPI 0.143, asyncpg 0.32, mcp 2.3, PostgreSQL 18.6, react-admin 5 + Vite + TypeScript, Docker compose v2.

---

## 1. Summary

Основное требование — сценарии 1–3 (P1): решение, отчёт о расходе, настройка. Затем сценарии 4–6 (P2): подписки, импорт, отчёты, роли и уведомления. Сценарий 7 (P3, MCP) делается, если на контрольной точке недели 5 факт не хуже плана. IdP в v1 не делаем.

Подход: детерминированное ядро без ML. Конкуренция решается блокировками строк в PostgreSQL, суммы хранятся в целых копейках, расход ведётся в суточном реестре, подписки амортизируются накопительно (ADR-001…007).

## 2. Technical Context

| Параметр | Значение |
|---|---|
| **Language/Version** | Python 3.13 (образ `python:3.13-slim`); TypeScript 5 + Node 24 LTS только для сборки фронтенда [Вывод: актуальная LTS-линия на 10.2026, зафиксировать в T001] |
| **Primary Dependencies** | FastAPI 0.143.0 (MIT) [Факт]; mcp 2.3.0 (MIT) [Факт]; asyncpg 0.32.0 (Apache-2.0) [Факт]; Pydantic v2, uvicorn, httpx, argon2-cffi, prometheus-client, tzdata — версии фиксируются lock-файлом `uv.lock` в T001, лицензии проверяет SCA; react-admin 5.x (MIT, без пакетов `@react-admin/ra-*` из EE), React, MUI core, Vite, openapi-typescript — версии в `package-lock.json` |
| **Storage** | PostgreSQL 18.6 (`postgres:18`), партиции `call_record` по месяцам |
| **Testing** | pytest + pytest-asyncio (backend), httpx ASGI-клиент для приёмочных тестов, openapi-core для проверки ответов по `12_openapi.yaml`, import-linter, vitest (frontend), vegeta (нагрузка, MIT) |
| **Target Platform** | Linux x86-64, Docker Engine + compose v2, одна машина |
| **Project Type** | web-service + SPA + клиентская библиотека |
| **Performance Goals** | SC-001: p95 ≤ 10 мс и p99 ≤ 25 мс внутри; p95 ≤ 20 мс у клиента при 50 rps |
| **Constraints** | Нет исходящих соединений при `IMPORT_ENABLED=false`; нет содержимого запросов; ≤ 2 млн ₽ и ≤ 4 чел.-мес |
| **Scale/Scope** | 1 000 сотрудников, 20 подразделений, 30 моделей, 50 rps [Гипотеза спецификации] |
| **Эталонный стенд** | 4 vCPU x86-64, 8 ГБ RAM, NVMe ≥ 200 ГБ; `api` с 2 воркерами uvicorn, `shared_buffers=1GB` |

## 3. Global Constraints

Действуют для каждой задачи. Значения взяты из спецификации и конституции.

- Содержимое запросов и ответов не принимается и не хранится. Во всех схемах `extra="forbid"`; сотрудник в событиях хранится как псевдоним `emp-<10 hex>` (принцип I).
- Ключи `ANTHROPIC_ADMIN_KEY` и `OPENAI_ADMIN_KEY` задаются только в окружении или через `*_FILE`. Их читает только `worker`; в логах, БД и ответах их нет (принцип II).
- HTTP-клиент разрешён только в `bm.egress`. При `IMPORT_ENABLED=false` (это значение по умолчанию) исходящих соединений нет (принцип IV).
- Лицензии — только MIT, Apache-2.0, BSD-2/3, ISC, PostgreSQL, PSF. GPL, AGPL, SSPL, LGPL ломают `make check` (принцип V).
- Деньги — `int` копеек, вычисления в `Decimal`, на каждое событие одно округление `ROUND_HALF_UP`, `float` запрещён (принцип VI).
- Все периоды считаются по `Europe/Moscow`: сутки с 00:00, неделя с понедельника, месяц с 1-го числа (FR-014).
- Тексты интерфейса, `message` и `detail` — на русском; коды — на английском и одинаковые для HTTP и MCP (принцип IX).
- Таймаут клиента — 50 мс, по умолчанию fail-open (принцип III). Срок резерва — `RESERVATION_TTL_MIN=15`.
- Пороги уведомлений — 80% и 100%, по одному на (подразделение, период, порог). Порог подписок по умолчанию — 50%.
- Ретенция: детальные данные 12 мес., агрегаты 36 мес.

## 4. Constitution Check

| Принцип | Как выполняется | Чем проверяется | Статус |
|---|---|---|---|
| I. Только метаданные | `extra="forbid"`, в схеме БД нет текстовых полей для содержимого, псевдонимы, логин видит только администратор | `tests/security/test_no_content.py` (схема БД + выборка логов) | PASS |
| II. Не вызывает модели | Прокси нет. Ключи провайдеров есть только в окружении `worker` | `tests/security/test_secrets_not_leaked.py` | PASS |
| III. Fail-open | `budget_client` с таймаутом 50 мс, буфер SQLite; импорт изолирован в `worker` | `make failover` (SC-002, SC-003) — гейт релиза | PASS |
| IV. Без интернета | Модуль `egress`, import-linter, подмена `socket` в тестах, счётчик `DOCKER-USER` | `tests/security/test_no_egress.py`, `make egress-host-check` | PASS |
| V. Лицензии | Белый список, SBOM CycloneDX, pip-audit, npm audit, Trivy | `make check` | PASS |
| VI. Деньги точно и один раз | Копейки, реестр, идемпотентность по `decision_id`, `(caller_id, report_id)` и `(provider, source, model, day)`, один `mode` на источник | `tests/unit/test_money.py`, `test_amortize.py`, `tests/acceptance/test_us2_*`, SC-007 | PASS |
| VII. Сначала тест | Приёмочные тесты P1 — отдельные задачи, которые предшествуют реализации (`12_tasks.md`) | Порядок задач, падение тестов до реализации | PASS |
| VIII. Простота и рамки | 4 контейнера, без Redis, брокера и ML. Объём C, порядок урезания: MCP → webhook → OpenAI | Контрольная точка недели 5 | **Внимание**: 4,09 чел.-мес против 4,0 — решает заказчик (APPROVAL, вопрос 1) |
| IX. Русский язык | `ru.ts` для react-admin, `message` и `detail` на русском, МСК | `tests/acceptance/test_messages_ru.py` | PASS |

## 5. Project Structure

### 5.1. Документация (корень репозитория проекта)

```text
10_tech_spec.md  10_constitution.md  12_arch_decision.md  12_adr/  12_openapi.yaml  12_plan.md  12_tasks.md
docs/admin-guide.md        # T090: установка, бэкап/восстановление, правила денег, сутки импорта (RU)
docs/integration-guide.md  # T090: протокол Decision API, эталонная схема fail-open (RU)
```

### 5.2. Исходный код

```text
Makefile                         # up, down, test, test-unit, smoke, load, failover, check, backup, restore, e2e
app/
├── backend/
│   ├── pyproject.toml  uv.lock  importlinter.ini
│   └── bm/
│       ├── main.py              # create_app(settings) -> FastAPI; монтирует /api/v1, /mcp, /admin, /metrics
│       ├── settings.py          # Settings (pydantic-settings), все ENV из .env.example
│       ├── db/
│       │   ├── migrate.py       # раннер *.sql по порядку, таблица schema_migrations
│       │   └── migrations/0001_init.sql …
│       ├── domain/              # ЧИСТЫЕ функции, без I/O
│       │   ├── money.py  periods.py  amortize.py  select.py  codes.py  types.py
│       ├── budget/              # глубокий модуль денег
│       │   ├── service.py       # class Budget
│       │   ├── repo.py          # SQL горячего пути
│       │   └── snapshot.py      # ConfigSnapshot в памяти + config_version
│       ├── config/service.py    # apply(change, actor) + аудит, CRUD справочников
│       ├── org_import/service.py
│       ├── provider_import/
│       │   ├── pipeline.py      # run(provider, day_from, day_to)
│       │   └── sources/{base.py, anthropic.py, openai.py, recorded.py}
│       ├── notify/service.py  notify/channels.py
│       ├── access/{authorize.py, permissions.py, auth.py, audit.py}
│       ├── reports/service.py
│       ├── egress/client.py     # единственный HTTP-клиент
│       ├── http/{errors.py, deps.py, routers/*.py}
│       ├── mcp/server.py        # P3, stretch
│       └── worker/{main.py, jobs.py}
├── frontend/
│   ├── package.json  package-lock.json  vite.config.ts  tsconfig.json
│   └── src/
│       ├── api/schema.d.ts      # генерируется openapi-typescript из 12_openapi.yaml
│       ├── dataProvider.ts  authProvider.ts  i18n/ru.ts
│       ├── resources/{departments,employees,models,subscriptions,providerSources,callers,adminUsers,fxRates,events,audit}/
│       └── pages/{Dashboard,OrgImport,Reports,Reconciliation,ImportStatus,Notifications}.tsx
└── client/
    └── budget_client/           # эталонный клиент (FR-006, SC-002)
        ├── pyproject.toml
        └── budget_client/{client.py, buffer.py, config_cache.py}
deploy/
├── docker-compose.yml           # api, worker, postgres, backup; сети core(internal) и edge
├── docker-compose.test.yml      # postgres-test + tests runner
├── Dockerfile                   # multi-stage: node build SPA → python runtime (api/worker)
├── .env.example
├── backup/{backup.sh, restore.sh}
└── ci/{check.sh, licenses-allow.txt}
design/
├── tokens.json                  # T003
└── screens.md                   # T003: 8 экранов
tests/
├── conftest.py                  # фикстуры: db (миграции на чистой БД), clock (FrozenClock), app client, seed
├── fixtures/{seed.py, ad_groups_ok.csv, ad_groups_errors.csv, anthropic/*.json, openai/*.json}
├── unit/        test_money.py test_periods.py test_amortize.py test_select.py test_permissions.py
├── contract/    test_openapi_contract.py
├── integration/ test_decide_db.py test_settle_db.py test_concurrency.py test_import_pipeline.py test_worker_jobs.py
├── acceptance/  test_us1_decisions.py test_us2_usage.py test_us3_config.py test_us4_subscriptions.py
│                test_us5_import.py test_us6_reports_roles.py test_us7_mcp.py test_edge_cases.py test_messages_ru.py
├── security/    test_no_content.py test_no_egress.py test_secrets_not_leaked.py test_roles_matrix.py
├── smoke/       test_smoke.py   # против поднятого стека, BASE_URL
├── load/        targets.py run_load.sh   # vegeta, SC-001/SC-004
└── failover/    test_sc002_failover.py test_sc003_kill.py
```

**Решение по структуре.** Backend, frontend и клиент лежат в `app/`, общие тесты — в корневом `tests/`, поставка — в `deploy/`. `app/ml` нет (ML_REQUIRED: no). Frontend собирается в статику внутри образа `api` и отдаётся с `/admin`, поэтому отдельного контейнера и Node в рантайме нет.

## 6. Интерфейсы модулей (то, на что опираются задачи)

```python
# bm/domain/types.py — значения, общие для всех модулей
Kop = int                                    # копейки
class PeriodKind(StrEnum): DAY="day"; WEEK="week"; MONTH="month"
class Outcome(StrEnum): ALLOW="allow"; REDIRECT="redirect"; DENY="deny"
class DenyCode(StrEnum): MODEL_NOT_ALLOWED="model_not_allowed"; BUDGET_EXCEEDED="budget_exceeded"; MODEL_UNKNOWN="model_unknown"; SUBJECT_UNKNOWN="subject_unknown"
@dataclass(frozen=True) class Period: kind: PeriodKind; start: date; end: date; resets_at: datetime   # end включительно, resets_at = (end+1) 00:00 МСК
@dataclass(frozen=True) class Price: id: UUID; in_per_1k: Decimal; out_per_1k: Decimal; cached_per_1k: Decimal | None; currency: Literal["RUB","USD"]
@dataclass(frozen=True) class ModelInfo: id: str; kind: str; supports_tools: bool; supports_json: bool; max_context: int; default_in: int; default_out: int; tie_order: int; active: bool; price: Price
@dataclass(frozen=True) class LimitState: owner_type: str; owner_ref: str; period: Period; limit: Kop; spent_token: Kop; spent_import: Kop; amort: Kop; reserved: Kop
    # remaining = limit - spent_token - spent_import - amort - reserved
@dataclass(frozen=True) class Verdict: outcome: Outcome; model: str | None; reserve: Kop; code: DenyCode | None; reason: str | None; observed_violation: str | None; applied: LimitState | None; blocked_by_subs: bool

# bm/domain/money.py
def cost(tokens_in: int, tokens_out: int, tokens_cached: int, price: Price, fx: Decimal) -> Kop
    # round_half_up((in*p_in + out*p_out + cached*(p_cached or p_in)) / 1000 * fx * 100); fx=1 для RUB
def kop_to_str(k: Kop) -> str            # 3000 -> "30.00", -5 -> "-0.05"
def str_to_kop(s: str) -> Kop

# bm/domain/periods.py
MSK = ZoneInfo("Europe/Moscow")
def bounds(kind: PeriodKind, at: datetime) -> Period
def msk_date(at: datetime) -> date

# bm/domain/amortize.py
def month_total(seats: int, seat_price: Decimal, fx: Decimal) -> Kop
def amortized(m_total: Kop, month: date, active_from: date, active_to: date | None, window: tuple[date, date]) -> Kop
    # cum(d) = round_half_up(M * active_days(month_start..d) / days_in_month); результат = cum(min(window.end, active_to)) - cum(window.start - 1)

# bm/domain/select.py
def select(req: DecisionInput, requested: ModelInfo | None, allowed: frozenset[str], catalog: Sequence[ModelInfo],
           limits: Sequence[LimitState], mode: str, fx: Mapping[str, Decimal]) -> Verdict
    # нормативный алгоритм — 12_arch_decision.md §4.5, шаги 2–8

# bm/budget/service.py
class Budget:
    async def decide(self, cmd: DecideCmd, caller: CallerCtx) -> DecisionOut
    async def settle(self, reports: list[UsageReportIn], caller: CallerCtx) -> list[SettlementOut]
    async def balance(self, owner: OwnerRef, at: datetime | None = None) -> BalanceOut
    async def balance_items(self, owner: OwnerRef, period: PeriodKind, at: datetime, source: str | None, page: int, size: int) -> tuple[list[BalanceItemOut], int]
    async def expire_reserves(self, now: datetime) -> int
# DecideCmd/DecisionOut/UsageReportIn/SettlementOut/BalanceOut — Pydantic-модели 1:1 со схемами 12_openapi.yaml
# (DecisionRequest/Decision/UsageReport/UsageResult/BalanceReport); генерируются вручную в bm/http/schemas.py и переиспользуются MCP.

# bm/provider_import/sources/base.py
class CostSource(Protocol):
    provider: Literal["anthropic","openai"]
    async def fetch(self, day_from: date, day_to: date) -> list[CostLine]   # day — сутки UTC
@dataclass(frozen=True) class CostLine: provider: str; source_kind: str; source_ref: str; model: str; day: date; amount: Decimal; currency: str
# bm/provider_import/pipeline.py
async def run(source: CostSource, day_from: date, day_to: date, now: datetime) -> RunResult

# bm/access/authorize.py
def authorize(principal: Principal, action: str, scope: Scope) -> None   # raise Forbidden; аудит пишет вызывающий слой
# bm/access/permissions.py: PERMISSIONS: dict[str, frozenset[Role]] — единственная таблица прав, из неё строится тест SC-010

# bm/egress/client.py
def get_client(purpose: Literal["anthropic","openai","webhook"]) -> httpx.AsyncClient   # raise EgressDisabled

# bm/notify/service.py
async def evaluate(now: datetime) -> list[NotificationOut]
async def deliver(n: NotificationOut) -> None

# budget_client/client.py
class BudgetClient:
    def __init__(self, base_url: str, api_key: str, state_dir: Path, timeout_ms: int = 50) -> None
    def decide(self, subject: dict, model: str, kind: str, **kw) -> ClientDecision   # при сбое: ClientDecision(outcome="allow", fail_open=True) или raise PolicyUnavailable
    def report(self, *, decision_id: str | None, tokens: dict, call_status: str, **kw) -> None   # всегда пишет в буфер, досылает фоном
    def flush(self, max_batch: int = 500) -> int
```

## 7. Данные (DDL — `bm/db/migrations/0001_init.sql`)

| Таблица | Колонки (основные) | Ключи и ограничения |
|---|---|---|
| `department` | id uuid, name, ad_group, policy_mode, fail_mode, subs_warn_pct int default 50 | UNIQUE(ad_group); policy_mode default 'observe' |
| `employee` | id uuid, pseudonym, ad_login, display_name, active | UNIQUE(pseudonym), UNIQUE(ad_login) |
| `employee_assignment` | employee_id, department_id, valid_from date, valid_to date null | одно открытое назначение на сотрудника (partial unique) |
| `caller_system` | id, name, key_prefix, key_sha256, scope_type, scope_department_id, provider_source_id, status, created_at, revoked_at | UNIQUE(key_sha256) |
| `limit_def` | owner_type, owner_id, period, amount_kop bigint | PK(owner_type, owner_id, period); amount_kop ≥ 0 |
| `dept_model_allow` | department_id, model_id | PK |
| `model` | id text, provider, kind, supports_tools, supports_json, max_context, default_in, default_out, tie_order, active | PK(id) |
| `model_price` | id uuid, model_id, valid_from timestamptz, in_per_1k numeric(18,6), out_per_1k, cached_per_1k, currency | неизменяема |
| `fx_rate` | month date (1-е число), usd_rub numeric(12,4), entered_by, entered_at | PK(month) |
| `budget_lock` | owner_type, owner_id, reserved_kop bigint | PK; строка создаётся вместе с владельцем |
| `call_record` | id uuid (v7), caller_id, report_id uuid, employee_id, department_id, requested_model, final_model, outcome, code, price_id, fx numeric, est_in, est_out, est_source, reserve_kop, fact_kop, tokens_in/out/cached, call_status, flags text[], status, expense_date, created_at, settled_at, expires_at | PARTITION BY RANGE(created_at) по месяцам; UNIQUE(caller_id, report_id) — через таблицу `report_key` (уникальность в партициях не глобальна) |
| `report_key` | caller_id, report_id, call_record_id, created_at | PK(caller_id, report_id) — идемпотентность FR-009/FR-011 |
| `ledger_daily` | owner_type, owner_id, day date, source ('token','import'), amount_kop | PK(owner_type, owner_id, day, source) |
| `subscription` | id, provider, plan, seat_price numeric, currency, seats, start_date, end_date, assignee_type, assignee_id | денежные поля неизменяемы (триггер) |
| `subscription_month` | subscription_id, month date, m_total_kop, fx, fx_warning | PK(subscription_id, month) |
| `provider_source` | id, provider, kind, ref, display_name, department_id, mode | UNIQUE(provider, ref) |
| `import_line` | provider, source_ref, model, day, amount_orig numeric, currency, fx, amount_kop, status, version, fetched_at | PK(provider, source_ref, model, day) |
| `import_run` | id, provider, status, started_at, finished_at, day_from, day_to, counters, error_class | |
| `notification` | id, kind, department_id, period, period_start, message, created_at, read_by[], webhook_status | UNIQUE(kind, department_id, period, period_start) для порогов |
| `agg_daily` | day, department_id, model_id, source, amount_kop, tokens | PK(day, department_id, model_id, source) |
| `admin_user`, `session` | login, argon2_hash, role, department_id, active, must_change_password, failed_attempts, locked_until | |
| `audit_log` | id bigserial, at, actor, action, object_type, object_id, before jsonb, after jsonb | роли приложения запрещены UPDATE и DELETE |
| `config_version` | version bigint | одна строка; +1 при каждом `apply` |
| `job_run` | job, started_at, finished_at, ok, error_class | |

**Остаток (FR-026)** для владельца O и периода P на момент `at`:
`remaining = limit(O,P) − Σ ledger_daily(O, day ∈ [P.start, msk_date(at)]) − budget_lock(O).reserved_kop − Σ amortized(sub, …, window=[P.start, msk_date(at)])`.
- Для подразделения учитываются его подписки и подписки сотрудников по отрезкам их назначения в подразделение.
- Резервы не привязаны к периоду: открытый резерв уменьшает все периоды владельца.
- Факт сотрудника пишется в две строки `ledger_daily`: сотрудника и подразделения. Импорт пишется только в строку подразделения.

## 8. Make-цели (проверка локально)

| Цель | Что делает | Признак успеха |
|---|---|---|
| `make test-unit` | `pytest tests/unit` + `npm --prefix app/frontend run test` без Docker | exit 0 |
| `make test` | `docker compose -f deploy/docker-compose.test.yml run --rm tests`: чистый PostgreSQL, миграции, `pytest tests/unit tests/contract tests/integration tests/acceptance tests/security`, import-linter, vitest | exit 0, отчёт pytest без failed |
| `make up` | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build` и ожидание `readyz` (≤ 120 с) | все сервисы `healthy` |
| `make smoke` | `BASE_URL=http://127.0.0.1:8080 pytest tests/smoke`: вход администратора → подразделение, модель, лимиты, ключ → решение allow → отчёт → остаток уменьшился | exit 0 |
| `make load` | vegeta 50 rps × 10 мин + тест границы лимита (3 клиента × 50) | p95 ≤ 10 мс, p99 ≤ 25 мс (из `/metrics`), перерасход ≤ 2% |
| `make failover` | SC-002 и SC-003 на поднятом стеке | 100% fail-open / fail-closed, 0 потерь, 0 дублей |
| `make check` | SBOM (CycloneDX), pip-audit, npm audit, Trivy, проверка лицензий по `deploy/ci/licenses-allow.txt`, import-linter | 0 critical, 0 запрещённых лицензий |
| `make egress-host-check` | Только на стенде QA с root: правило-счётчик `DOCKER-USER`, прогон `make smoke`, 0 пакетов наружу | 0 |
| `make backup` / `make restore` | `pg_dump -Fc` / `pg_restore` в чистую БД + `make smoke` | smoke зелёный после восстановления |

**Переменные окружения (`deploy/.env.example`).** `POSTGRES_PASSWORD`, `DATABASE_URL`, `BIND_ADDR=127.0.0.1`, `API_PORT=8080`, `UVICORN_WORKERS=2`, `SESSION_SECRET`, `ADMIN_INITIAL_LOGIN=admin`, `ADMIN_INITIAL_PASSWORD`, `RESERVATION_TTL_MIN=15`, `DECISION_TIMEOUT_MS=50`, `DEFAULT_FAIL_MODE=open`, `IMPORT_ENABLED=false`, `ANTHROPIC_ADMIN_KEY` / `ANTHROPIC_ADMIN_KEY_FILE`, `OPENAI_ADMIN_KEY` / `OPENAI_ADMIN_KEY_FILE`, `IMPORT_HOUR_MSK=4`, `IMPORT_RETRY_UNTIL_HOUR_MSK=12`, `IMPORT_WINDOW_DAYS=7`, `WEBHOOK_URL=` (пусто — выключен), `SUBS_WARN_PCT_DEFAULT=50`, `RETENTION_DETAIL_MONTHS=12`, `RETENTION_AGG_MONTHS=36`, `BACKUP_KEEP=14`, `LOG_LEVEL=INFO`.

## 9. Review Focus

Случаи, которые спецификация подразумевает, но на которых проще всего ошибиться. Тест на каждый закреплён за задачей.

1. **Неделя на стыке месяцев с подпиской и курсом USD.** Амортизация считается по двум отрезкам с разными `M` и курсами → `test_amortize.py::test_week_across_months_two_fx` (T011).
2. **Отчёт пришёл после истечения резерва.** Резерв уже списан по оценке с флагом `no_report`. Поздний отчёт заменяет оценку фактом: в реестр пишется дельта, флаг остаётся, повтора нет → `test_us2_usage.py::test_late_report_after_expiry_replaces_estimate` (T030).
3. **Переход суток, недели или месяца между `decide` и `settle`.** Расход идёт на дату решения, а не на дату отчёта → `test_settle_db.py::test_expense_date_is_decision_date` (T030).
4. **Остаток уже отрицательный** (поздний импорт или подписки ≥ 100%). Разрешено перенаправление только на модель с нулевой ценой; `budget_blocked_by_subscriptions=true` → `test_select.py::test_negative_remaining_only_zero_price` (T012).
5. **Повторная загрузка AD-файла без сотрудника, у которого есть открытые резервы и личный лимит.** Назначение закрывается, резервы закрываются по отчёту или сроку, личный лимит за период сохраняется → `test_us3_config.py::test_removed_employee_keeps_period_spend` (T040).

## 10. Complexity Tracking

| Отклонение от простейшего | Зачем | Почему проще не подходит |
|---|---|---|
| Контейнер `backup` (4-й сервис) | FR-037, резервное копирование по расписанию без cron на хосте | cron на хосте не входит в «одну команду» и не переносим |
| Таблица `report_key` рядом с партициями | Уникальный `(caller_id, report_id)` при секционировании по `created_at` | В PostgreSQL уникальный индекс на секционированной таблице должен включать ключ секционирования |
| `subscription_month` | Курс задним числом не пересчитывает уже амортизированное (спецификация, «Пограничные случаи») | Расчёт «на лету» с текущим курсом менял бы прошлые дни |
