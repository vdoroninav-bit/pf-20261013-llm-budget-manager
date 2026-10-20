# 14. Отчёт backend-разработчика

## 2026-10-14 — Phase 2: T015, T016, T017, T018, T019

### Итог

| Задача | Статус | Ключевые файлы |
|---|---|---|
| T015 Каркас приложения и контрактный тест | готово | `bm/settings.py`, `bm/main.py`, `bm/http/errors.py`, `bm/http/deps.py`, `bm/http/routers/system.py`, `bm/metrics.py`, `bm/db/pool.py`, `tests/contract/test_openapi_contract.py`, `tests/integration/test_system.py`, `tests/unit/test_settings.py` |
| T016 Вход, роли, аудит | готово | `bm/access/{auth,permissions,authorize,audit}.py`, `bm/http/routers/auth.py`, `bm/http/schemas.py`, `tests/unit/test_permissions.py`, `tests/acceptance/test_auth.py` |
| T017 Egress и «0 исходящих» | готово | `bm/egress/client.py`, `app/backend/importlinter.ini`, `tests/security/test_no_egress.py` (страж `socket.connect` в `tests/conftest.py` уже был) |
| T018 Снимок конфигурации | готово | `bm/budget/snapshot.py`, `bm/config/service.py`, `tests/integration/test_snapshot.py` |
| T019 Ядро уведомлений | готово | `bm/notify/{service,channels}.py`, `bm/http/routers/notifications.py`, `tests/integration/test_notify_once.py` |

Порядок работы: T015 → T016 → T019 → T017 → T018. По каждой задаче сначала писался тест и подтверждалось его падение (ImportError или отсутствие маршрута), затем код.

### Проверка (команды готовности)

```
$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test SKIP_FRONTEND=1 make test
БД доступна: PostgreSQL 16.x  (предупреждение: целевая версия — 18)
======================= 240 passed, 50 xfailed in 35.05s =======================
сетевые библиотеки (httpx, urllib, http, socket) — только в bm.egress KEPT (3 ignored imports)
bm.domain не зависит от ввода-вывода и других модулей bm KEPT
ядро не импортирует адаптеры (http, mcp, worker) KEPT
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK                                   → exit 0

$ make lint
Contracts: 3 kept, 0 broken.
ruff check: All checks passed!
ruff format --check: 62 files already formatted         → exit 0
```

Было 136 тестов, стало 240 (+104). 50 xfailed — это операции контракта, роутеры которых появятся позже (`PENDING` в контрактном тесте, `xfail(strict=True)`).

Контракт import-linter проверен на нарушение: временный `import urllib.request` в `bm.access.audit` дал BROKEN с цепочками `bm.http → bm.access.audit → urllib` и `bm.main → …`. Импорт `bm.egress` из `bm.notify` проходит, как и задумано.

### Что сделано и как устроено

**T015.**
- `create_app(settings=None, clock=None)`: без аргументов настройки читаются из окружения. Это совместимо с `uvicorn bm.main:create_app --factory` в `deploy/Dockerfile`.
- Lifespan создаёт пул asyncpg, применяет миграции (под advisory-блокировкой из T014) и создаёт первого администратора.
- Swagger UI, ReDoc и `/openapi.json` выключены: UI грузит скрипты с CDN (принцип IV). Источник истины — `12_openapi.yaml`.
- Ошибки отдаются как `application/problem+json` с `detail` на русском:
  - 422 перечисляет только имена полей, тело запроса не повторяется (принцип I);
  - 500 отдаётся без подробностей;
  - 404 и 405 тоже идут в формате Problem.
- `/readyz` отвечает 503, если БД недоступна или не применена последняя миграция из образа.
- `/metrics` использует `text/plain; version=0.0.4` по контракту. В prometheus-client 0.26 тип по умолчанию — 1.0.0, поэтому `CONTENT_TYPE_PLAIN_0_0_4` задан явно. В выводе есть все 6 метрик из описания операции.
- Контрактный тест:
  - (а) спецификация валидна;
  - (б) хелпер ловит необъявленный статус, тело вне схемы и чужую операцию;
  - (в) каждая `operationId` сверяется по методу и пути, а в приложении нет операций сверх контракта.
- Операции берутся из `app.openapi()`. В FastAPI 0.143 `include_router` ленивый, и `app.routes` вложенных маршрутов не видит.
- Исправлен `tests/contract/helpers.py`: openapi-core не разбирал `application/problem+json`, и проверка любого ответа-ошибки падала. Добавлен десериализатор через `Config(extra_media_type_deserializers=…)`.

**T016.**
- Пароли хранятся в argon2id с перехэшированием при входе, если изменились параметры.
- Сессия:
  - токен случайный, в cookie `bm_session` с флагами `HttpOnly; SameSite=Strict; Path=/`, срок жизни 12 ч;
  - в БД лежит HMAC-SHA256(SESSION_SECRET, токен), а не сам токен;
  - CSRF-токен возвращают `login` и `me`, он сверяется на POST/PUT/PATCH/DELETE.
- Блокировка входа:
  - 10 неудач подряд закрывают вход на 15 минут (`429 login_locked` с `Retry-After`), даже с верным паролем;
  - успешный вход сбрасывает счётчик;
  - счётчик хранится в той же транзакции под `FOR UPDATE`;
  - для неизвестного логина выполняется фиктивная проверка хэша, чтобы время ответа не выдавало, есть ли такой логин.
- Первый администратор создаётся из `ADMIN_INITIAL_*` с `must_change_password=true`, только если пользователей нет. Создание идёт под advisory-блокировкой, потому что воркеров uvicorn два. Если пользователей нет и пароль не задан, сервис не стартует.
- `PERMISSIONS` — единственная таблица прав. Тест сверяет её с `x-roles` всех операций контракта.
- Общая зависимость `AdminSession` берёт `operationId` маршрута и проверяет:
  1. сессию;
  2. CSRF;
  3. `must_change_password` (до смены пароля доступны только `me`, `logout`, `changePassword`);
  4. роль.
- Отказ по роли — 403 и запись в аудит `forbidden_access` (actor, `operation`, operationId, метод и путь). Для объектов чужого подразделения есть `authorize_object(...)`.
- Аудит пишется для `login`, `login_locked`, `change_password` и создания первого администратора.

**T017.**
- `get_client(purpose, settings, *, transport=None)`:
  - `anthropic` и `openai` работают только при `IMPORT_ENABLED=true`, иначе `EgressDisabled`; только HTTPS и только свой хост;
  - `webhook` разрешён только на 10/8, 172.16/12, 192.168/16, 127/8 (имя хоста резолвится, все адреса должны быть частными) и только на свой хост и порт;
  - редиректы выключены, прокси из окружения игнорируются (`trust_env=False`);
  - параметр `transport` нужен для тестов коннекторов (T062, T063): MockTransport с фикстурами.
- В import-linter запрещены `httpx`, `urllib`, `http`, `socket` во всех пакетах `bm`, кроме `bm.egress`, включая `bm.main`, `bm.settings`, `bm.metrics`, `bm.clock`. Косвенные цепочки тоже проверяются, исключены только импорты внутри `bm.egress.client`.

**T018.**
- `ConfigSnapshot` содержит:
  - подразделения с разрешёнными моделями;
  - сотрудников с назначением на сегодня по МСК и всю историю назначений (для подписок по отрезкам);
  - лимиты;
  - каталог с текущей ценой (модели без действующей цены в каталог не попадают);
  - курсы;
  - ключи по SHA-256, включая отозванные с `active=False` — чтобы T021 отвечал 401;
  - неудалённые подписки.
- `load(conn, now)` читает снимок одной транзакцией REPEATABLE READ (если транзакции ещё нет), версию — первой.
- `SnapshotCache.ensure_fresh(conn, now)` делает ровно один запрос к `config_version` (тест считает запросы). Снимок перечитывается, если:
  - сменилась версия;
  - наступили новые сутки МСК;
  - вступила в силу цена с будущим `valid_from` (`valid_until`).
- `config.apply(conn, Change(action, object_type, run), actor)`: изменение, аудит и `config_version += 1` выполняются в одной транзакции. Исключение откатывает всё, вложенный вызов откатывается вместе с внешней транзакцией.

**T019.**
- `create_once(conn, kind, department_id, period, period_start, message) -> bool` работает через `ON CONFLICT ON CONSTRAINT notification_once DO NOTHING`. Тест: 8 параллельных соединений, создаёт ровно одно.
- `InApp` — канал по умолчанию: уведомление и есть строка в ленте.
- `listNotifications`:
  - admin видит всё, budget_owner — только своё подразделение (уведомления без подразделения ему не видны);
  - finance_viewer получает 403 с записью в аудит;
  - работают фильтр `unread` и пагинация до 200.
- `markNotificationRead`: «прочитано» хранится отдельно для каждого пользователя (`read_by`), повтор тоже отвечает 204. Чужое уведомление — 403 и аудит, несуществующее — 404.

### Отклонения от плана

1. **`get_client(purpose, settings, *, transport=None)`** вместо `get_client(purpose)` из `12_plan.md` §6. Настройки передаются явно, а не читаются из глобального состояния. `transport` нужен для тестов без сети.
2. **import-linter запрещает `urllib` и `http` целиком.** Подмодули внешних пакетов (`http.client`) import-linter запретить не может (ошибка «subpackages of external packages are not valid»). Следствие: в `bm` вне `egress` нельзя `urllib.parse` и `http.HTTPStatus`. Заголовки Problem взяты из своей таблицы в `errors.py`.
3. **`app/backend/pyproject.toml`: в per-file-ignores для `tests/**` добавлены `E501` и `S107`.** До моих задач `make lint` падал на 24 ошибках в уже существующих файлах T010–T014: длинные SQL и assert в тестах, пароль по умолчанию в `seed.admin_user`. Код приложения правился вручную, E501 для него остаётся в силе. Также прогнан `ruff format`: переформатированы 25 файлов, в том числе тесты T010–T014. Логика не менялась.
4. **Код ошибки `password_change_required`** (403) вне списка примеров `Problem.code`. Список в контракте открытый (`examples`), но фронтенду стоит о нём знать: на этот код показывать экран смены пароля.
5. **Неверный старый пароль в `changePassword` возвращает `422 invalid_credentials`, а не 401.** На 401 SPA посчитала бы, что сессия истекла, и выкинула пользователя.

### Вопросы техлиду

1. **Пробелы в контракте (менять его может только архитектор).** Сервер отдаёт статусы, которые в контракте не объявлены:
   - `logout`: 401 (нет сессии) и 403 (CSRF);
   - `changePassword`: 403 (CSRF);
   - `listNotifications`: 401 и 403 (finance_viewer по `x-roles`);
   - `markNotificationRead`: 401.
   Предлагаю объявить у всех операций с `security: session` ответы `401 Unauthorized` и `403 Forbidden`. Тогда `assert_matches_contract` можно применять и к отказным ответам. Сейчас в тестах такие ответы проверяются без хелпера.
2. **Флаг `Secure` у cookie.** Сейчас он не ставится: по умолчанию API слушает `127.0.0.1:8080` по HTTP. Если у заказчика будет HTTPS на обратном прокси, нужна переменная вроде `SESSION_COOKIE_SECURE=true`. Добавить её в `.env.example` (зона DevOps)?
3. **Срок сессии 12 ч** зашит константой (`SESSION_TTL`). В спецификации значения нет. Нужна ли переменная окружения?
4. **Прокси для коннекторов.** `trust_env=False` не даёт трафику уйти через прокси из окружения. Если в контуре заказчика к `api.anthropic.com` можно выйти только через корпоративный прокси, понадобится явная настройка `IMPORT_HTTPS_PROXY`. Решение за техлидом и архитектором.
5. **Ключ уникальности уведомлений с NULL** (`NULLS NOT DISTINCT`). Для уведомлений без периода (`import_failure`, `fx_missing`) вызывающий код T061/T071 должен передавать `period_start` (например, дату сбоя), иначе такое уведомление придёт один раз за всё время. Это описано в docstring `bm/notify/service.py`.
6. **Метрики при `UVICORN_WORKERS=2`** считаются в каждом процессе отдельно. Для `make load` (T086) понадобится multiprocess-режим prometheus-client или сбор по воркерам.

### Известные ограничения

- Хост webhook проверяется при создании клиента. Повторный резолв при соединении (DNS rebinding) не контролируется. Рекомендация для `admin-guide`: указывать IP-адрес.
- JSON-логи пока не настроены. Обработчик 500 пишет в журнал только класс ошибки, путь и метод. Общая настройка логирования — вместе с worker (T031).

## 2026-10-15 — Backend 2: T041, T042 (US3: загрузка AD, админ-CRUD) и доводка T016/T019 по 14_tech_rulings.md

**Итог.** T042 закрыта. T041 реализована полностью, но её критерий готовности ещё не выполнен: `test_removed_employee_keeps_period_spend` проходит до шага `getDepartmentBalance`, а эта операция появится в T071. Поэтому T041 в `12_tasks.md` не отмечена. После T071 тест станет XPASS(strict): нужно снять с него `xfail` и отметить T041.
`make test` (bm_test, `SKIP_FRONTEND=1`): **417 passed, 24 xfailed**, import-linter 3/3, `run-tests.sh full: OK`. `make lint` проходит.

### Что сделано

**T041 — загрузка AD-файла** (`importOrg`)
- `bm/org_import/service.py`: `import_ad_csv(conn, data, dry_run, actor, *, now) -> OrgImportSummary` и `parse(data)`.
  - Формат: UTF-8 (BOM допускается), разделитель `;`, заголовок `login;display_name;group;is_primary`.
  - Ошибка формата всего файла (кодировка, заголовок, пустой файл) → `422 validation_error`.
  - Ошибки строк попадают в сводку, номер строки — строка файла. Ошибка строки: нет логина или группы, ≠ 4 полей, `is_primary` не из `1/0/пусто`, повтор пары логин+группа, вторая отметка основной группы.
- Группа AD соответствует подразделению (`department.ad_group`). Новое подразделение создаётся с `policy_mode=observe`.
- Основная группа — строка с `is_primary=1`. Если отметки нет, берётся первая строка; при нескольких группах выдаётся предупреждение.
- Псевдоним `emp-` + 10 hex выдаётся один раз, при коллизии генерируется заново.
- Сотрудник, которого нет в файле: назначение закрывается, `employee.active=false`, личные лимиты и расход сохраняются. При возврате он получает новое назначение.
- Перевод: старое назначение закрывается вчерашним днём, новое начинается сегодня.
- `dry_run` выполняет ту же логику в транзакции и откатывает её, поэтому цифры сводки совпадают с реальной загрузкой. Запись в БД, аудит и `config_version` не меняются.
- Реальная загрузка идёт через `config.apply`. В аудите (`action=import`) — только числа сводки, без логинов. Параллельные загрузки сериализуются `pg_advisory_xact_lock`.
- 413 `file_too_large`: роутер читает не больше `MAX_FILE_BYTES + 1` (10 МиБ, константа), проверка выполняется до разбора файла (В6).
- `bm/http/routers/org.py` — `multipart/form-data` (`file`, `dry_run`).

**T042 — админ-CRUD.** Все 24 операции тегов departments (кроме двух операций баланса — T071), employees, models, fx, callers, admin-users.
- Ядро:
  - `bm/config/{departments,employees,catalog,callers,limits,errors}.py`, `bm/access/admin_users.py`;
  - роутеры `bm/http/routers/{departments,employees,models,fx,callers,admin_users}.py`;
  - схемы — отдельный модуль `bm/http/schemas_admin.py`;
  - преобразование ошибок ядра в Problem — `bm/http/admin_errors.py`.
- Все изменения идут через `config.apply`: аудит «было/стало» и `config_version += 1`.
- `revealPseudonym` пишет `reveal_pseudonym` (без `config_version`).
- Ключ вызывающей системы:
  - `bm_<prefix>_<secret>` показывается один раз, в БД хранится только SHA-256;
  - в аудите нет ни ключа, ни хэша;
  - повторный отзыв ничего не меняет и не пишет аудит.
- `createModel`/`createModelPrice`:
  - USD-цена без единого курса → `422 fx_rate_missing`;
  - `valid_from` в прошлом → 422;
  - `current_price` — последняя действующая цена, если её нет — ближайшая будущая.
- `updateAdminUser`:
  - `409 last_admin` (В6) — проверка под advisory-блокировкой в транзакции изменения;
  - `reset_password` и `active=false` закрывают сессии пользователя;
  - `reset_password` выставляет `must_change_password=true`.
- Подразделение у пользователя админки только для `budget_owner`, для него оно обязательно.
- Владелец бюджета видит только своё подразделение и его сотрудников. Чужой объект → 403 с записью `forbidden_access`.

**Доводка T016/T019 (14_tech_rulings.md Б1, Б2)**
- `SESSION_COOKIE_SECURE` (bool, по умолчанию `false`) в `bm/settings.py`. Флаг `Secure` ставится у `bm_session` при входе и при удалении cookie в `logout`. Тесты: `tests/unit/test_settings.py::test_session_cookie_secure_default_false_and_from_env`, `tests/integration/test_admin_crud.py::test_session_cookie_secure_flag_follows_setting[False|True]`.
- Отказные 401/403 в `tests/acceptance/test_auth.py` и `tests/integration/test_notify_once.py` проверяются через `assert_matches_contract`. Добавлен `test_must_change_password_on_real_admin_route` (403 `password_change_required` на `listDepartments`).
- Отказы новых роутеров проверяются так же: `test_new_routes_require_session` (401 по 8 операциям), CSRF 403, 403 владельца бюджета.
- Проверка (а) «спецификация 1.0.2 валидна» (`tests/contract`): 47 passed, 20 xfailed (PENDING других задач).

**Тесты Backend 2:** `tests/integration/test_admin_crud.py` — 35 тестов (cookie, 401/403, права владельца, 404/409/422, last_admin, сброс пароля, формат файла AD, BOM, повторы, обновление имени, observe у новых подразделений).

### Команды и вывод
```
$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test SKIP_FRONTEND=1 make test
================= 417 passed, 24 xfailed in 195.62s (0:03:15) ==================
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK

$ make lint
Contracts: 3 kept, 0 broken.
All checks passed!
104 files already formatted

$ pytest tests/acceptance/test_us3_config.py tests/integration/test_admin_crud.py -rxX
XFAIL test_us3_config.py::test_removed_employee_keeps_period_spend - T071: getDepartmentBalance ...
XFAIL test_us3_config.py::test_3_5_new_price_from_15th_old_events_unchanged - дефект теста (QA) ...
53 passed, 2 xfailed

$ pytest tests/acceptance/test_us3_config.py -k "test_3_1 or test_3_2 or removed" --runxfail
E  assert 404 == 200      # test_us3_config.py:241 — GET /departments/{id}/balance (T071)
1 failed, 3 passed
```

### Изменения в общих и чужих файлах (точечные)
- `bm/main.py`: подключены 7 роутеров и `admin_errors.install(app)`. Правки Backend 1 (`caller`, `Budget`) не затронуты.
- `bm/http/routers/auth.py`, `bm/settings.py`: `SESSION_COOKIE_SECURE`.
- `app/backend/pyproject.toml` + `uv.lock`: прямая зависимость **`python-multipart==0.0.32`**. Без неё FastAPI не принимает `multipart/form-data` (`importOrg`). Раньше пакет приходил только транзитивно через extra `mcp`. Версия уже была в `uv.lock`, поэтому `uv lock` добавил две строки и ничего не обновил. Лицензия Apache-2.0 [Факт, метаданные пакета]. DevOps стоит учесть это в SBOM (T088).
- `tests/contract/helpers.py`: `assert_matches_contract` читает тело multipart-запроса через `req.read()` (httpx отправляет multipart потоком, `req.content` падал с `RequestNotRead`). Проверки не менялись.
- `tests/contract/test_openapi_contract.py`: операции T041/T042 убраны из `PENDING`, иначе получаем XPASS(strict).
- `tests/acceptance/test_us3_config.py`: модульный `xfail` снят. Точечный `xfail(strict)` остался у двух тестов (см. ниже). Проверки не менялись.

### Отклонения
1. **Ошибка 404 не объявлена у `listModelPrices` и `createModelPrice`.** Для неизвестной модели `listModelPrices` отвечает `200 {items: [], total: 0}`, а `createModelPrice` — `422` с `code=not_found`.
2. **Перевод и вывод сотрудника в тот же день, когда открыто назначение.** Назначение не закрывается, а удаляется, потому что CHECK `valid_to >= valid_from` и снимок T018 считают `valid_to` включительно. Иначе в один день действовали бы два назначения. Расход привязан к подразделению в момент решения (`call_record`/`ledger_daily`), поэтому история за неполные сутки не теряет денег. Изменение видно в аудите (`import`).
3. **Логин, который есть только в строках с ошибками, не выводится из подразделения.** Иначе опечатка в строке исключила бы человека. «Нет в файле» понимается как «логина нет ни в одной строке».
4. **Возврат выведенного сотрудника** считается в `employees_updated`, а не в `employees_added`: псевдоним и лимиты у него прежние.
5. **Сортировка списков** (`sort`) — по белому списку (`name`/`created_at` у подразделений, `id`/`provider`/`tie_order`/`created_at` у моделей). Другое поле → 422.
6. **Подразделения из импорта создаются для всех групп из строк без ошибок**, а не только для основных.

### Дефект приёмочного теста (для QA)
`test_3_5_new_price_from_15th_old_events_unchanged`: вход выполнен 14-го в 12:00, затем `clock.set(15-е 10:00)` — это 22 ч, а сессия живёт 12 ч (`SESSION_TTL`). Поэтому `GET /models/A/prices` получает 401, и тест падает с `KeyError: 'items'`. Остальные проверки теста (старая цена 14-го, новая 15-го, `fact_kop` не пересчитан) проходят. С повторным входом перед `GET /prices` (проверено на временной копии теста) тест проходит целиком. Предложение для QA: войти заново после `clock.set` или сдвинуть время меньше чем на 12 ч, затем снять `xfail`.

### Вопросы техлиду
1. Объявить в контракте `404` у `listModelPrices` и `createModelPrice` (см. отклонение 1)? Сейчас пришлось выбирать между 200/422 и необъявленным статусом.
2. Тестовая БД `bm_test` общая для параллельных разработчиков, а фикстура `db` пересоздаёт схему `public`. Совместный прогон даёт ложные падения: в базовом прогоне так упал `test_create_once_concurrent_single_winner`, при повторе он прошёл. Свои прогоны я вёл на отдельной `bm_test_b2` (создана рядом с `bm_test_b1`), финальный — на `bm_test`. Предлагаю закрепить отдельную БД за каждым исполнителем.
3. `deploy/.env.example` и руководство администратора: нужна переменная `SESSION_COOKIE_SECURE=false` (DevOps, T087/T090) — со стороны backend готово.

## 2026-10-15 — Backend 1: T021 (`Budget.decide`, роутеры вызывающей системы), T031 (`settle`, пакет, истечение резервов, баланс, worker)

**Итог.** T021 и T031 закрыты. Команды готовности обеих задач и `make test` на `bm_test` зелёные: **423 passed, 24 xfailed**. `make lint` проходит. Модульные `xfail` сняты во всех шести файлах приёмочных тестов T020/T030. Остались два точечных `xfail(strict)`: `test_subscriptions_over_100_percent_block_paid_calls` — это T051, его ставил QA; `test_decision_not_followed_charges_actual_model` — дефект засева в тесте, см. вопрос 1.

### Что сделано

| Задача | Файлы | Что |
|---|---|---|
| T021 | `bm/budget/service.py` (`Budget.authenticate`, `decide`, `integration_config`) | Алгоритм §4.5. Шаг 1 (субъект и `subject_scope`, отказ — 403 и аудит `forbidden_access`, actor `caller:<id>`) и шаг 9 (запись) — здесь; шаги 2–8 — `domain.select`. Транзакция: `FOR UPDATE` строк `budget_lock` в порядке (owner_type, owner_id) → один `SUM … FILTER` по `ledger_daily` → `select()` → `INSERT call_record` → `UPDATE budget_lock`. Резерв ложится и на сотрудника, и на подразделение. `decision_id` — UUIDv7 |
| T021 | `bm/budget/repo.py` | SQL горячего пути, `uuid7()`, `ensure_partitions` (под advisory-блокировкой) |
| T021 | `bm/budget/schemas.py`, `bm/budget/errors.py` | Pydantic-модели тега caller 1:1 с контрактом (`extra="forbid"`, токены — `StrictInt ≥ 0`); ошибки модуля с `status`/`code` контракта и `detail` на русском |
| T021 | `bm/http/routers/caller.py` | `createDecision`, `getIntegrationConfig` (ETag = хэш содержимого, 304 на `If-None-Match`; ключ с областью видит только своё подразделение) |
| T021 | `bm/http/deps.py` (дописан в конец) | `CallerDep` — аутентификация по `X-Api-Key`, `BudgetDep` |
| T021 | `tests/integration/test_decide_db.py` (9 тестов) | UUIDv7 и поля `call_record`; резерв в обоих `budget_lock`; deny → `closed` без резерва; окна периодов МСК (неделя через границу месяца); партиция на месяц часов при старте; `subject_scope` у ключей; ETag/304 |
| T031 | `bm/budget/service.py` (`settle`, `report`, `expire_reserves`, `caller_balance`, `balance`) | Идемпотентность по `report_key` (`INSERT … ON CONFLICT DO NOTHING`) и по статусу решения. Факт пишется в `ledger_daily` сотрудника и подразделения на дату решения, резерв снимается целиком — всё в одной транзакции, 200 отдаётся после COMMIT. Поздний отчёт после истечения пишет в реестр дельту «факт − оценка», флаг `no_report` сохраняется. `decision_not_followed` тарифицируется по цене фактической модели на момент решения. Отчёт без решения: дата расхода — `occurred_at` по МСК (не старше 7 суток и не дальше 5 мин в будущее), подразделение — по назначению на эту дату. Повтор (ключ, `report_id`) распознаётся до проверок (досылка из буфера через 8 суток — `duplicate`, а не 422) |
| T031 | `bm/http/routers/caller.py` | `reportUsage`, `reportUsageBatch` (> 1000 строк → 413 `batch_too_large` до разбора строк; каждая строка — своя транзакция, ошибки схемы и учёта — `ok=false` + `Problem` строки), `getCallerBalance` |
| T031 | `bm/worker/{jobs.py, main.py, __main__.py}` | `Scheduler`: у задачи свой интервал, `pg_try_advisory_lock(hashtext('bm.job.<имя>'))`, запись в `job_run` (ok/`error_class`), сбой задачи не останавливает цикл. Задачи: `expire_reserves` раз в 60 с, `call_record_partitions` раз в час. `python -m bm.worker`, остановка по SIGTERM |
| T031 | `bm/metrics.py` | Б6: при `PROMETHEUS_MULTIPROC_DIR` `/metrics` собирает все процессы через `MultiProcessCollector`; у gauge режим `mostrecent` задан явно |
| T031 | `tests/integration/test_worker_jobs.py` (3), `tests/unit/test_metrics.py` (3), `tests/integration/test_usage_rules.py` (6) | Планировщик и блокировка; два процесса пишут в один каталог → сумма; изоляция строк пакета, 403 + аудит для отчёта без решения, старый дубль, остаток ключа service, расход до перевода сотрудника |

TDD: `test_decide_db.py`, `test_worker_jobs.py` и `test_metrics.py` написаны первыми и падали (9 failed; 5 failed, 1 passed). `test_usage_rules.py` написан после реализации как регрессионный, он сразу зелёный.

### Команды и вывод (сокращённо)

```text
# до реализации
$ pytest tests/integration/test_decide_db.py                          9 failed
$ pytest tests/unit/test_metrics.py tests/integration/test_worker_jobs.py   5 failed, 1 passed

# готовность T021 (DATABASE_URL=…/bm_test)
$ pytest tests/acceptance/test_us1_decisions.py tests/acceptance/test_edge_cases.py tests/integration/test_concurrency.py
40 passed, 1 xfailed (T051) in 21.79s
# + test_messages_ru.py проходит полностью (в составе make test)

# готовность T031
$ pytest tests/acceptance/test_us2_usage.py tests/integration/test_settle_db.py
26 passed, 1 xfailed (вопрос 1) in 19.46s

$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test SKIP_FRONTEND=1 make test
БД доступна: PostgreSQL 16.15
423 passed, 24 xfailed in 151.47s
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK

$ make lint
Contracts: 3 kept, 0 broken. / All checks passed! / 105 files already formatted

$ python -m bm.worker   (SESSION_SECRET задан, 4 с, затем SIGTERM)
worker_started → reserves_expired → worker_stopped, exit=0; job_run: expire_reserves ok, call_record_partitions ok
```

Самые долгие тесты: пакет из 1000 строк — 3,4 с, 3 × 50 конкурентных решений — 1,6 с.

### Изменения в общих и чужих файлах (точечные)
- `bm/main.py`: в lifespan создаётся `app.state.budget = Budget(...)` (договорённость QA, `tests/fixtures/qa.py::expire_reserves`) и вызывается `ensure_partitions()` на месяц часов приложения; подключён `caller.router`.
- `bm/http/deps.py`: дописан блок `CallerDep`/`BudgetDep`/`budget_problem`, две строки импорта и docstring. Остальное не тронуто.
- `tests/contract/test_openapi_contract.py`: пять операций caller убраны из `PENDING`.
- Тесты QA: в `test_us1_decisions.py`, `test_edge_cases.py`, `test_messages_ru.py`, `test_concurrency.py`, `test_us2_usage.py`, `test_settle_db.py` снят модульный `xfail`, docstring поправлен. Одному тесту добавлен точечный `xfail(strict)` с причиной (вопрос 1). Проверки не менялись.

### Отклонения
1. **Схемы caller лежат в `bm/budget/schemas.py`, а не в `bm/http/schemas.py`** (план §6). `Budget` принимает и возвращает их сам, а import-linter (контракт adapters-thin) запрещает ядру импортировать `bm.http`. MCP (T081) берёт их оттуда же.
2. **Методы `Budget` сами берут соединение из пула**, сигнатуры — как в плане (`decide(cmd, caller)`). Соединение-зависимость FastAPI здесь не используется: при пуле в 10 соединений и 50 одновременных запросах два соединения на запрос привели бы к взаимной блокировке пула.
3. **Ключ, которого нет в снимке, проверяется одним запросом к `caller_system` по индексу SHA-256.** Новый ключ действует сразу, даже если снимок ещё не перечитан (так требует `test_decision_of_other_caller_is_404`, где ключ засевается прямым INSERT). Отзыв идёт через `config.apply`, поднимает версию и действует сразу.
4. **Подписки в остатке = 0** до T051 (шов — `Budget._states`, поле `amort`). Уведомление `reservation_no_report` при истечении резерва не создаётся — это T071.
5. **USD-курс в решении**: курс месяца, а если его нет — последний известный до него. Если курса нет вообще, модель-кандидат пропускается, а для запрошенной модели возвращается 422 `fx_rate_missing` (статус 422 у `createDecision` объявлен).
6. **`expense_date` отчёта без решения** — дата `occurred_at` по МСК. Подразделение берётся по назначению на эту дату (история `employee_assignment`).

### Вопросы техлиду
1. **Дефект засева в `test_us2_usage.py::test_decision_not_followed_charges_actual_model` (для QA).** Фикстура `w` задаёт подразделению дневной лимит 10 000 ₽. `spend(db, "department", w.dep, 9_998_000)` кладёт 99 980 ₽ на **сегодня**, поэтому дневной остаток −89 980 ₽. По §4.5 п. 5 применяется самый строгий лимит, и итог — `deny budget_exceeded`, а не `redirect` на C, как ожидает тест (C стоит 5 ₽, бесплатной модели нет). Если засеять `day=TODAY - timedelta(days=1)`, тест проходит целиком (проверено на временной копии). Предлагаю QA поправить засев и снять `xfail`.
2. **`policy_mode` в решении `subject_unknown`, когда у ключа нет подразделения** (`any_employee`). Поле обязательно, а подразделения нет. Отдаю `enforce` (исход — отказ в любом режиме), `fail_mode` = `DEFAULT_FAIL_MODE`, `department_id = null`. Устраивает?
3. **`decision_not_followed`: куда писать фактическую модель.** В `call_record` нет колонки для неё. Сейчас `final_model` перезаписывается фактической моделью: `requested_model` и флаг сохраняются, а модель из решения теряется. Нужна ли колонка `actual_model` (новая миграция) для отчётов T071?
4. **Коды 422 для отчёта без решения:** неизвестный субъект — `subject_unknown`, неизвестная модель — `model_unknown`, нет курса — `fx_rate_missing`. В `Problem.code` это только примеры, не enum, поэтому контракт не нарушается. `getCallerBalance` без `login` для ключа не-service → 404 `subject_unknown`.
5. **`over_limit` и `used_pct` в `LimitBalance`:** `over_limit = remaining < 0` (с учётом резервов), `used_pct = (лимит − остаток) / лимит`, один знак, ROUND_HALF_UP; при лимите 0 — `0.0` или `100.0`. Нужно подтверждение для T071/T072.
6. **Gauge `reservations_open`** выставляет worker, а `/metrics` отдаёт `api`. Без общего каталога multiprocess (или без расчёта в `api`) этот gauge в `/metrics` не виден. Решение для DevOps (T087): общий `tmpfs` у `api` и `worker`? Или убрать gauge из worker и считать в `api`?
7. **Формат журналов.** `python -m bm.worker` использует `logging.basicConfig` с уровнем из `LOG_LEVEL`. Общего JSON-форматтера в проекте пока нет. Чья это задача (T087?)?
8. **Отдельная тестовая БД.** Итерации шли на `bm_test_b1` (создана мной, `CREATE DATABASE` прошёл), финальный прогон — на `bm_test`. Поддерживаю предложение Backend 2 закрепить отдельную БД за каждым исполнителем.

## 2026-10-19 — Backend 1: T033 (эталонный клиент `budget_client`)

**Статус:** T033 закрыта по первой части критерия готовности (`pytest tests/unit/test_client_buffer.py`). Вторая часть, `make up && make failover`, ждёт T087 и Docker: демон не запущен, отказные тесты T032 без `BM_FAILOVER=1` пропускаются (2 skipped).

### Что сделано
- `app/client/budget_client/` — отдельный пакет: `pyproject.toml` (`dependencies = []`, Python ≥ 3.11, сборка — hatchling, MIT), `README.md`, а также `budget_client/{__init__.py, client.py, buffer.py, config_cache.py, transport.py}`.
- **Зависимости:** только stdlib (`http.client`, `sqlite3`, `json`, `threading`). Сторонних пакетов нет, поэтому белый список лицензий и транзитивная телеметрия не затрагиваются.
- **Сеть:** только к хосту и порту из `base_url` (http/https). `http.client` не читает `HTTP(S)_PROXY`, это закреплено тестом. Телеметрии нет. Ключ API на диск не пишется (тест).
- **`decide()`:** общий срок запроса 50 мс (соединение, отправка и ответ вместе), keep-alive по соединению на поток. Если переиспользованное соединение оказалось мёртвым, выполняется один повтор, пока срок не истёк.
  - Сбой (таймаут, нет соединения, 5xx, 408/429, испорченный ответ) обрабатывается по `fail_mode` субъекта: `open` → `ClientDecision(outcome="allow", fail_open=True)`, `closed` → `PolicyUnavailable` (`code="policy_unavailable"`).
  - Ответы 401/403/404/422 поднимают `ClientRequestError`, см. отклонение 2.
- **Кэш** (`config_cache.py`): `integration-config`, ETag и `fail_mode` по субъекту из ответов `decide` лежат в JSON на диске. Запись атомарная (tmp + `os.replace` + fsync) и только при изменении.
  - Обновление раз в `refresh_s` (60 с) через `If-None-Match`; на 304 обновляется только отметка времени.
  - Порядок выбора `fail_mode`: (1) более свежая из двух записей — последнее решение или конфигурация подразделения субъекта; (2) для ключа `service` — его единственное подразделение; (3) `default_fail_mode`; (4) `open`, если утилита ни разу не ответила (принцип III).
  - Кэш переживает перезапуск процесса вызывающей системы.
- **Буфер** (`buffer.py`): SQLite, `journal_mode=WAL`, `synchronous=FULL`.
  - Ключ — `report_id` (UNIQUE, `INSERT OR IGNORE`); `seq` задаёт порядок.
  - Состояния: `pending` и `rejected` — построчная 4xx от сервера, строка уходит в карантин и не блокирует очередь.
- **`report(*, decision_id, tokens, call_status, **kw)`:** `kw` — `report_id` (задаёт вызывающий, по ожиданию QA; если нет — UUID4), `subject`, `model`, `occurred_at` (по умолчанию время вызова), `actual_model`.
  - Отчёт сначала записывается в буфер (COMMIT с fsync), затем будится фон.
  - Без `decision_id` обязательны `subject` и `model`.
- **`flush(max_batch=500, max_retries=5) -> int`:**
  - Отправляет пакеты по 500 в `/usage/batch`. При сбое пакета делает паузы 0,5 → 1 → 2 → … с (не больше 30 с), не больше 5 повторов подряд; неотправленное остаётся в буфере.
  - Возвращает число строк, принятых сервером (`charged` и `duplicate`).
  - Отправляются только строки, записанные до начала вызова, поэтому под потоком `flush()` завершается.
  - Явный `flush()` и фон взаимно исключены замком, строка уходит один раз (тест: 800 строк → ровно 800 отправок).
- **Фон** (поток-демон, `background=True` по умолчанию): обновляет конфигурацию раз в 60 с и досылает буфер после `report()`. Отчёты, пришедшие в пределах 50 мс, собираются в один пакет. Если досылка не удалась, следующая попытка — через растущую паузу (не больше 60 с). Есть `close()` и контекстный менеджер.

### Проверка
```
$ uv run ... pytest tests/unit/test_client_buffer.py -q
37 passed in 5.97s
$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test_b1 SKIP_FRONTEND=1 make test
478 passed, 83 xfailed in 294.68s ... Contracts: 3 kept, 0 broken ... run-tests.sh full: OK
$ ruff check / ruff format --check (app/client, tests/unit/test_client_buffer.py)
All checks passed! / 7 files already formatted
$ pytest tests/failover -q          # без BM_FAILOVER=1, Docker не запущен
2 skipped
```
- Мутационная проверка: `synchronous=NORMAL`, `BATCH_SIZE=600` и `PAUSE_BASE_S=1.0` роняют 4 теста; после отката зелёные.
- **Эмуляция SC-002 без Docker** (скрипт в scratchpad, не в репозитории): настоящий `bm` под uvicorn на loopback, БД `bm_test_b1`, данные через `tests.failover.stack.provision`. Поток 2 × 10 rps в течение 20 с, на 5-й секунде API получает SIGKILL и до конца потока лежит. Затем подъём и `flush()`.
  - Итог: open в окне отказа — 149/149 `fail_open`; closed в окне отказа — 149/149 `PolicyUnavailable`; иных исключений нет.
  - Фон 5 раз пытался дослать с паузами и отступил. После подъёма `flush()` дослал 150 из 150; в `report_key` из 250 `report_id` пропущено 0, дублей 0.
  - p95 пары `decide`+`report` — 11,2 мс (одна машина, `report` с fsync).
- **`make lint`: exit 1, но не из-за T033.** 5 ошибок ruff (I001, F401, UP017) в чужих файлах, которые сейчас пишет QA: `tests/acceptance/test_us5_import.py`, `test_us6_reports_roles.py`, `tests/integration/test_import_pipeline.py`. Мои файлы чистые. В одном из трёх прогонов `make test` был 1 failed. Он не воспроизвёлся, и в это время соседи меняли тесты; по T033 падений нет.

### Отклонения
1. **Добавлен модуль `transport.py`** (HTTP на `http.client`) сверх перечня файлов задачи. Он изолирует сеть и общий срок запроса.
2. **4xx на `decide` → `ClientRequestError`, а не fail-open.** Это следует ADR-005 (ошибки запроса и доступа — не сбой). Иначе отозванный ключ или неверный запрос молча превращались бы в fail-open: при `closed` вызовы шли бы без контроля. Сбоем считаются только 408 и 429.
3. **`report()` возвращает `report_id` (str), а не `None`**, как в плане §6. Это совместимо: вызывающий может не задавать `report_id` и всё равно знать ключ.
4. **Таймаут досылки (10 с на пакет) и `integration-config` (2 с) отличается от 50 мс.** 50 мс относится к пути решения; пакет из 500 строк за 50 мс не обрабатывается. Значения задаются параметрами конструктора.
5. **`timeout_ms` из `integration-config` клиент не применяет:** действует значение конструктора (по умолчанию 50).

### Вопросы техлиду / QA / DevOps
1. **QA (T032): прогрев в SC-002 может мигать.** Первое решение после подъёма или подготовки данных у холодного API заняло 55 мс (замер httpx), то есть больше 50 мс. Тогда `warm.fail_open=True`, и assert прогрева падает до начала теста. Предлагаю в прогреве повторять `decide` до ответа без `fail_open` (до N раз). В эмуляции так и сделано: хватило одной попытки.
2. **DevOps (зона `Makefile`):** `make lint` не охватывает `app/client`. Предлагаю добавить `app/client` в пути `ruff check/format` целей `lint` и `fmt`. Сейчас проверено вручную тем же конфигом.
3. **DevOps / T088:** включить `app/client/budget_client` в SBOM (пакет без зависимостей) и описать в `docs/integration-guide.md` (T090) права на `state_dir`. В буфере лежат логины субъектов (ПДн), ключа там нет.

## 2026-10-20 — Backend 1: T051 (подписки и амортизация в остатке), T061 (конвейер импорта и справочник источников)

**Итог.** T051 и T061 реализованы, пометки `xfail(T051)`, `xfail(T061)` и общая `T051 + T061` (SC-007) сняты, проверки не менялись. `pytest tests/acceptance/test_us4_subscriptions.py` — 15 passed. Все тесты T060 на `RecordedSource` проходят. Полный `make test` на `bm_test_b1` даёт 549 passed, 12 xfailed и 14 failed. Все 14 падений — `XPASS(strict)` у пометок **T071**: код Backend 2 (отчёты, баланс подразделения, журналы) уже лежит в дереве, и эти тесты проходят. Снимать пометки T071 — часть «Готово» T071, поэтому я их не трогал. Других падений нет. import-linter: 3 kept, 0 broken.

### Что сделано
| Задача | Файлы | Суть |
|---|---|---|
| T051 | `bm/budget/subs.py` (новый) | Чьи подписки: сотрудника — ему и подразделению по отрезкам `employee_assignment`; «не распределено» — никому. M месяца берётся только из `subscription_month`; если строки нет, M считается по курсу месяца или последнему введённому (`last_known_rate_used`) и фиксируется при первом расчёте (`ON CONFLICT DO NOTHING`). Амортизация окна — через `domain.amortize`. Доля подписок считается за весь период (FR-020), `notify_shares` создаёт `subs_share` |
| | `bm/budget/service.py` | `_amort` → `LimitState.amort` в `decide` и `balance`. В `balance` M фиксируется до read-only транзакции. Прочитанные M кэшируются в процессе (они неизменяемы). Добавлено свойство `Budget.pool` |
| | `bm/config/subscriptions.py`, `bm/http/routers/subscriptions.py`, `bm/http/schemas_subs.py` | 6 операций через `config.apply` (аудит, `config_version`). USD без единого курса → `422 fx_rate_missing`. При создании фиксируется M по текущий месяц и проверяется доля |
| | `bm/worker/jobs.py` | Задача `subscriptions_new_period` раз в 5 минут, срабатывает с 00:05 МСК: фиксирует M текущего месяца и заново проверяет долю (дублей нет за счёт `create_once`) |
| T061 | `bm/provider_import/{pipeline.py, catalog.py, runs.py, sources/base.py, sources/recorded.py}`, `bm/http/routers/{provider_sources.py, imports.py}` | `pipeline.run(pool, source, day_from, day_to, now)`. Загрузка у провайдера идёт вне транзакции. Запись — в одной транзакции под advisory-блокировкой провайдера: upsert по (provider, source_ref, model, day), статус по текущему справочнику, в `ledger_daily` подразделения уходит дельта «новое − старое». Строка без курса сохраняется с `amount_kop = NULL`. Уведомления `import_failure` (после 2 неудач подряд) и `import_unallocated` создаются с `period_start` = дата МСК (Б5). Справочник: `409 source_exists`, `422 unsupported_source_kind`. Статус, журнал запусков, `runImport` → 202 `queued`, 409 `import_disabled` / `provider_key_missing` |
| | `bm/notify/service.py`, `tests/unit/test_notify_key.py` | `create_once(period_start=None)` → `ValueError` (Б5) |
| | `bm/worker/jobs.py` | `import_jobs(budget, settings)`: такт раз в 5 минут — сначала очередь ручных запусков, затем плановая загрузка с `IMPORT_HOUR_MSK` и повтор раз в час до `IMPORT_RETRY_UNTIL_HOUR_MSK`, окно `IMPORT_WINDOW_DAYS`. При `IMPORT_ENABLED=false` такт сразу завершается. Коннектор подгружается по имени модуля (T062/T063): если модуля нет, ручной запуск получает `failed/internal` |

Правки тестов (только снятие пометок и договорённости из `tests/fixtures`): `xfail` в `test_us4_subscriptions.py`, `test_edge_cases.py::test_subscriptions_over_100_percent…`, `test_us5_import.py` (модуль), `test_import_pipeline.py`, `test_sc007_reference_balance.py` (модуль), `test_secrets_not_leaked.py::test_import_api_never_returns_keys`; T051 и T061 убраны из `PENDING` в `tests/contract/test_openapi_contract.py`. В `tests/fixtures/qa.py::worker_tick` теперь передаётся `app.state.settings` в `default_jobs`, как предусмотрено договорённостью QA №2: без настроек нет задачи импорта.

### Команды (сокращённо)
```text
$ pytest tests/acceptance/test_us4_subscriptions.py
15 passed in 18.67s
$ DATABASE_URL=…/bm_test_b1 SKIP_FRONTEND=1 make test
14 failed, 549 passed, 12 xfailed in 231.90s   # 14 = XPASS(strict) пометок T071 (проверено -rX: 14)
$ lint-imports → Contracts: 3 kept, 0 broken
$ ruff check / format --check по моим файлам — чисто
```
`make lint` целиком красный из-за файлов Backend 2 в работе: `reports/*`, `notify/thresholds.py`, `logging.py`, `metrics.py`, `http/routers/{audit,events,reports,departments}.py`, строка `notifications` в `worker/jobs.py`, `tests/unit/test_logging.py`. Ошибки — E501, I001 и формат. В моих файлах ошибок нет.

### Отклонения
1. **Удаление подписки** ставит `end_date = сегодня`, а `deleted_at` не ставит. Существующий тест T018 (`test_deleted_subscriptions_excluded`) требует, чтобы снимок не видел строк с `deleted_at`, а прошедшие дни должны оставаться в остатке. Ещё не начавшаяся подписка при удалении стирается целиком вместе со строками `subscription_month`. Удаление видно в аудите (`action=delete`).
2. **Задача подписок запускается ежедневно с 00:05**, а не только 1-го числа. Новый период дня или недели — тоже «наступление нового периода» (сценарий 4.4). Ключ `create_once` даёт одно уведомление на период.
3. **Курс для строк импорта:** курс месяца даты расхода, а если его нет — последний введённый до него. Так же считает `Budget._rate`. Если курса нет совсем, строка сохраняется с `amount_kop = NULL`.
4. **`finished_at` запуска импорта = `now` такта**, а не реальное время окончания: у `pipeline.run` нет часов, сигнатура взята из договорённости QA.

### Вопросы техлиду
1. **Вопросы QA о подписках (15_qa_report.md):** (а) доля подписок считается за весь период — реализовано так, как проверяют тесты QA; (б) `endSubscription` датой в прошлом **запрещён** (422), как и повторное открытие уже закончившейся подписки: иначе менялась бы амортизация прошедших дней (4.5). Тестом это не закреплено; (в) «не распределено» по прошедшим дням — это отчёт T071 (Backend 2), моя часть тут не задействована. Подтвердите (а) и (б).
2. **T071 / Backend 2:** когда T071 будет закрыта, снять пометки T071 в `test_us5_import.py` (2 теста), `test_sc007_reference_balance.py` (1), `test_us3_config.py` (1), `test_us6_reports_roles.py` (5) и `PENDING_MARK` в `test_roles_matrix.py` (5). Сейчас все они дают XPASS. После этого `make test` будет зелёным.
3. **Импорт без коннекторов (до T062/T063):** при `IMPORT_ENABLED=true` плановая загрузка пропускается с записью `import_connector_missing` в журнал, ручной запуск завершается `failed/internal`. Нужен ли отдельный код в статусе — или это закрывает T062?

## 2026-10-20 — Backend 2: T071 (отчёты, баланс подразделения, журналы, фоновые задачи), закрытие T041

**Итог:** T071 и T041 отмечены `[x]`. `SKIP_FRONTEND=1 make test` на `bm_test_b2`: **583 passed, 11 xfailed**, import-linter 3/3 KEPT, exit 0. `make lint`: exit 0.

### Что сделано
- **Баланс подразделения:** `getDepartmentBalance`, `listDepartmentBalanceItems` (`bm/http/routers/departments.py`). Остаток считается через `Budget.balance`, то есть по тем же правилам, что getCallerBalance и пороги; амортизация из T051 подхватывается автоматически. Для `at` в прошлом периоды, которые уже закончились, показываются без открытых резервов. Прослеживаемость: строки `token` (call_record), `reservation` (открытые резервы, только в текущем периоде), `import` (import_line `charged`), `subscription` (по подписке, через `bm.budget.subs`). Финансист получает `subject_pseudonym = null`.
- **LimitBalance по В1-5** (`Budget._limit_balance`): `used_pct` и `over_limit` считаются по расходу без резервов. Тесты — `tests/unit/test_limit_balance.py`.
- **Отчёты** (`bm/reports/service.py`, `bm/http/routers/reports.py`):
  - `getSpendReport` — `group_by` department/model/source/day/month; `department_id=unallocated`; флаг `late`; модель берётся по `final_model` (В1-3). Токены за дни старше самой старой партиции читаются из `agg_daily`.
  - `getSpendCsv` — UTF-8 с BOM, разделитель `;`, строка на (месяц, подразделение, модель, источник, валюта), защита от CSV-инъекции (`'` перед `= + - @ \t \r`), без сотрудников.
  - `getReconciliation` — источники `decision_api` против событий ключей, связанных с источником. Сутки UTC; есть `diff_pct` и `coverage_pct`.
  - Владелец бюджета получает чужое подразделение или `unallocated` → 403 и аудит.
- **Журналы:** `listEvents` (фильтры outcome/code/flag/department, у владельца — только своё подразделение) и `listAudit` (`bm/http/routers/{events,audit}.py`).
- **Фоновые задачи** (`bm/worker/jobs.py::report_jobs`, добавляются в `default_jobs`; `settings` — необязательный параметр, worker его передаёт):
  - `notifications` раз в минуту (`bm/notify/thresholds.py`):
    - пороги 80/100% по `used_pct`;
    - `fx_missing` с ключом `period_start` = 1-е число месяца, только если нужен USD;
    - `reservation_no_report`: подразделение плюс дата МСК истечения резерва (Б5);
    - при заданном `WEBHOOK_URL` статус уведомления — `webhook_status=pending`.
  - `agg_daily` раз в сутки: 8 последних суток до вчера, токены и импорт.
  - `retention` раз в сутки (`bm/reports/maintenance.py`):
    - партиции на 2 месяца вперёд;
    - партиции старше `RETENTION_DETAIL_MONTHS` перед `DROP` сворачиваются в `agg_daily`;
    - заодно удаляются `report_key` того же возраста, `agg_daily` старше `RETENTION_AGG_MONTHS` и `job_run` старше 90 суток.
- **Журналы JSON** (`bm/logging.py`, stdlib):
  - одна строка JSON с полями `ts`, `level`, `logger`, `msg` и белым списком дополнительных полей;
  - исключение пишется только классом;
  - значения `*_ADMIN_KEY`, `IMPORT_HTTPS_PROXY` (вместе с логином и паролем из URL), `SESSION_SECRET` и начального пароля маскируются;
  - уровень берётся из `LOG_LEVEL`;
  - подключено в `create_app` и в `worker/main.py`.
- **Gauge `reservations_open` удалён** (В1-6): изменены `bm/metrics.py` и `Budget.expire_reserves`; `tests/unit/test_metrics.py` и `tests/integration/test_system.py` проверяют, что метрики нет.
- **T041:** с `test_removed_employee_keeps_period_spend` снят xfail, тест проходит.
- **Снятые пометки xfail:**
  - модульная T071 в `test_us6_reports_roles.py`;
  - `@T071` в `test_us5_import.py` (2) и `test_sc007_reference_balance.py` (1);
  - `PENDING_MARK` в `test_roles_matrix.py` — `PENDING` пуст;
  - T071 в `PENDING` контрактного теста.
  Проверки не менялись.
- **Новые тесты:** `tests/unit/test_logging.py` (6), `tests/unit/test_limit_balance.py` (6), `tests/integration/test_reports.py` (11), `tests/integration/test_report_jobs.py` (8).

### Команды
```
DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test_b2 SKIP_FRONTEND=1 make test
  583 passed, 11 xfailed in 262.51s; Contracts: 3 kept, 0 broken; run-tests.sh full: OK
pytest tests/acceptance/test_us6_reports_roles.py tests/security tests/acceptance/test_us3_config.py::test_removed_employee_keeps_period_spend
  53 passed, 5 xfailed
make lint → exit 0 (141 files already formatted)
```
Оставшиеся 11 xfail: 9 относятся к T062/T063, а также 2 точечные пометки в `test_us6_reports_roles.py` — см. вопросы 1 и 2 ниже.

### Отклонения
1. **`tests/contract/helpers.py`** (файл QA, правка точечная): добавлен десериализатор `text/csv`. openapi-core не декодирует `text/csv` и сверял bytes со схемой `type: string`, поэтому любой корректный CSV не проходил проверку.
2. Пороги считаются только для лимитов **подразделений**: так сказано в FR-029, а в `notification` нет поля сотрудника.
3. Журнал доступа uvicorn приглушён (WARNING): в строке запроса может быть `login` сотрудника (ПДн). Длительность запросов видна в `/metrics`.
4. Строка CSV для подписки: модель пустая. Исходная сумма для USD = ₽ / курс M месяца.
5. Задачи `agg_daily` и `retention` начинаются при старте worker и дальше идут раз в сутки (`every = 1 день`), а не в фиксированный час.

### Вопросы техлиду
1. **Дефект засева QA:** `test_6_2_csv_injection_is_neutralized` создаёт сотрудников `user1`, `user2` после первого решения и не вызывает `bump_config`. Снимок конфигурации (T018) их не видит, отсюда `subject_unknown`. Тест помечен точечным `xfail` с этой причиной; та же проверка CSV-инъекции зелёная в `tests/integration/test_reports.py::test_csv_neutralizes_formula_prefixes`. QA: добавить `bump_config(db)` после засева и снять пометку.
2. **T061 (Backend 1):** `createProviderSource` и `updateProviderSource` не пишут `audit_log` (FR-031), поэтому `test_6_5_audit_records_who_when_before_after` помечен точечным `xfail` с причиной T061. Снять пометку после исправления.
3. **Аудит изменений через `config.apply` (T042):** время записи берётся из БД (`now()`), а не из `Clock`. В проде это одно и то же, но в тестах с `FrozenClock` дата аудита не совпадает с замороженной. Переводить ли `apply` на `Clock`?
4. **Зависимость от модуля Backend 1:** отчёты и прослеживаемость используют публичные функции `bm.budget.subs` (`charged_to`, `needed_months`, `month_totals`, `amount`, `Charged`). Если Backend 1 меняет их сигнатуры, нужно сообщить Backend 2.
