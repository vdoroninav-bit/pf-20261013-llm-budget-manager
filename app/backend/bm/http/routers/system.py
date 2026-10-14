"""Системные операции: `healthz`, `readyz`, `metrics` (без аутентификации)."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from bm import __version__, metrics
from bm.db.migrate import available, current_version

router = APIRouter(tags=["system"])


@router.get("/healthz", operation_id="healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz", operation_id="readyz")
async def readyz(request: Request) -> JSONResponse:
    """Готов, если БД отвечает и применена последняя миграция из образа."""
    latest = available()[-1][0] if available() else None
    try:
        async with request.app.state.pool.acquire(timeout=2) as conn:
            applied = await current_version(conn)
    except Exception:
        return JSONResponse({"status": "fail", "db": "fail", "version": __version__}, status_code=503)
    body = {"status": "ok", "db": "ok", "version": __version__, "migrations": applied or "none"}
    if applied is None or applied != latest:
        body["status"] = "fail"
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)


@router.get("/metrics", operation_id="metrics")
async def get_metrics() -> Response:
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)
