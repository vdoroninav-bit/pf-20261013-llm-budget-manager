"""T096 (14_tech_rulings.md, пакет 4, 4Д6): процесс api раздаёт собранную админку по /admin/.

- каталог — параметр `create_app(admin_dir=…)`, по умолчанию `/app/static/admin` (туда его кладёт образ);
- каталог есть → `StaticFiles(html=True)` на /admin: `/admin/` — index.html, ассеты по `base: '/admin/'`;
  `/admin` → `/admin/`; отсутствующий файл → 404 (маршруты SPA хешевые, подмена на index.html не нужна);
- файлы вне каталога не отдаются, в том числе по `..` в сыром пути запроса;
- каталога нет → монтирования нет, одна строка info в журнале, API работает;
- `/api/*`, `/healthz`, `/readyz`, `/metrics` не затронуты; контракт (app.openapi()) статику не содержит.
"""

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock

pytestmark = pytest.mark.db

INDEX = '<!doctype html><html><body><div id="root"></div><script src="/admin/assets/app.js"></script></body></html>'
APP_JS = "console.log('admin');"
SECRET = "SESSION_SECRET=не-отдавать"


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    """tmp/admin — каталог сборки; tmp/secret.txt и tmp/bm/settings.py лежат рядом, вне каталога."""
    admin = tmp_path / "admin"
    (admin / "assets").mkdir(parents=True)
    (admin / "index.html").write_text(INDEX, encoding="utf-8")
    (admin / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    (tmp_path / "bm").mkdir()
    (tmp_path / "bm" / "settings.py").write_text(SECRET, encoding="utf-8")
    return tmp_path


async def _started(settings: Any, clock: FrozenClock, admin_dir: Path) -> AsyncIterator[Any]:
    from bm.main import create_app

    application = create_app(settings, clock=clock, admin_dir=admin_dir)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def static_app(
    db: asyncpg.Connection, settings: Any, clock: FrozenClock, static_root: Path
) -> AsyncIterator[Any]:
    async for application in _started(settings, clock, static_root / "admin"):
        yield application


@pytest.fixture
async def bare_app(
    db: asyncpg.Connection, settings: Any, clock: FrozenClock, tmp_path: Path
) -> AsyncIterator[Any]:
    async for application in _started(settings, clock, tmp_path / "missing"):
        yield application


def _client(application: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://localhost:8080")


async def _raw_get(application: Any, raw_path: bytes) -> tuple[int, bytes]:
    """GET в обход нормализации URL в httpx: путь передаётся в ASGI как есть (`..`, `%2e%2e`)."""
    from urllib.parse import unquote_to_bytes

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "server": ("localhost", 8080),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
        "path": unquote_to_bytes(raw_path).decode("utf-8"),
        "raw_path": raw_path,
        "query_string": b"",
        "headers": [(b"host", b"localhost:8080")],
    }
    status = 0
    body = b""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, body
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    await application(scope, receive, send)
    return status, body


# ---------------------------------------------------------------- каталог есть


async def test_index_served_as_html(static_app: Any) -> None:
    async with _client(static_app) as c:
        r = await c.get("/admin/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in r.text


async def test_asset_served_by_base_path(static_app: Any) -> None:
    async with _client(static_app) as c:
        r = await c.get("/admin/assets/app.js")
    assert r.status_code == 200
    assert r.text == APP_JS


async def test_admin_without_slash_redirects(static_app: Any) -> None:
    async with _client(static_app) as c:
        r = await c.get("/admin")
        assert r.status_code in (307, 308)
        assert httpx.URL(r.headers["location"]).path == "/admin/"
        followed = await c.get("/admin", follow_redirects=True)
    assert followed.status_code == 200 and '<div id="root">' in followed.text


async def test_missing_file_is_404_not_index(static_app: Any) -> None:
    async with _client(static_app) as c:
        r = await c.get("/admin/nope.js")
        deep = await c.get("/admin/departments/1")
    assert r.status_code == 404
    assert deep.status_code == 404  # маршруты SPA хешевые: /admin/#/departments/1


async def test_dot_dot_via_client_is_404(static_app: Any) -> None:
    async with _client(static_app) as c:
        r = await c.get("/admin/../bm/settings.py")
    assert r.status_code == 404
    assert SECRET not in r.text


@pytest.mark.parametrize(
    "raw",
    [
        b"/admin/../secret.txt",
        b"/admin/../bm/settings.py",
        b"/admin/%2e%2e/secret.txt",
        b"/admin/assets/../../secret.txt",
        b"/admin/..%2fsecret.txt",
    ],
)
async def test_files_outside_dir_not_served(static_app: Any, raw: bytes) -> None:
    status, body = await _raw_get(static_app, raw)
    assert status == 404, (raw, status)
    assert SECRET.encode() not in body


async def test_api_and_system_untouched(static_app: Any) -> None:
    async with _client(static_app) as c:
        health = await c.get("/healthz")
        ready = await c.get("/readyz")
        metrics = await c.get("/metrics")
        me = await c.get("/api/v1/auth/me")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert ready.status_code == 200 and ready.json()["db"] == "ok"
    assert metrics.status_code == 200
    assert me.status_code == 401
    assert me.headers["content-type"].startswith("application/problem+json")  # ошибка API, а не статика


def test_static_mount_not_in_contract_schema(static_root: Path, settings: Any) -> None:
    """Монтирование статики — не операция API: в схеме приложения (её сверяет T015) путей /admin нет."""
    from bm.main import create_app

    application = create_app(settings, admin_dir=static_root / "admin")
    paths = application.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/admin")]


# ---------------------------------------------------------------- каталога нет


async def test_no_dir_no_mount_api_works(bare_app: Any) -> None:
    async with _client(bare_app) as c:
        admin = await c.get("/admin/")
        health = await c.get("/healthz")
    assert admin.status_code == 404
    assert health.status_code == 200


def test_no_dir_logs_one_info_line(tmp_path: Path, settings: Any, caplog: pytest.LogCaptureFixture) -> None:
    from bm.main import create_app

    missing = tmp_path / "missing"
    with caplog.at_level(logging.INFO, logger="bm"):
        create_app(settings, admin_dir=missing)
    lines = [r for r in caplog.records if "admin" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO


def test_default_dir_is_image_path() -> None:
    import inspect

    from bm import main

    assert main.ADMIN_STATIC_DIR == Path("/app/static/admin")
    assert inspect.signature(main.create_app).parameters["admin_dir"].default == main.ADMIN_STATIC_DIR
