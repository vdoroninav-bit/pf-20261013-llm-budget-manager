"""Курсы USD/₽ по месяцам: `listFxRates`, `putFxRate` (FR-019, T042)."""

from fastapi import APIRouter, Path

from bm.config import catalog as service
from bm.http.deps import AdminSession, ClockDep, Conn
from bm.http.schemas_admin import FxRate, FxRateList, FxRatePut

router = APIRouter(prefix="/fx-rates", tags=["fx"])


@router.get("", operation_id="listFxRates")
async def list_fx_rates(session: AdminSession, conn: Conn) -> FxRateList:
    items = await service.list_fx(conn)
    return FxRateList.model_validate({"items": items, "total": len(items)})


@router.put("/{month}", operation_id="putFxRate")
async def put_fx_rate(
    body: FxRatePut,
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    month: str = Path(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
) -> FxRate:
    saved = await service.put_fx(conn, session.principal.login, month, body.usd_rub, clock.now())
    return FxRate.model_validate(saved)
