"""Общие параметры списков админки (`Page`, `PageSize`, `Sort`, `Order` контракта)."""

from typing import Annotated, Literal

from fastapi import Query

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]
Sort = Annotated[str | None, Query()]
Order = Annotated[Literal["asc", "desc"], Query()]
