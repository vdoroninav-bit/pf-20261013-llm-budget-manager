"""Лимиты владельца бюджета в ₽ на день, неделю, месяц (`limit_def`, схема `LimitSet`): null — лимита нет."""

from collections.abc import Iterable, Mapping
from uuid import UUID

import asyncpg

from bm.domain.money import kop_to_str

PERIODS = ("day", "week", "month")

type LimitSet = dict[str, str | None]


def empty() -> LimitSet:
    return dict.fromkeys(PERIODS)


async def read_many(
    conn: asyncpg.Connection, owner_type: str, owner_ids: Iterable[UUID]
) -> dict[UUID, LimitSet]:
    ids = list(owner_ids)
    result: dict[UUID, LimitSet] = {i: empty() for i in ids}
    if not ids:
        return result
    rows = await conn.fetch(
        "SELECT owner_id, period, amount_kop FROM limit_def "
        "WHERE owner_type = $1 AND owner_id = ANY($2::uuid[])",
        owner_type,
        ids,
    )
    for r in rows:
        result[r["owner_id"]][r["period"]] = kop_to_str(r["amount_kop"])
    return result


async def read(conn: asyncpg.Connection, owner_type: str, owner_id: UUID) -> LimitSet:
    return (await read_many(conn, owner_type, [owner_id]))[owner_id]


async def write(
    conn: asyncpg.Connection, owner_type: str, owner_id: UUID, values: Mapping[str, int | None]
) -> None:
    """Заменяет все три лимита: None удаляет лимит периода. Вызывается внутри транзакции `config.apply`."""
    for period in PERIODS:
        amount = values.get(period)
        if amount is None:
            await conn.execute(
                "DELETE FROM limit_def WHERE owner_type = $1 AND owner_id = $2 AND period = $3",
                owner_type,
                owner_id,
                period,
            )
        else:
            await conn.execute(
                "INSERT INTO limit_def(owner_type, owner_id, period, amount_kop) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (owner_type, owner_id, period) DO UPDATE SET amount_kop = EXCLUDED.amount_kop",
                owner_type,
                owner_id,
                period,
                amount,
            )
