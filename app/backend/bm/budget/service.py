"""Глубокий модуль денег: `Budget` — решение, учёт факта, остатки, истечение резервов (12_plan.md §6).

Нормативный алгоритм решения — 12_arch_decision.md §4.5. Шаг 1 (субъект) и шаг 9 (запись) — здесь,
шаги 2–8 — чистая функция `bm.domain.select.select`.

Транзакция решения (ADR-002): блокировка строк `budget_lock` владельцев в порядке (owner_type, owner_id)
→ один `SUM … FILTER` по `ledger_daily` → `select()` → `INSERT call_record` → `UPDATE budget_lock`.
Справочники берутся из снимка конфигурации в памяти (`SnapshotCache.ensure_fresh` — один запрос версии).

Адаптеры (HTTP, MCP) получают экземпляр из `app.state.budget` и переводят `BudgetError` в свой формат.
"""

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import asyncpg

from bm import metrics
from bm.access import audit
from bm.budget import repo, subs
from bm.budget.errors import (
    BudgetError,
    DecisionNotFound,
    FxRateMissing,
    InvalidReport,
    SubjectNotFound,
    SubjectOutOfScope,
    Unauthenticated,
)
from bm.budget.repo import Owner
from bm.budget.schemas import (
    AppliedLimit,
    BalanceReport,
    Decision,
    DecisionRequest,
    DepartmentFailMode,
    EmployeeSubject,
    EstimateOut,
    IntegrationConfig,
    LimitBalance,
    SpentBySource,
    SubjectRef,
    UsageReport,
    UsageResult,
)
from bm.budget.snapshot import ConfigSnapshot, DepartmentCfg, EmployeeCfg, SnapshotCache
from bm.clock import Clock
from bm.domain import codes
from bm.domain.money import cost, kop_to_str
from bm.domain.periods import MSK, bounds, msk_date
from bm.domain.select import MissingFxRate, select
from bm.domain.types import DecisionInput, DenyCode, Kop, LimitState, Outcome, PeriodKind, Price, Verdict

log = logging.getLogger("bm.budget")

_PERIODS = (PeriodKind.DAY, PeriodKind.WEEK, PeriodKind.MONTH)
_ONE = Decimal(1)
REPORT_MAX_AGE = timedelta(
    days=7
)  # отчёт без решения — не раньше 7 суток назад (12_arch_decision.md §6 п. 8)
REPORT_MAX_AHEAD = timedelta(minutes=5)  # допуск расхождения часов вызывающей системы
INTEGRATION_REFRESH_S = 60


@dataclass(frozen=True, slots=True)
class CallerCtx:
    """Аутентифицированная вызывающая система (ключ `X-Api-Key`)."""

    id: UUID
    name: str
    scope_type: str  # service | department_employees | any_employee
    scope_department_id: UUID | None

    @property
    def actor(self) -> str:
        """Актор в журнале аудита (`AuditEntry.actor`)."""
        return f"caller:{self.id}"


@dataclass(frozen=True, slots=True)
class OwnerRef:
    """Владелец бюджета: сотрудник (по id) или подразделение."""

    owner_type: str  # employee | department
    owner_id: UUID


@dataclass(frozen=True, slots=True)
class _Subject:
    employee: EmployeeCfg | None  # None — субъект-сервис
    department: DepartmentCfg

    @property
    def owners(self) -> list[Owner]:
        """Владельцы бюджета в порядке блокировки (owner_type, owner_id)."""
        out: list[Owner] = [("department", self.department.id)]
        if self.employee is not None:
            out.append(("employee", self.employee.id))
        return sorted(out)


@dataclass(frozen=True, slots=True)
class _LimitSpec:
    owner_type: str
    owner_id: UUID
    owner_ref: str
    period: PeriodKind
    limit: Kop


def _msk(at: datetime) -> datetime:
    return at.astimezone(MSK)


def _pct(part: Kop, whole: Kop) -> str:
    """Процент с одним знаком, ROUND_HALF_UP, без float. Лимит 0: 0.0 без расхода, иначе 100.0."""
    if whole == 0:
        return "0.0" if part == 0 else ("100.0" if part > 0 else "-100.0")
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value:.1f}"


def _price(row: asyncpg.Record) -> Price:
    return Price(
        id=row["id"],
        in_per_1k=row["in_per_1k"],
        out_per_1k=row["out_per_1k"],
        cached_per_1k=row["cached_per_1k"],
        currency=row["currency"],
    )


def _flags(v: Verdict) -> list[str]:
    if v.observed_violation == "budget_exceeded":
        return ["over_limit_observed"]
    if v.observed_violation == "model_not_allowed":
        return ["model_not_allowed_observed"]
    return []


class Budget:
    """Экземпляр на процесс (api или worker). Соединения берёт из пула сам."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        clock: Clock,
        *,
        reservation_ttl: timedelta = timedelta(minutes=15),
        timeout_ms: int = 50,
        default_fail_mode: str = "open",
        snapshots: SnapshotCache | None = None,
    ) -> None:
        self._pool = pool
        self._clock = clock
        self._ttl = reservation_ttl
        self._timeout_ms = timeout_ms
        self._default_fail_mode = default_fail_mode
        self._snapshots = snapshots or SnapshotCache()
        self._month_totals: dict[subs.MonthKey, subs.MonthTotal] = {}  # M месяцев неизменяемы (T051)

    @property
    def pool(self) -> asyncpg.Pool:
        """Пул соединений процесса (задачи worker: подписки, импорт)."""
        return self._pool

    # ------------------------------------------------------------ служебное

    def now(self) -> datetime:
        return self._clock.now()

    async def ensure_partitions(self, at: datetime | None = None) -> None:
        """Партиции `call_record` на текущий (по часам `Budget`) и следующий месяц."""
        async with self._pool.acquire() as conn:
            await repo.ensure_partitions(conn, msk_date(at or self._clock.now()))

    async def _snapshot(self, conn: asyncpg.Connection, now: datetime) -> ConfigSnapshot:
        return await self._snapshots.ensure_fresh(conn, now)

    async def authenticate(self, api_key: str | None) -> CallerCtx:
        """Ключ `bm_<prefix>_<secret>` → вызывающая система. Нет ключа, чужой или отозванный — 401."""
        if not api_key:
            raise Unauthenticated("Нет заголовка X-Api-Key: передайте ключ вызывающей системы.")
        digest = hashlib.sha256(api_key.encode()).digest()
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, self._clock.now())
            key = snap.keys.get(digest)
            if key is None:  # ключ мог появиться после загрузки снимка — одна проверка по индексу
                key = await repo.caller_key(conn, digest)
        if key is None or not key.active:
            raise Unauthenticated("Ключ вызывающей системы недействителен или отозван.")
        return CallerCtx(key.id, key.name, key.scope_type, key.scope_department_id)

    async def _out_of_scope(self, conn: asyncpg.Connection, caller: CallerCtx, operation: str) -> None:
        """Субъект вне `subject_scope` ключа — ошибка доступа, пишется в аудит (§4.5 п. 1)."""
        await audit.write(
            conn,
            actor=caller.actor,
            action="forbidden_access",
            object_type="operation",
            object_id=operation,
            after={"reason": "subject_out_of_scope", "scope_type": caller.scope_type},
            at=self._clock.now(),
        )
        raise SubjectOutOfScope("Субъект вне области действия ключа вызывающей системы.")

    def _resolve(
        self, snap: ConfigSnapshot, caller: CallerCtx, subject: SubjectRef | None
    ) -> bool | _Subject | None:
        """Субъект → (сотрудник, подразделение). None — не найден; False — вне `subject_scope` ключа."""
        if subject is None or not isinstance(subject, EmployeeSubject):
            if caller.scope_type != "service" or caller.scope_department_id is None:
                return False
            dept = snap.departments.get(caller.scope_department_id)
            return _Subject(None, dept) if dept is not None else None
        if caller.scope_type == "service":
            return False
        emp = snap.employees_by_login.get(subject.login)
        if emp is None:
            return None
        if caller.scope_type == "department_employees" and emp.department_id != caller.scope_department_id:
            return False
        return _Subject(emp, snap.departments[emp.department_id])

    @staticmethod
    def _fx(snap: ConfigSnapshot, day: date) -> dict[str, Decimal]:
        """Курс USD месяца `day`; нет курса месяца — последний известный до него."""
        month = day.replace(day=1)
        known = [m for m in snap.fx if m <= month]
        return {"USD": snap.fx[max(known)]} if known else {}

    @staticmethod
    def _limit_specs(snap: ConfigSnapshot, subj: _Subject) -> list[_LimitSpec]:
        """Заданные лимиты: сотрудника, затем подразделения; внутри — день, неделя, месяц."""
        specs: list[_LimitSpec] = []
        owners: list[tuple[str, UUID, str]] = []
        if subj.employee is not None:
            owners.append(("employee", subj.employee.id, subj.employee.pseudonym))
        owners.append(("department", subj.department.id, str(subj.department.id)))
        for owner_type, owner_id, ref in owners:
            defined = snap.limits_for(owner_type, owner_id)
            specs.extend(
                _LimitSpec(owner_type, owner_id, ref, k, defined[k]) for k in _PERIODS if k in defined
            )
        return specs

    async def _amort(
        self, conn: asyncpg.Connection, snap: ConfigSnapshot, specs: Sequence[_LimitSpec], now: datetime
    ) -> dict[tuple[str, UUID, PeriodKind], Kop]:
        """Амортизация подписок по лимитам: дни периода по `now` включительно (FR-026, T051).

        M месяца — из `subscription_month` (первый расчёт фиксирует её); прочитанные M кэшируются в процессе.
        """
        today = msk_date(now)
        windows = {(s.owner_type, s.owner_id, s.period): (bounds(s.period, now).start, today) for s in specs}
        return await subs.window_amounts(conn, snap, windows, cache=self._month_totals)

    @staticmethod
    async def _states(
        conn: asyncpg.Connection,
        specs: Sequence[_LimitSpec],
        reserved: dict[Owner, Kop],
        now: datetime,
        amort: Mapping[tuple[str, UUID, PeriodKind], Kop] | None = None,
    ) -> list[LimitState]:
        """Состояние лимитов по FR-026; `amort` — амортизация подписок по (владелец, период) из `_amort`."""
        if not specs:
            return []
        amort = amort or {}
        today = msk_date(now)
        periods = {k: bounds(k, now) for k in {s.period for s in specs}}
        owners = sorted({(s.owner_type, s.owner_id) for s in specs})
        sums = await repo.spent(conn, owners, {k: (p.start, today) for k, p in periods.items()})
        out = []
        for s in specs:
            sp = sums.get((s.owner_type, s.owner_id), {}).get(s.period, repo.Spent())
            out.append(
                LimitState(
                    owner_type="employee" if s.owner_type == "employee" else "department",
                    owner_ref=s.owner_ref,
                    period=periods[s.period],
                    limit=s.limit,
                    spent_token=sp.token,
                    spent_import=sp.import_,
                    amort=amort.get((s.owner_type, s.owner_id, s.period), 0),
                    reserved=reserved.get((s.owner_type, s.owner_id), 0),
                )
            )
        return out

    # ------------------------------------------------------------ решение

    async def decide(self, cmd: DecisionRequest, caller: CallerCtx) -> Decision:
        now = self._clock.now()
        today = msk_date(now)
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, now)
            subj = self._resolve(snap, caller, cmd.subject)
            if subj is False:
                await self._out_of_scope(conn, caller, "createDecision")
            async with conn.transaction():
                if not isinstance(subj, _Subject):
                    verdict = Verdict(
                        Outcome.DENY, None, 0, DenyCode.SUBJECT_UNKNOWN, None, None, None, False, None
                    )
                    dept = (
                        snap.departments.get(caller.scope_department_id)
                        if caller.scope_department_id
                        else None
                    )
                    rate = None
                else:
                    dept = subj.department
                    reserved = await repo.lock_owners(conn, subj.owners)
                    specs = self._limit_specs(snap, subj)
                    amort = await self._amort(conn, snap, specs, now)
                    limits = await self._states(conn, specs, reserved, now, amort)
                    fx = self._fx(snap, today)
                    needs = cmd.needs
                    req = DecisionInput(
                        kind=cmd.kind,
                        input_tokens=cmd.input_tokens,
                        max_output_tokens=cmd.max_output_tokens,
                        tools=needs.tools if needs else False,
                        json_mode=needs.json_mode if needs else False,
                        context_tokens=needs.context_tokens if needs else 0,
                    )
                    try:
                        verdict = select(
                            req,
                            snap.catalog.get(cmd.model),
                            dept.allowed,
                            list(snap.catalog.values()),
                            limits,
                            dept.policy_mode,
                            fx,
                        )
                    except MissingFxRate as e:
                        raise FxRateMissing(
                            f"Нет курса {e.currency}/RUB: администратор должен ввести курс месяца."
                        ) from e
                    final = snap.catalog.get(verdict.model) if verdict.model else None
                    # курс итоговой модели (select пропускает кандидатов без курса, поэтому он есть)
                    rate = None if final is None else fx.get(final.price.currency, _ONE)
                decision = await self._record(conn, caller, cmd, subj, dept, verdict, rate, snap, now)
        metrics.DECISIONS.labels(outcome=verdict.outcome.value, code=verdict.code or "").inc()
        return decision

    async def _record(
        self,
        conn: asyncpg.Connection,
        caller: CallerCtx,
        cmd: DecisionRequest,
        subj: bool | _Subject | None,
        dept: DepartmentCfg | None,
        v: Verdict,
        rate: Decimal | None,
        snap: ConfigSnapshot,
        now: datetime,
    ) -> Decision:
        """Шаг 9: `call_record` для любого исхода; резерв — только allow/redirect, в той же транзакции."""
        decision_id = repo.uuid7(now)
        is_open = v.outcome is not Outcome.DENY
        expires_at = now + self._ttl if is_open else None
        final = snap.catalog.get(v.model) if v.model else None
        employee = subj.employee if isinstance(subj, _Subject) else None
        est = v.estimate
        await repo.insert_call_record(
            conn,
            {
                "id": decision_id,
                "caller_id": caller.id,
                "employee_id": employee.id if employee else None,
                "department_id": dept.id if isinstance(subj, _Subject) and dept else None,
                "requested_model": cmd.model,
                "final_model": v.model,
                "outcome": v.outcome.value,
                "code": v.code.value if v.code else None,
                "price_id": final.price.id if final else None,
                "fx": rate,
                "est_in": est.input_tokens if est else None,
                "est_out": est.output_tokens if est else None,
                "est_source": est.source if est else None,
                "reserve_kop": v.reserve if is_open else 0,
                "flags": _flags(v),
                "status": "open" if is_open else "closed",
                "expense_date": msk_date(now),
                "created_at": now,
                "expires_at": expires_at,
            },
        )
        if is_open and isinstance(subj, _Subject):
            await repo.add_reserved(conn, subj.owners, v.reserve)
        applied = v.applied
        return Decision(
            decision_id=decision_id,
            outcome=v.outcome,
            code=v.code,
            reason=v.reason,
            requested_model=cmd.model,
            model=v.model,
            reserve_rub=kop_to_str(v.reserve if is_open else 0),
            expires_at=_msk(expires_at) if expires_at else None,
            http_status_hint=codes.http_status_hint(v.outcome, v.code),  # type: ignore[arg-type]
            policy_mode=dept.policy_mode if dept else "enforce",  # type: ignore[arg-type]
            fail_mode=dept.fail_mode if dept else self._default_fail_mode,  # type: ignore[arg-type]
            department_id=dept.id if isinstance(subj, _Subject) and dept else None,
            observed_violation=v.observed_violation,
            applied_limit=AppliedLimit(
                owner_type=applied.owner_type,
                owner_ref=applied.owner_ref,
                period=applied.period.kind,
                limit_rub=kop_to_str(applied.limit),
                remaining_rub=kop_to_str(applied.remaining),
                period_start=applied.period.starts_at,
                resets_at=applied.period.resets_at,
            )
            if applied is not None
            else None,
            estimate=EstimateOut(
                input_tokens=est.input_tokens, output_tokens=est.output_tokens, source=est.source
            )
            if est is not None and is_open
            else None,
            budget_blocked_by_subscriptions=v.blocked_by_subs,
            message=codes.message(v, cmd.model),
            created_at=_msk(now),
        )

    # ------------------------------------------------------------ конфигурация интеграции

    async def integration_config(self, caller: CallerCtx) -> IntegrationConfig:
        """FR-006: таймаут, период обновления, fail_mode по умолчанию и подразделений области ключа."""
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, self._clock.now())
        depts = sorted(snap.departments.values(), key=lambda d: str(d.id))
        if caller.scope_type != "any_employee":
            depts = [d for d in depts if d.id == caller.scope_department_id]
        items = [DepartmentFailMode(department_id=d.id, fail_mode=d.fail_mode) for d in depts]  # type: ignore[arg-type]
        payload = {
            "timeout_ms": self._timeout_ms,
            "refresh_s": INTEGRATION_REFRESH_S,
            "default_fail_mode": self._default_fail_mode,
            "departments": [[str(i.department_id), i.fail_mode] for i in items],
        }
        version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        return IntegrationConfig(
            version=version,
            timeout_ms=self._timeout_ms,
            refresh_s=INTEGRATION_REFRESH_S,
            default_fail_mode=self._default_fail_mode,  # type: ignore[arg-type]
            departments=items,
        )

    # ------------------------------------------------------------ расход

    async def settle(
        self, reports: Sequence[UsageReport], caller: CallerCtx
    ) -> list[UsageResult | BudgetError]:
        """Учёт отчётов о расходе (FR-008…FR-011). Каждая строка — своя транзакция: строки независимы.

        Результат строки — `UsageResult` или `BudgetError` (ошибка этой строки). Ответ возвращается
        только после COMMIT строки (SC-003).
        """
        now = self._clock.now()
        out: list[UsageResult | BudgetError] = []
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, now)
            prices: dict[tuple[str, datetime], asyncpg.Record | None] = {}
            for report in reports:
                try:
                    out.append(await self._settle_one(conn, snap, caller, report, now, prices))
                except BudgetError as exc:
                    out.append(exc)
        for r in out:
            metrics.REPORTS.labels(status=r.status if isinstance(r, UsageResult) else "error").inc()
        return out

    async def report(self, report: UsageReport, caller: CallerCtx) -> UsageResult:
        """Один отчёт; ошибка строки поднимается как исключение."""
        (result,) = await self.settle([report], caller)
        if isinstance(result, BudgetError):
            raise result
        return result

    async def _settle_one(
        self,
        conn: asyncpg.Connection,
        snap: ConfigSnapshot,
        caller: CallerCtx,
        report: UsageReport,
        now: datetime,
        prices: dict[tuple[str, datetime], asyncpg.Record | None],
    ) -> UsageResult:
        # Повтор (ключ, report_id) распознаётся раньше проверок:
        # досылка из буфера через 8 суток — дубль, а не 422.
        dup = await repo.reported(conn, caller.id, report.report_id)
        if dup is not None:
            return await self._duplicate(conn, report.report_id, dup)
        if report.decision_id is not None:
            return await self._settle_decision(conn, caller, report.decision_id, report, now)
        return await self._settle_free(conn, snap, caller, report, now, prices)

    async def _duplicate_of(
        self, conn: asyncpg.Connection, caller: CallerCtx, report_id: UUID
    ) -> UsageResult:
        """Дубль по (ключ, report_id): ключ занят параллельной транзакцией, которая уже зафиксирована."""
        record_id = await repo.reported(conn, caller.id, report_id)
        if record_id is None:  # pragma: no cover — ON CONFLICT сработал, значит ключ есть
            raise DecisionNotFound("Запись расхода не найдена.")
        return await self._duplicate(conn, report_id, record_id)

    async def _duplicate(self, conn: asyncpg.Connection, report_id: UUID, record_id: UUID) -> UsageResult:
        row = await repo.get_call_record(conn, record_id)
        if row is None:  # pragma: no cover — report_key всегда ссылается на запись
            raise DecisionNotFound("Запись расхода не найдена.")
        return self._result(row, report_id, duplicate=True)

    @staticmethod
    def _result(row: asyncpg.Record, report_id: UUID, *, duplicate: bool = False) -> UsageResult:
        flags = list(row["flags"])
        if duplicate and "duplicate" not in flags:
            flags.append("duplicate")
        return UsageResult(
            report_id=report_id,
            decision_id=row["id"] if row["outcome"] != "none" else None,
            status="duplicate" if duplicate else "charged",
            amount_rub=kop_to_str(row["fact_kop"] or 0),
            flags=flags,  # type: ignore[arg-type]
            expense_date=row["expense_date"],
        )

    @staticmethod
    def _record_owners(row: asyncpg.Record) -> list[Owner]:
        owners: list[Owner] = []
        if row["department_id"] is not None:
            owners.append(("department", row["department_id"]))
        if row["employee_id"] is not None:
            owners.append(("employee", row["employee_id"]))
        return sorted(owners)

    async def _rate(self, conn: asyncpg.Connection, currency: str, day: date) -> Decimal:
        if currency == "RUB":
            return _ONE
        rate = await repo.fx_at(conn, day.replace(day=1))
        if rate is None:
            raise FxRateMissing(f"Нет курса {currency}/RUB: администратор должен ввести курс месяца.")
        return rate

    async def _settle_decision(
        self,
        conn: asyncpg.Connection,
        caller: CallerCtx,
        decision_id: UUID,
        report: UsageReport,
        now: datetime,
    ) -> UsageResult:
        found = await repo.get_call_record(conn, decision_id)
        if found is None or found["caller_id"] != caller.id or found["outcome"] == "none":
            raise DecisionNotFound("Решение не найдено или принадлежит другой вызывающей системе.")
        owners = self._record_owners(found)
        async with conn.transaction():
            await repo.lock_owners(conn, owners)
            row = await repo.get_call_record(conn, found["id"], for_update=True)
            if row is None:  # pragma: no cover — записи решений не удаляются
                raise DecisionNotFound("Решение не найдено.")
            if row["status"] == "settled":  # факт по этому решению уже учтён (другой report_id)
                return self._result(row, report.report_id, duplicate=True)
            if not await repo.claim_report(conn, caller.id, report.report_id, row["id"], now):
                return await self._duplicate_of(conn, caller, report.report_id)

            flags = list(row["flags"])
            actual = report.actual_model or row["final_model"] or row["requested_model"]
            followed = row["outcome"] != "deny" and actual == row["final_model"]
            price_row: asyncpg.Record | None = None
            if followed:
                price_row = await repo.price_by_id(conn, row["price_id"])
                rate = row["fx"] if row["fx"] is not None else _ONE
            else:
                flags.append("decision_not_followed")
                price_row = await repo.price_at(conn, actual, row["created_at"])
                if price_row is None:
                    raise InvalidReport(f"Модели {actual} нет в каталоге.", code="model_unknown")
                rate = await self._rate(conn, price_row["currency"], row["expense_date"])
            if price_row is None:  # pragma: no cover — история цен неизменяема
                raise InvalidReport("Цена решения не найдена.")
            t = report.tokens
            fact = cost(t.input, t.output, t.cached, _price(price_row), rate)

            if row["status"] == "open":
                await repo.add_reserved(conn, owners, -row["reserve_kop"])
                delta = fact
            elif row["status"] == "expired":  # поздний отчёт заменяет оценку фактом: в реестр — дельта
                delta = fact - (row["fact_kop"] or 0)
            else:  # решение-отказ, вызов всё же сделан
                delta = fact
            await repo.add_ledger(conn, owners, row["expense_date"], delta)
            settled = await repo.settle_call_record(
                conn,
                row["id"],
                row["created_at"],
                fact=fact,
                tokens=(t.input, t.output, t.cached),
                call_status=report.call_status,
                flags=flags,
                now=now,
                final_model=None if followed else actual,
            )
        return self._result(settled, report.report_id)

    async def _settle_free(
        self,
        conn: asyncpg.Connection,
        snap: ConfigSnapshot,
        caller: CallerCtx,
        report: UsageReport,
        now: datetime,
        prices: dict[tuple[str, datetime], asyncpg.Record | None],
    ) -> UsageResult:
        """Отчёт без решения (вызов при fail-open, сценарий 2.6): дата расхода — `occurred_at` по МСК."""
        model, occurred = report.model, report.occurred_at
        if report.subject is None or model is None or occurred is None:
            missing = [f for f in ("subject", "model", "occurred_at") if getattr(report, f) is None]
            raise InvalidReport(
                "Отчёт без decision_id должен содержать subject, model и occurred_at; нет: "
                + ", ".join(missing)
                + "."
            )
        if occurred.tzinfo is None or occurred.utcoffset() is None:
            raise InvalidReport("occurred_at — время с часовым поясом (ISO 8601 со смещением).")
        if occurred < now - REPORT_MAX_AGE:
            raise InvalidReport("Отчёт старше 7 суток не принимается: occurred_at слишком давно.")
        if occurred > now + REPORT_MAX_AHEAD:
            raise InvalidReport("occurred_at в будущем: проверьте часы вызывающей системы.")
        subj = self._resolve(snap, caller, report.subject)
        if subj is False:
            await self._out_of_scope(conn, caller, "reportUsage")
        if not isinstance(subj, _Subject):
            raise InvalidReport(
                "Сотрудник не найден или не состоит ни в одном подразделении.", code="subject_unknown"
            )
        expense_date = msk_date(occurred)
        dept_id = subj.department.id
        if subj.employee is not None:
            dept_id = self._department_on(snap, subj.employee.id, expense_date) or dept_id
        owners: list[Owner] = [("department", dept_id)]
        if subj.employee is not None:
            owners.append(("employee", subj.employee.id))
        owners.sort()

        key = (model, occurred)
        if key not in prices:
            prices[key] = await repo.price_at(conn, model, occurred)
        price_row = prices[key]
        if price_row is None:
            raise InvalidReport(f"Модели {model} нет в каталоге.", code="model_unknown")
        rate = await self._rate(conn, price_row["currency"], expense_date)
        t = report.tokens
        fact = cost(t.input, t.output, t.cached, _price(price_row), rate)
        record_id = repo.uuid7(now)
        async with conn.transaction():
            await repo.lock_owners(conn, owners)
            if not await repo.claim_report(conn, caller.id, report.report_id, record_id, now):
                return await self._duplicate_of(conn, caller, report.report_id)
            row = {
                "id": record_id,
                "caller_id": caller.id,
                "report_id": report.report_id,
                "employee_id": subj.employee.id if subj.employee else None,
                "department_id": dept_id,
                "requested_model": model,
                "final_model": model,
                "outcome": "none",
                "price_id": price_row["id"],
                "fx": rate,
                "fact_kop": fact,
                "tokens_in": t.input,
                "tokens_out": t.output,
                "tokens_cached": t.cached,
                "call_status": report.call_status,
                "flags": ["no_decision"],
                "status": "settled",
                "expense_date": expense_date,
                "created_at": now,
                "settled_at": now,
            }
            await repo.insert_call_record(conn, row)
            await repo.add_ledger(conn, owners, expense_date, fact)
        return UsageResult(
            report_id=report.report_id,
            decision_id=None,
            status="charged",
            amount_rub=kop_to_str(fact),
            flags=["no_decision"],
            expense_date=expense_date,
        )

    @staticmethod
    def _department_on(snap: ConfigSnapshot, employee_id: UUID, day: date) -> UUID | None:
        """Подразделение сотрудника на дату расхода (история назначений): расход до перевода — у старого."""
        for a in snap.assignments:
            if (
                a.employee_id == employee_id
                and a.valid_from <= day
                and (a.valid_to is None or day <= a.valid_to)
            ):
                return a.department_id
        return None

    # ------------------------------------------------------------ истечение резервов

    async def expire_reserves(self, now: datetime) -> int:
        """FR-010: резерв без отчёта дольше срока списывается по оценке с флагом `no_report`.

        Каждый резерв — своя транзакция с тем же порядком блокировок, что у `decide` и `settle`.
        Возвращает число закрытых резервов; повторный вызов ничего не меняет.
        """
        closed = 0
        async with self._pool.acquire() as conn:
            while True:
                due = await repo.due_reserves(conn, now, limit=500)
                for r in due:
                    async with conn.transaction():
                        owners = self._record_owners(r)
                        await repo.lock_owners(conn, owners)
                        row = await repo.get_call_record(conn, r["id"], for_update=True)
                        if row is None or row["status"] != "open":
                            continue  # успел прийти отчёт
                        reserve = row["reserve_kop"]
                        await repo.add_reserved(conn, owners, -reserve)
                        await repo.add_ledger(conn, owners, row["expense_date"], reserve)
                        await repo.expire_call_record(conn, row["id"], row["created_at"], fact=reserve)
                        closed += 1
                if len(due) < 500:
                    break
        if closed:
            log.info("reserves_expired", extra={"count": closed})
        return closed

    # ------------------------------------------------------------ остатки

    async def caller_balance(self, caller: CallerCtx, login: str | None) -> BalanceReport:
        """`getCallerBalance`: остатки сотрудника (`login`) или подразделения ключа со scope=service."""
        now = self._clock.now()
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, now)
            if login is None:
                if caller.scope_type != "service":
                    raise SubjectNotFound("Укажите login сотрудника.")
                subj = self._resolve(snap, caller, None)
            else:
                subj = self._resolve(snap, caller, EmployeeSubject(type="employee", login=login))
            if subj is False:
                await self._out_of_scope(conn, caller, "getCallerBalance")
            if not isinstance(subj, _Subject):
                raise SubjectNotFound("Сотрудник не найден или не состоит ни в одном подразделении.")
            return await self._balance(conn, snap, subj, now)

    async def balance(self, owner: OwnerRef, at: datetime | None = None) -> BalanceReport:
        """Остатки владельца по всем его лимитам (FR-026): сотрудник — его и его подразделения."""
        now = at or self._clock.now()
        async with self._pool.acquire() as conn:
            snap = await self._snapshot(conn, self._clock.now())
            if owner.owner_type == "department":
                dept = snap.departments.get(owner.owner_id)
                if dept is None:
                    raise SubjectNotFound("Подразделение не найдено.")
                subj = _Subject(None, dept)
            else:
                emp = next((e for e in snap.employees_by_login.values() if e.id == owner.owner_id), None)
                if emp is None:
                    raise SubjectNotFound("Сотрудник не найден или не состоит ни в одном подразделении.")
                subj = _Subject(emp, snap.departments[emp.department_id])
            return await self._balance(conn, snap, subj, now)

    async def _balance(
        self, conn: asyncpg.Connection, snap: ConfigSnapshot, subj: _Subject, now: datetime
    ) -> BalanceReport:
        specs = self._limit_specs(snap, subj)
        # M месяца фиксируется до транзакции чтения: она только для чтения, а M неизменяема
        amort = await self._amort(conn, snap, specs, now)
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            reserved = await repo.reserved_of(conn, subj.owners)
            states = await self._states(conn, specs, reserved, now, amort)
        emp = subj.employee
        return BalanceReport(
            as_of=_msk(now),
            owner_type="employee" if emp else "department",
            owner_ref=emp.pseudonym if emp else str(subj.department.id),
            department_id=subj.department.id,
            policy_mode=subj.department.policy_mode,  # type: ignore[arg-type]
            limits=[self._limit_balance(s) for s in states],
        )

    @staticmethod
    def _limit_balance(s: LimitState) -> LimitBalance:
        """14_tech_rulings.md, пакет 2, В1-5: процент и перерасход — по расходу без резервов (T071)."""
        used = s.spent_token + s.spent_import + s.amort
        return LimitBalance(
            owner_type=s.owner_type,
            owner_ref=s.owner_ref,
            period=s.period.kind,
            period_start=s.period.starts_at,
            resets_at=s.period.resets_at,
            limit_rub=kop_to_str(s.limit),
            spent=SpentBySource(
                token_rub=kop_to_str(s.spent_token),
                import_rub=kop_to_str(s.spent_import),
                subscriptions_rub=kop_to_str(s.amort),
            ),
            reserved_rub=kop_to_str(s.reserved),
            remaining_rub=kop_to_str(s.remaining),
            used_pct=_pct(used, s.limit),
            over_limit=used > s.limit,
            subscriptions_share_pct=_pct(s.amort, s.limit),
        )
