"""Принцип IV (T017, ADR-006): ноль исходящих соединений, единственный HTTP-клиент — `bm.egress`.

Первый уровень проверки — autouse-фикстура `egress_guard` в tests/conftest.py: любой `connect`, кроме
PostgreSQL, роняет тест. Второй уровень (счётчик DOCKER-USER на хосте) — `make egress-host-check`, T088.
"""

import socket
from typing import Any

import httpx
import pytest

from bm.egress.client import EgressDisabled, EgressForbidden, get_client
from bm.settings import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"database_url": "postgresql://x@127.0.0.1/x_test", "session_secret": "s" * 32}
    return Settings(_env_file=None, **(base | kw))  # type: ignore[call-arg]


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"host": request.url.host})


# ---------------------------------------------------------------- страж в тестах


def test_guard_blocks_outgoing_connection(expect_egress_attempts: list[Any]) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="исходящее соединение запрещено"):
            s.connect(("203.0.113.10", 443))  # TEST-NET-3, RFC 5737
    finally:
        s.close()
    assert expect_egress_attempts == [("203.0.113.10", 443)]


def test_guard_blocks_connect_ex(expect_egress_attempts: list[Any]) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError):
            s.connect_ex(("198.51.100.7", 80))
    finally:
        s.close()
    assert len(expect_egress_attempts) == 1


async def test_app_startup_and_probes_make_no_egress(
    client: httpx.AsyncClient, egress_guard: list[Any]
) -> None:
    """Полный цикл api при IMPORT_ENABLED=false: старт, healthz, readyz, metrics, вход — только PostgreSQL."""
    for path in ("/healthz", "/readyz", "/metrics"):
        assert (await client.get(path)).status_code == 200
    await client.post("/api/v1/auth/login", json={"login": "x", "password": "y"})
    assert egress_guard == []


# ---------------------------------------------------------------- флаг IMPORT_ENABLED


@pytest.mark.parametrize("purpose", ["anthropic", "openai"])
def test_provider_client_disabled_by_default(purpose: str) -> None:
    with pytest.raises(EgressDisabled):
        get_client(purpose, _settings())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("purpose", "host"), [("anthropic", "api.anthropic.com"), ("openai", "api.openai.com")]
)
async def test_provider_client_only_its_host(purpose: str, host: str) -> None:
    client = get_client(purpose, _settings(import_enabled=True), transport=httpx.MockTransport(_ok))  # type: ignore[arg-type]
    async with client:
        resp = await client.get(f"https://{host}/v1/ping")
        assert resp.json() == {"host": host}
        for other in ("example.com", "api.openai.com.evil.test", "169.254.169.254"):
            if other == host:
                continue
            with pytest.raises(EgressForbidden):
                await client.get(f"https://{other}/")
        bad = "api.openai.com" if purpose == "anthropic" else "api.anthropic.com"
        with pytest.raises(EgressForbidden):
            await client.get(f"https://{bad}/")


async def test_provider_client_https_only() -> None:
    client = get_client("anthropic", _settings(import_enabled=True), transport=httpx.MockTransport(_ok))
    async with client:
        with pytest.raises(EgressForbidden):
            await client.get("http://api.anthropic.com/")


def test_provider_client_ignores_proxy_env_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.1:3128")
    client = get_client("anthropic", _settings(import_enabled=True))
    assert client.follow_redirects is False
    assert client._trust_env is False  # переменные прокси окружения не используются


def test_unknown_purpose_rejected() -> None:
    with pytest.raises(EgressForbidden):
        get_client("telemetry", _settings(import_enabled=True))  # type: ignore[arg-type]


# ---------------------------------------------------------------- webhook — только частные адреса


def test_webhook_disabled_without_url() -> None:
    with pytest.raises(EgressDisabled):
        get_client("webhook", _settings())


@pytest.mark.parametrize(
    "url",
    [
        "http://10.1.2.3/hook",
        "https://172.16.0.5:8443/x",
        "http://172.31.255.254/",
        "http://192.168.10.1/",
        "http://127.0.0.1:9000/hook",
    ],
)
async def test_webhook_private_addresses_allowed(url: str) -> None:
    client = get_client("webhook", _settings(webhook_url=url), transport=httpx.MockTransport(_ok))
    async with client:
        assert (await client.post(url, json={})).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/hook",
        "http://172.32.0.1/",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "http://100.64.0.1/",
        "ftp://10.0.0.1/",
    ],
)
def test_webhook_public_or_odd_addresses_forbidden(url: str) -> None:
    with pytest.raises(EgressForbidden):
        get_client("webhook", _settings(webhook_url=url))


async def test_webhook_client_cannot_reach_other_hosts() -> None:
    client = get_client(
        "webhook", _settings(webhook_url="http://10.0.0.5/hook"), transport=httpx.MockTransport(_ok)
    )
    async with client:
        with pytest.raises(EgressForbidden):
            await client.post("http://10.0.0.6/hook", json={})


def test_webhook_works_without_import_flag() -> None:
    """Флаг импорта не нужен webhook: канал во внутреннюю сеть разрешён конституцией отдельно."""
    get_client("webhook", _settings(webhook_url="http://10.0.0.5/hook", import_enabled=False))
