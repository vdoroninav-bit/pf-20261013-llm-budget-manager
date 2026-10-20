"""T061 (14_tech_rulings.md Б5): ключ уведомления без периода — `period_start` обязателен."""

from typing import Any

import pytest

from bm.notify.service import create_once


async def test_create_once_rejects_missing_period_start() -> None:
    conn: Any = None  # до запроса в БД дело не доходит
    with pytest.raises(ValueError, match="period_start"):
        await create_once(conn, "import_failure", None, None, None, "Импорт не удался 2 раза подряд.")  # type: ignore[arg-type]
