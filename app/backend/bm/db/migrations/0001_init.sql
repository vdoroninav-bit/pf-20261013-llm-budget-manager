-- 0001_init: схема LLM Budget Manager v1 (12_plan.md §7, ADR-002, ADR-003, ADR-004).
-- Совместимость: PostgreSQL 16 (локальный стенд) и 18 (поставка). Возможности только PG 18 не используются;
-- UUID v7 для call_record генерирует приложение.
-- Деньги — BIGINT копеек (*_kop), цены и курсы — NUMERIC. Типов real/double precision нет (принцип VI).
-- Текстовых полей для содержимого запросов моделей нет (принцип I).

-- ---------------------------------------------------------------- оргструктура
CREATE TABLE department (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    ad_group      text UNIQUE,
    policy_mode   text NOT NULL DEFAULT 'observe' CHECK (policy_mode IN ('observe', 'warn', 'enforce')),
    fail_mode     text NOT NULL DEFAULT 'open' CHECK (fail_mode IN ('open', 'closed')),
    subs_warn_pct integer NOT NULL DEFAULT 50 CHECK (subs_warn_pct BETWEEN 1 AND 100),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employee (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pseudonym    text NOT NULL UNIQUE CHECK (pseudonym ~ '^emp-[0-9a-f]{10}$'),
    ad_login     text NOT NULL UNIQUE,
    display_name text,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- История назначений: расход до перевода остаётся у старого подразделения (пограничные случаи спецификации).
CREATE TABLE employee_assignment (
    id            bigserial PRIMARY KEY,
    employee_id   uuid NOT NULL REFERENCES employee(id),
    department_id uuid NOT NULL REFERENCES department(id),
    valid_from    date NOT NULL,
    valid_to      date,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX employee_assignment_one_open ON employee_assignment(employee_id) WHERE valid_to IS NULL;
CREATE INDEX employee_assignment_department ON employee_assignment(department_id);

-- ---------------------------------------------------------------- каталог моделей и курсы
CREATE TABLE model (
    id             text PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
    provider       text NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('chat', 'embedding')),
    supports_tools boolean NOT NULL,
    supports_json  boolean NOT NULL,
    max_context    integer NOT NULL CHECK (max_context >= 1),
    default_in     integer NOT NULL CHECK (default_in >= 0),
    default_out    integer NOT NULL CHECK (default_out >= 0),
    tie_order      integer NOT NULL DEFAULT 100,
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- История цен неизменяема: новая цена — новая строка с valid_from.
CREATE TABLE model_price (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id      text NOT NULL REFERENCES model(id),
    valid_from    timestamptz NOT NULL,
    in_per_1k     numeric(18, 6) NOT NULL CHECK (in_per_1k >= 0),
    out_per_1k    numeric(18, 6) NOT NULL CHECK (out_per_1k >= 0),
    cached_per_1k numeric(18, 6) CHECK (cached_per_1k >= 0),
    currency      text NOT NULL CHECK (currency IN ('RUB', 'USD')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_id, valid_from)
);

CREATE FUNCTION bm_model_price_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'model_price: история цен неизменяема (добавьте новую цену с valid_from)';
END $$;
CREATE TRIGGER model_price_immutable BEFORE UPDATE ON model_price
    FOR EACH ROW EXECUTE FUNCTION bm_model_price_immutable();

CREATE TABLE dept_model_allow (
    department_id uuid NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    model_id      text NOT NULL REFERENCES model(id),
    PRIMARY KEY (department_id, model_id)
);

CREATE TABLE fx_rate (
    month      date PRIMARY KEY CHECK (extract(day FROM month) = 1),
    usd_rub    numeric(12, 4) NOT NULL CHECK (usd_rub > 0),
    entered_by text NOT NULL,
    entered_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- лимиты и блокировки
CREATE TABLE limit_def (
    owner_type text NOT NULL CHECK (owner_type IN ('employee', 'department')),
    owner_id   uuid NOT NULL,
    period     text NOT NULL CHECK (period IN ('day', 'week', 'month')),
    amount_kop bigint NOT NULL CHECK (amount_kop >= 0),
    PRIMARY KEY (owner_type, owner_id, period)
);

-- Строка на каждого владельца бюджета; блокируется FOR UPDATE в порядке (owner_type, owner_id) (ADR-002).
CREATE TABLE budget_lock (
    owner_type   text NOT NULL CHECK (owner_type IN ('employee', 'department')),
    owner_id     uuid NOT NULL,
    reserved_kop bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_type, owner_id)
);

CREATE FUNCTION bm_create_budget_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO budget_lock(owner_type, owner_id) VALUES (TG_ARGV[0], NEW.id) ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
CREATE TRIGGER department_budget_lock AFTER INSERT ON department
    FOR EACH ROW EXECUTE FUNCTION bm_create_budget_lock('department');
CREATE TRIGGER employee_budget_lock AFTER INSERT ON employee
    FOR EACH ROW EXECUTE FUNCTION bm_create_budget_lock('employee');

-- ---------------------------------------------------------------- источники провайдеров и ключи
CREATE TABLE provider_source (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider      text NOT NULL CHECK (provider IN ('anthropic', 'openai')),
    kind          text NOT NULL CHECK (kind IN ('workspace', 'project', 'api_key')),
    ref           text NOT NULL,
    display_name  text,
    department_id uuid REFERENCES department(id),
    mode          text NOT NULL CHECK (mode IN ('import', 'decision_api')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, ref)
);

CREATE TABLE caller_system (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL,
    key_prefix          text NOT NULL,
    key_sha256          bytea NOT NULL UNIQUE CHECK (length(key_sha256) = 32),
    scope_type          text NOT NULL CHECK (scope_type IN ('service', 'department_employees', 'any_employee')),
    scope_department_id uuid REFERENCES department(id),
    provider_source_id  uuid REFERENCES provider_source(id),
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz,
    CHECK (scope_type = 'any_employee' OR scope_department_id IS NOT NULL)
);

-- ---------------------------------------------------------------- решения и расход
CREATE TABLE call_record (
    id              uuid NOT NULL,
    caller_id       uuid NOT NULL,
    report_id       uuid,
    employee_id     uuid,
    department_id   uuid,
    requested_model text NOT NULL,
    final_model     text,
    outcome         text NOT NULL CHECK (outcome IN ('allow', 'redirect', 'deny', 'none')),
    code            text CHECK (code IN ('model_not_allowed', 'budget_exceeded', 'model_unknown', 'subject_unknown')),
    price_id        uuid,
    fx              numeric(12, 4),
    est_in          integer,
    est_out         integer,
    est_source      text CHECK (est_source IN ('request', 'catalog_default')),
    reserve_kop     bigint NOT NULL DEFAULT 0,
    fact_kop        bigint,
    tokens_in       integer,
    tokens_out      integer,
    tokens_cached   integer,
    call_status     text CHECK (call_status IN ('ok', 'aborted', 'error')),
    flags           text[] NOT NULL DEFAULT '{}',
    status          text NOT NULL CHECK (status IN ('open', 'settled', 'expired', 'closed')),
    expense_date    date NOT NULL,
    created_at      timestamptz NOT NULL,
    settled_at      timestamptz,
    expires_at      timestamptz,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX call_record_open_expiry ON call_record(expires_at) WHERE status = 'open';
CREATE INDEX call_record_department_date ON call_record(department_id, expense_date);

-- Партиции по месяцам МСК. Функцию вызывает миграция и (позже) worker раз в сутки.
CREATE FUNCTION bm_ensure_call_record_partitions(at date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m     date;
    lo    timestamptz;
    hi    timestamptz;
    pname text;
BEGIN
    FOR i IN 0..1 LOOP
        m := (date_trunc('month', at) + make_interval(months => i))::date;
        lo := (m::timestamp) AT TIME ZONE 'Europe/Moscow';
        hi := ((m + interval '1 month')::timestamp) AT TIME ZONE 'Europe/Moscow';
        pname := format('call_record_%s', to_char(m, 'YYYY_MM'));
        IF to_regclass(pname) IS NULL THEN
            EXECUTE format('CREATE TABLE %I PARTITION OF call_record FOR VALUES FROM (%L) TO (%L)', pname, lo, hi);
        END IF;
    END LOOP;
END $$;
SELECT bm_ensure_call_record_partitions((now() AT TIME ZONE 'Europe/Moscow')::date);

-- Уникальность (caller_id, report_id) глобально: на секционированной таблице она невозможна без ключа секции.
CREATE TABLE report_key (
    caller_id      uuid NOT NULL,
    report_id      uuid NOT NULL,
    call_record_id uuid NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (caller_id, report_id)
);

-- Суточный реестр расхода по дате расхода (ADR-003). Факт сотрудника — две строки: сотрудник и подразделение.
CREATE TABLE ledger_daily (
    owner_type text NOT NULL CHECK (owner_type IN ('employee', 'department')),
    owner_id   uuid NOT NULL,
    day        date NOT NULL,
    source     text NOT NULL CHECK (source IN ('token', 'import')),
    amount_kop bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_type, owner_id, day, source)
);

-- ---------------------------------------------------------------- подписки
CREATE TABLE subscription (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider      text NOT NULL,
    plan          text NOT NULL,
    seat_price    numeric(18, 6) NOT NULL CHECK (seat_price >= 0),
    currency      text NOT NULL CHECK (currency IN ('RUB', 'USD')),
    seats         integer NOT NULL CHECK (seats >= 1),
    start_date    date NOT NULL,
    end_date      date CHECK (end_date IS NULL OR end_date >= start_date),
    assignee_type text NOT NULL CHECK (assignee_type IN ('employee', 'department', 'unallocated')),
    assignee_id   uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    CHECK ((assignee_type = 'unallocated') = (assignee_id IS NULL))
);

-- Денежные поля и назначение неизменяемы: изменить подписку = закрыть датой и создать новую (ADR-003).
CREATE FUNCTION bm_subscription_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.provider, NEW.plan, NEW.seat_price, NEW.currency, NEW.seats, NEW.start_date, NEW.assignee_type,
        NEW.assignee_id) IS DISTINCT FROM
       (OLD.provider, OLD.plan, OLD.seat_price, OLD.currency, OLD.seats, OLD.start_date, OLD.assignee_type,
        OLD.assignee_id) THEN
        RAISE EXCEPTION 'subscription: денежные поля и назначение неизменяемы (закройте подписку и создайте новую)';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER subscription_immutable BEFORE UPDATE ON subscription
    FOR EACH ROW EXECUTE FUNCTION bm_subscription_immutable();

-- M месяца фиксируется при первом расчёте вместе с курсом: курс задним числом не пересчитывает прошлое.
CREATE TABLE subscription_month (
    subscription_id uuid NOT NULL REFERENCES subscription(id),
    month           date NOT NULL CHECK (extract(day FROM month) = 1),
    m_total_kop     bigint NOT NULL CHECK (m_total_kop >= 0),
    fx              numeric(12, 4),
    fx_warning      text CHECK (fx_warning IN ('last_known_rate_used', 'no_rate')),
    PRIMARY KEY (subscription_id, month)
);

-- ---------------------------------------------------------------- импорт провайдеров (ADR-004)
CREATE TABLE import_line (
    provider      text NOT NULL CHECK (provider IN ('anthropic', 'openai')),
    source_ref    text NOT NULL,
    model         text NOT NULL,
    day           date NOT NULL,
    amount_orig   numeric(18, 6) NOT NULL,
    currency      text NOT NULL CHECK (currency IN ('RUB', 'USD')),
    fx            numeric(12, 4),
    amount_kop    bigint,
    department_id uuid,
    status        text NOT NULL CHECK (status IN ('charged', 'reconcile', 'unallocated')),
    version       integer NOT NULL DEFAULT 1,
    fetched_at    timestamptz NOT NULL,
    PRIMARY KEY (provider, source_ref, model, day)
);

CREATE TABLE import_run (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        text NOT NULL CHECK (provider IN ('anthropic', 'openai')),
    status          text NOT NULL CHECK (status IN ('queued', 'running', 'ok', 'failed')),
    started_at      timestamptz,
    finished_at     timestamptz,
    day_from        date NOT NULL,
    day_to          date NOT NULL,
    lines_charged   integer NOT NULL DEFAULT 0,
    lines_reconcile integer NOT NULL DEFAULT 0,
    lines_unallocated integer NOT NULL DEFAULT 0,
    lines_changed   integer NOT NULL DEFAULT 0,
    error_class     text CHECK (error_class IN ('auth_rejected', 'provider_unavailable', 'bad_response', 'internal')),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX import_run_provider_created ON import_run(provider, created_at DESC);

-- ---------------------------------------------------------------- уведомления
CREATE TABLE notification (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind           text NOT NULL CHECK (kind IN ('threshold_80', 'threshold_100', 'subs_share', 'import_failure',
                                                 'import_unallocated', 'fx_missing', 'reservation_no_report')),
    department_id  uuid REFERENCES department(id),
    period         text CHECK (period IN ('day', 'week', 'month')),
    period_start   date,
    message        text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    read_by        uuid[] NOT NULL DEFAULT '{}',
    webhook_status text NOT NULL DEFAULT 'disabled' CHECK (webhook_status IN ('disabled', 'pending', 'delivered', 'failed')),
    -- Одно уведомление на ключ (FR-029). NULLS NOT DISTINCT (PG 15+): NULL в ключе считается равным NULL.
    CONSTRAINT notification_once UNIQUE NULLS NOT DISTINCT (kind, department_id, period, period_start)
);
CREATE INDEX notification_department_created ON notification(department_id, created_at DESC);

-- ---------------------------------------------------------------- агрегаты (хранятся 36 мес.)
CREATE TABLE agg_daily (
    day           date NOT NULL,
    department_id uuid NOT NULL,
    model_id      text NOT NULL,
    source        text NOT NULL CHECK (source IN ('token', 'import', 'subscription')),
    amount_kop    bigint NOT NULL DEFAULT 0,
    tokens        bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (day, department_id, model_id, source)
);

-- ---------------------------------------------------------------- админка
CREATE TABLE admin_user (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login                text NOT NULL UNIQUE CHECK (length(login) BETWEEN 1 AND 128),
    argon2_hash          text NOT NULL,
    role                 text NOT NULL CHECK (role IN ('admin', 'budget_owner', 'finance_viewer')),
    department_id        uuid REFERENCES department(id),
    active               boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT false,
    failed_attempts      integer NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (role <> 'budget_owner' OR department_id IS NOT NULL)
);

-- Хранится SHA-256 токена сессии, а не сам токен.
CREATE TABLE session (
    token_sha256 bytea PRIMARY KEY CHECK (length(token_sha256) = 32),
    user_id      uuid NOT NULL REFERENCES admin_user(id) ON DELETE CASCADE,
    csrf_token   text NOT NULL,
    created_at   timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL
);
CREATE INDEX session_by_user ON session(user_id);

-- ---------------------------------------------------------------- аудит (только INSERT)
CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    action      text NOT NULL,
    object_type text NOT NULL,
    object_id   text,
    before      jsonb,
    after       jsonb
);
CREATE INDEX audit_log_at ON audit_log(at);

-- Роль приложения (владелец таблиц) не может менять и удалять записи аудита.
-- Триггер дублирует запрет для суперпользователя, на которого REVOKE не действует.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM CURRENT_USER;

CREATE FUNCTION bm_audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log: журнал аудита только дополняется (% запрещён)', TG_OP;
END $$;
CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION bm_audit_log_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION bm_audit_log_append_only();

-- ---------------------------------------------------------------- версия конфигурации и задачи
CREATE TABLE config_version (
    id      boolean PRIMARY KEY DEFAULT true CHECK (id),
    version bigint NOT NULL
);
INSERT INTO config_version(id, version) VALUES (true, 1);

CREATE TABLE job_run (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    ok          boolean,
    error_class text
);
CREATE INDEX job_run_job_started ON job_run(job, started_at DESC);
