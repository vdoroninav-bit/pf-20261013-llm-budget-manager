"""Записанный источник расхода: строки задаются заранее, сети нет (тесты конвейера, T060/T061).

`RecordedSource(provider, lines, *, error_class=None)`: `fetch` отдаёт строки с днём в [day_from, day_to];
при `error_class` загрузка завершается сбоем этого класса.
"""

from collections.abc import Iterable
from datetime import date

from bm.provider_import.sources.base import CostLine, ImportFailed, Provider


class RecordedSource:
    def __init__(
        self, provider: Provider, lines: Iterable[CostLine] = (), *, error_class: str | None = None
    ) -> None:
        self.provider: Provider = provider
        self._lines = list(lines)
        self._error = error_class

    async def fetch(self, day_from: date, day_to: date) -> list[CostLine]:
        if self._error is not None:
            raise ImportFailed(self._error, "записанный источник")
        return [x for x in self._lines if day_from <= x.day <= day_to]
