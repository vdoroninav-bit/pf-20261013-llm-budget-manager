"""Единственный исходящий HTTP-клиент (принцип IV, ADR-006). Остальным пакетам `httpx`, `urllib`,
`http.client` и `socket` запрещены import-linter'ом (app/backend/importlinter.ini).

Правила:
- `anthropic`, `openai` — только при IMPORT_ENABLED=true, только HTTPS и только свой хост
  (`api.anthropic.com` / `api.openai.com`);
- `webhook` — только если WEBHOOK_URL задан и его хост — адрес из 10/8, 172.16/12, 192.168/16 или 127/8
  (имя хоста резолвится, все адреса должны быть частными); запросы — только на этот хост и порт;
- редиректы не выполняются, переменные окружения прокси игнорируются (`trust_env=False`): иначе запрос
  мог бы уйти на хост вне списка;
- прокси импорта (14_tech_rulings.md, пакет 2, Б4): если задан IMPORT_HTTPS_PROXY, у клиентов `anthropic`
  и `openai` — явный `proxy=` (туннель CONNECT к тем же двум хостам, белый список не меняется). К `webhook`
  прокси не применяется никогда. При IMPORT_ENABLED=false клиент не создаётся — соединений нет, и с прокси
  тоже.

Ограничение: имя хоста webhook проверяется при создании клиента; повторный резолв при соединении
(DNS rebinding) не контролируется — для v1 рекомендуем указывать в WEBHOOK_URL IP-адрес.
"""

import ipaddress
import socket
from typing import Literal
from urllib.parse import urlsplit

import httpx

from bm.settings import Settings

type Purpose = Literal["anthropic", "openai", "webhook"]

# Типы httpx для коннекторов импорта: пакетам вне bm.egress импорт httpx запрещён (import-linter), поэтому
# клиент, транспорт для тестов и класс сетевых ошибок они получают отсюда.
Client = httpx.AsyncClient
Transport = httpx.AsyncBaseTransport
TransportError = httpx.TransportError

PROVIDER_HOSTS: dict[str, str] = {"anthropic": "api.anthropic.com", "openai": "api.openai.com"}
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class EgressDisabled(RuntimeError):
    """Исходящий канал выключен конфигурацией (IMPORT_ENABLED=false или WEBHOOK_URL пуст)."""


class EgressForbidden(RuntimeError):
    """Адрес вне разрешённого списка."""


def _is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and any(ip in net for net in PRIVATE_NETWORKS)


def _resolve(host: str) -> set[str]:
    try:
        ipaddress.ip_address(host)
        return {host}
    except ValueError:
        pass
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise EgressForbidden(f"не удалось разрешить имя хоста webhook {host!r}") from exc


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _host_guard(scheme_allowed: frozenset[str], host: str, port: int | None):  # type: ignore[no-untyped-def]
    async def check(request: httpx.Request) -> None:
        url = request.url
        if url.scheme not in scheme_allowed:
            raise EgressForbidden(f"схема {url.scheme!r} запрещена для исходящих запросов")
        if url.host != host:
            raise EgressForbidden(f"хост {url.host!r} вне списка разрешённых")
        if port is not None and (url.port or _default_port(url.scheme)) != port:
            raise EgressForbidden(f"порт {url.port!r} вне списка разрешённых")

    return check


def get_client(
    purpose: Purpose, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """HTTP-клиент для цели `purpose`. `transport` — для тестов (httpx.MockTransport с фикстурами): он
    заменяет весь сетевой стек, поэтому прокси при нём не монтируется (иначе httpx ушёл бы мимо подмены)."""
    proxy: str | None = None
    if purpose in PROVIDER_HOSTS:
        if not settings.import_enabled:
            raise EgressDisabled("импорт расхода провайдеров выключен (IMPORT_ENABLED=false)")
        hook = _host_guard(frozenset({"https"}), PROVIDER_HOSTS[purpose], None)
        base_url = f"https://{PROVIDER_HOSTS[purpose]}"
        if settings.import_https_proxy is not None and transport is None:
            proxy = settings.import_https_proxy.get_secret_value().strip()
    elif purpose == "webhook":
        if not settings.webhook_url:
            raise EgressDisabled("внешний канал уведомлений выключен (WEBHOOK_URL пуст)")
        parts = urlsplit(settings.webhook_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise EgressForbidden("WEBHOOK_URL должен быть http(s)://<адрес во внутренней сети>/…")
        addresses = _resolve(parts.hostname)
        if not addresses or not all(_is_private(a) for a in addresses):
            raise EgressForbidden(
                "WEBHOOK_URL должен вести во внутреннюю сеть (10/8, 172.16/12, 192.168/16, 127/8)"
            )
        port = parts.port or _default_port(parts.scheme)
        hook = _host_guard(frozenset({parts.scheme}), parts.hostname, port)
        base_url = f"{parts.scheme}://{parts.netloc}"
    else:
        raise EgressForbidden(f"неизвестная цель исходящих запросов {purpose!r}")
    return httpx.AsyncClient(
        base_url=base_url,
        transport=transport,
        proxy=proxy,
        trust_env=False,
        follow_redirects=False,
        timeout=DEFAULT_TIMEOUT,
        event_hooks={"request": [hook]},
    )
