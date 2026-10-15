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
