"""Загрузка выгрузки групп AD (FR-012, T041): `import_ad_csv(conn, data, dry_run, actor, now=…)`.

Формат: CSV UTF-8 (BOM допускается), разделитель `;`, заголовок `login;display_name;group;is_primary`,
`is_primary` — `1`, `0` или пусто. Номер строки в сводке — строка файла, заголовок — строка 1.

Правила:
- строка с ошибкой не применяется, остальные применяются; ошибки и предупреждения — по-русски;
- группа AD = подразделение (`department.ad_group`); новое подразделение создаётся в режиме observe;
- основное подразделение сотрудника — строка с `is_primary=1`; без отметки — первая по порядку строка
  (если групп несколько — предупреждение);
- псевдоним `emp-` + 10 случайных hex выдаётся один раз и не меняется при повторных загрузках;
- сотрудник, которого нет в файле, выводится из подразделения: назначение закрывается, `active=false`.
  Личные лимиты и расход за период остаются (сотрудник может вернуться). Логин, который встречается
  только в строках с ошибками, не трогается: ошибка в строке не должна выводить человека из подразделения;
- перевод: старое назначение закрывается вчерашним днём (`valid_to` включительно), новое действует с сегодня;
  назначение, открытое сегодня же, заменяется (история за неполные сутки не нужна: расход события
  привязан к подразделению в момент решения);
- `dry_run` выполняет всё то же в транзакции и откатывает её: цифры сводки совпадают с реальной загрузкой.
"""

import csv
import io
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from bm.config.errors import FileTooLarge, Invalid
from bm.config.service import Applied, Change, apply
from bm.domain.periods import msk_date

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 МиБ, 13_design_rulings.md В6; константа без настройки (принцип VIII)
HEADER = ("login", "display_name", "group", "is_primary")
MAX_NAME = 200  # department.name: length 1..200
_ORG_LOCK = 0x626D_6F72  # «bmor»: одна загрузка за раз


@dataclass(frozen=True, slots=True)
class RowMessage:
    line: int
    message: str


@dataclass(slots=True)
class OrgImportSummary:
    dry_run: bool
    departments_added: int = 0
    employees_added: int = 0
    employees_updated: int = 0
    employees_moved: int = 0
    employees_removed: int = 0
    rows_total: int = 0
    rows_applied: int = 0
    errors: list[RowMessage] = field(default_factory=list)
    warnings: list[RowMessage] = field(default_factory=list)

    def counts(self) -> dict[str, Any]:
        """Для аудита: только числа, без логинов и имён (принцип I)."""
        d = asdict(self)
        d["errors"] = len(self.errors)
        d["warnings"] = len(self.warnings)
        return d


@dataclass(frozen=True, slots=True)
class _Person:
    login: str
    display_name: str | None
    primary_group: str


@dataclass(slots=True)
class _Parsed:
    people: dict[str, _Person]
    groups: list[str]  # все группы из строк без ошибок, в порядке появления
    mentioned: set[str]  # логины из всех строк, включая строки с ошибками
    rows_total: int
    rows_applied: int
    errors: list[RowMessage]
    warnings: list[RowMessage]


def parse(data: bytes) -> _Parsed:
    """Разбор файла. Ошибка формата всего файла — `Invalid` (422); ошибки строк — в сводке."""
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLarge(
            "Файл больше 10 МБ. Выгрузка групп AD обычно гораздо меньше — проверьте, что выбран нужный файл."
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Invalid("Файл не в кодировке UTF-8. Сохраните выгрузку как CSV UTF-8.") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";")
    header = next(reader, None)
    if header is None or tuple(h.strip().lower() for h in header) != HEADER:
        raise Invalid("Первая строка файла должна быть заголовком: login;display_name;group;is_primary.")

    errors: list[RowMessage] = []
    warnings: list[RowMessage] = []
    mentioned: set[str] = set()
    seen_pairs: dict[tuple[str, str], int] = {}
    primary_line: dict[str, int] = {}
    rows: dict[str, list[tuple[int, str, bool, str | None]]] = {}
    groups: dict[str, None] = {}
    total = 0
    for raw in reader:
        line = reader.line_num
        if not raw or all(not c.strip() for c in raw):
            continue  # пустые строки не считаются
        total += 1
        if len(raw) != len(HEADER):
            errors.append(RowMessage(line, f"Ожидается 4 поля через «;», в строке {len(raw)}."))
            login0 = raw[0].strip()
            if login0:
                mentioned.add(login0)
            continue
        login, display_name, group, primary = (c.strip() for c in raw)
        if login:
            mentioned.add(login)
        if not login:
            errors.append(RowMessage(line, "Не указан логин сотрудника."))
            continue
        if not group:
            errors.append(RowMessage(line, "Не указана группа AD (подразделение)."))
            continue
        if len(group) > MAX_NAME:
            errors.append(RowMessage(line, f"Название группы длиннее {MAX_NAME} символов."))
            continue
        if primary not in ("", "0", "1"):
            errors.append(RowMessage(line, "Признак основной группы должен быть 1, 0 или пусто."))
            continue
        if (login, group) in seen_pairs:
            errors.append(
                RowMessage(
                    line,
                    f"Повтор: сотрудник уже указан в группе «{group}» в строке {seen_pairs[login, group]}.",
                )
            )
            continue
        is_primary = primary == "1"
        if is_primary and login in primary_line:
            errors.append(
                RowMessage(line, f"Основная группа сотрудника уже отмечена в строке {primary_line[login]}.")
            )
            continue
        seen_pairs[login, group] = line
        if is_primary:
            primary_line[login] = line
        rows.setdefault(login, []).append((line, group, is_primary, display_name or None))
        groups[group] = None

    people: dict[str, _Person] = {}
    for login, items in rows.items():
        chosen = next((it for it in items if it[2]), None)
        if chosen is None:
            chosen = items[0]
            if len(items) > 1:
                warnings.append(
                    RowMessage(
                        chosen[0],
                        "Основная группа не отмечена: сотрудник отнесён к первой по порядку "
                        f"группе «{chosen[1]}».",
                    )
                )
        name = chosen[3] or next((it[3] for it in items if it[3]), None)
        people[login] = _Person(login, name, chosen[1])
    rows_applied = sum(len(v) for v in rows.values())
    errors.sort(key=lambda m: m.line)
    warnings.sort(key=lambda m: m.line)
    return _Parsed(people, list(groups), mentioned, total, rows_applied, errors, warnings)


async def _new_pseudonym_employee(conn: asyncpg.Connection, person: _Person) -> UUID:
    for _ in range(10):
        emp_id: UUID | None = await conn.fetchval(
            "INSERT INTO employee(pseudonym, ad_login, display_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (pseudonym) DO NOTHING RETURNING id",
            f"emp-{secrets.token_hex(5)}",
            person.login,
            person.display_name,
        )
        if emp_id is not None:
            return emp_id
    raise RuntimeError(
        "не удалось выдать уникальный псевдоним"
    )  # 10 совпадений из 16^10 — практически невозможно


async def _close(conn: asyncpg.Connection, assignment_id: int, valid_from: date, today: date) -> None:
    if valid_from < today:
        await conn.execute(
            "UPDATE employee_assignment SET valid_to = $2 WHERE id = $1",
            assignment_id,
            today - timedelta(days=1),
        )
    else:
        await conn.execute("DELETE FROM employee_assignment WHERE id = $1", assignment_id)


async def _assign(conn: asyncpg.Connection, employee_id: UUID, department_id: UUID, today: date) -> None:
    await conn.execute(
        "INSERT INTO employee_assignment(employee_id, department_id, valid_from) VALUES ($1, $2, $3)",
        employee_id,
        department_id,
        today,
    )


async def _apply_rows(
    conn: asyncpg.Connection, parsed: _Parsed, summary: OrgImportSummary, today: date
) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock($1)", _ORG_LOCK)
    departments: dict[str, UUID] = {
        r["ad_group"]: r["id"]
        for r in await conn.fetch(
            "SELECT id, ad_group FROM department WHERE ad_group = ANY($1::text[])", parsed.groups
        )
    }
    for group in parsed.groups:
        if group not in departments:
            departments[group] = await conn.fetchval(
                "INSERT INTO department(name, ad_group) VALUES ($1, $1) RETURNING id", group
            )
            summary.departments_added += 1

    existing = {
        r["ad_login"]: r
        for r in await conn.fetch(
            "SELECT e.id, e.ad_login, e.display_name, e.active, "
            "a.id AS assignment_id, a.department_id, a.valid_from "
            "FROM employee e LEFT JOIN employee_assignment a ON a.employee_id = e.id AND a.valid_to IS NULL "
            "FOR UPDATE OF e"
        )
    }
    for login, person in parsed.people.items():
        target = departments[person.primary_group]
        row = existing.get(login)
        if row is None:
            emp_id = await _new_pseudonym_employee(conn, person)
            await _assign(conn, emp_id, target, today)
            summary.employees_added += 1
            continue
        renamed = person.display_name is not None and person.display_name != row["display_name"]
        if renamed or not row["active"]:
            await conn.execute(
                "UPDATE employee SET display_name = coalesce($2, display_name), active = true WHERE id = $1",
                row["id"],
                person.display_name,
            )
            summary.employees_updated += 1
        if row["assignment_id"] is None:  # вернулся после вывода из подразделения
            await _assign(conn, row["id"], target, today)
        elif row["department_id"] != target:
            await _close(conn, row["assignment_id"], row["valid_from"], today)
            await _assign(conn, row["id"], target, today)
            summary.employees_moved += 1

    for login, row in existing.items():
        if login in parsed.mentioned or row["assignment_id"] is None:
            continue
        await _close(conn, row["assignment_id"], row["valid_from"], today)
        await conn.execute("UPDATE employee SET active = false WHERE id = $1", row["id"])
        summary.employees_removed += 1


class _DryRunRollback(Exception):
    pass


async def import_ad_csv(
    conn: asyncpg.Connection, data: bytes, dry_run: bool, actor: str, *, now: datetime
) -> OrgImportSummary:
    parsed = parse(data)
    summary = OrgImportSummary(
        dry_run=dry_run,
        rows_total=parsed.rows_total,
        rows_applied=parsed.rows_applied,
        errors=parsed.errors,
        warnings=parsed.warnings,
    )
    today = msk_date(now)
    if dry_run:
        try:
            async with conn.transaction():
                await _apply_rows(conn, parsed, summary, today)
                raise _DryRunRollback
        except _DryRunRollback:
            pass
        return summary

    async def run(c: asyncpg.Connection) -> Applied:
        await _apply_rows(c, parsed, summary, today)
        return Applied(object_id=None, after=summary.counts(), result=summary)

    await apply(conn, Change("import", "org", run), actor, at=now)
    return summary
