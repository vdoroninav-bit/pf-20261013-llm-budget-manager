"""Эталонный клиент Decision API (FR-006, FR-011, принцип III, SC-002; 12_plan.md §6).

Схема интеграции вызывающей стороны:

    d = client.decide(subject, model, kind, input_tokens=..., max_output_tokens=...)
    # PolicyUnavailable → утилита недоступна, а политика субъекта fail-closed: вызов модели отклонить
    # d.outcome == "deny"  → отказ по бюджету или модели (это ответ утилиты, а не сбой: ADR-005)
    # d.fail_open          → утилита не ответила за 50 мс, вызов выполняется без решения
    ... вызов модели d.model ...
    client.report(decision_id=d.decision_id, tokens={...}, call_status="ok",
                  report_id=..., subject=subject, model=d.model)

- `decide()` ждёт ответа не дольше `timeout_ms` (50 мс). Сбой (таймаут, нет соединения, 5xx, 429,
  испорченный ответ) — по `fail_mode` субъекта из кэша на диске: `open` → разрешение с
  `fail_open=True`, `closed` → `PolicyUnavailable`. Ошибки запроса (401, 403, 404, 422) — это
  ошибка интеграции, а не сбой утилиты: поднимается `ClientRequestError`, иначе fail-open молча
  скрыл бы, например, отозванный ключ.
- `report()` сначала надёжно пишет отчёт в буфер на диске, затем фон досылает его пакетом.
- `flush()` досылает буфер пакетами по 500 с экспоненциальной паузой при сбое.
- Фон (поток-демон): раз в `refresh_s` (60 с) обновляет `integration-config`, досылает буфер.

Сеть — только к адресу `base_url`, без прокси окружения и без телеметрии. Зависимостей вне stdlib нет.
"""

import hashlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from budget_client.buffer import ReportBuffer
from budget_client.config_cache import ConfigCache
from budget_client.transport import HttpResponse, Transport, TransportError

log = logging.getLogger("budget_client")

DEFAULT_TIMEOUT_MS = 50
BATCH_SIZE = 500
PAUSE_BASE_S = 0.5
PAUSE_MAX_S = 30.0
FLUSH_RETRIES = 5
BACKGROUND_PAUSE_MAX_S = 60.0
COALESCE_S = 0.05

_REPORT_FIELDS = frozenset({"report_id", "subject", "model", "occurred_at", "actual_model"})
_FAILURE_STATUSES = frozenset({408, 429})


class BudgetClientError(Exception):
    """Базовое исключение клиента."""


class PolicyUnavailable(BudgetClientError):
    """Утилита недоступна, политика субъекта — fail-closed: вызов модели нужно отклонить."""

    code = "policy_unavailable"

    def __init__(
        self, message: str = "Утилита бюджетов недоступна, политика подразделения — fail-closed."
    ) -> None:
        super().__init__(message)


class ClientRequestError(BudgetClientError):
    """Утилита отклонила запрос (401/403/404/422): ошибка интеграции или ключа, не сбой."""

    def __init__(self, status: int, code: str | None, detail: str | None) -> None:
        super().__init__(f"{status} {code or ''}: {detail or ''}".strip())
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ClientDecision:
    outcome: str  # allow | redirect | deny
    fail_open: bool
    decision_id: str | None
    model: str | None  # модель для вызова (redirect — новая; deny — None)
    requested_model: str
    code: str | None = None
    http_status_hint: int = 200
    fail_mode: str | None = None
    department_id: str | None = None
    message: str | None = None
    raw: dict[str, Any] | None = None


class BudgetClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        state_dir: Path,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        *,
        background: bool = True,
        flush_timeout_s: float = 10.0,
        config_timeout_s: float = 2.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms должен быть > 0")
        self._http = Transport(base_url, api_key)
        self.timeout_ms = timeout_ms
        self._flush_timeout_s = flush_timeout_s
        self._config_timeout_s = config_timeout_s
        self._clock = clock
        self._sleep = sleep
        # Файлы состояния различаются по отпечатку ключа: один state_dir можно делить между ключами.
        # Сам ключ на диск не пишется.
        fp = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        state = Path(state_dir)
        state.mkdir(parents=True, exist_ok=True)
        self._cache = ConfigCache(state / f"config-{fp}.json", clock)
        self._buffer = ReportBuffer(state / f"buffer-{fp}.sqlite3")
        self._flush_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if background:
            self._thread = threading.Thread(target=self._background, name="budget_client", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------ решение

    def decide(self, subject: dict[str, Any], model: str, kind: str, **kw: Any) -> ClientDecision:
        """Решение утилиты; при сбое — fail-open или `PolicyUnavailable` по fail_mode субъекта."""
        body = {"subject": subject, "model": model, "kind": kind, **kw}
        try:
            resp = self._http.request("POST", "/api/v1/decisions", self.timeout_ms / 1000, body)
        except TransportError as exc:
            return self._on_failure(subject, model, exc.args[0] if exc.args else "transport")
        if resp.status == 200:
            try:
                return self._decision(subject, model, resp.json())
            except (ValueError, KeyError, TypeError):
                return self._on_failure(subject, model, "invalid_response")
        if 400 <= resp.status < 500 and resp.status not in _FAILURE_STATUSES:
            problem = _problem(resp)
            raise ClientRequestError(resp.status, problem.get("code"), problem.get("detail"))
        return self._on_failure(subject, model, f"http_{resp.status}")

    def _decision(self, subject: dict[str, Any], model: str, data: Any) -> ClientDecision:
        if not isinstance(data, dict) or data.get("outcome") not in ("allow", "redirect", "deny"):
            raise ValueError("некорректный ответ")
        fail_mode = data.get("fail_mode")
        if isinstance(fail_mode, str):
            self._cache.remember(subject, data.get("department_id"), fail_mode)
        return ClientDecision(
            outcome=data["outcome"],
            fail_open=False,
            decision_id=data["decision_id"],
            model=data.get("model"),
            requested_model=data.get("requested_model", model),
            code=data.get("code"),
            http_status_hint=int(data.get("http_status_hint", 200)),
            fail_mode=fail_mode,
            department_id=data.get("department_id"),
            message=data.get("message"),
            raw=data,
        )

    def _on_failure(self, subject: dict[str, Any], model: str, reason: str) -> ClientDecision:
        mode = self._cache.fail_mode_for(subject)
        log.warning("budget_client: утилита не ответила (%s), fail_mode=%s", reason, mode)
        if mode == "closed":
            raise PolicyUnavailable()
        return ClientDecision(
            outcome="allow",
            fail_open=True,
            decision_id=None,
            model=model,
            requested_model=model,
            fail_mode=mode,
        )

    # ------------------------------------------------------------ отчёт о расходе

    def report(self, *, decision_id: str | None, tokens: dict[str, Any], call_status: str, **kw: Any) -> str:
        """Записать отчёт в буфер на диске (надёжно, до возврата) и разбудить фоновую досылку.

        kw: report_id (задаёт вызывающий; нет — UUID4), subject, model, occurred_at, actual_model.
        Без decision_id обязательны subject и model (вызов при fail-open). Возвращает report_id.
        """
        unknown = set(kw) - _REPORT_FIELDS
        if unknown:
            raise TypeError(f"неизвестные поля отчёта: {sorted(unknown)}")
        if decision_id is None and (kw.get("subject") is None or kw.get("model") is None):
            raise ValueError("отчёт без decision_id требует subject и model")
        report_id = str(kw.pop("report_id", None) or uuid.uuid4())
        body: dict[str, Any] = {"report_id": report_id, "tokens": tokens, "call_status": call_status}
        if decision_id is not None:
            body["decision_id"] = decision_id
        body.update({k: v for k, v in kw.items() if v is not None})
        # время вызова, а не досылки: расход относится к дню вызова
        body.setdefault("occurred_at", datetime.now(UTC).isoformat())
        self._buffer.put(report_id, body)
        self._wake.set()
        return report_id

    def flush(self, max_batch: int = BATCH_SIZE, max_retries: int = FLUSH_RETRIES) -> int:
        """Дослать буфер пакетами по `max_batch`. Возвращает число принятых сервером отчётов.

        При сбое пакета — пауза 0,5 с, 1 с, 2 с… (не больше 30 с) и повтор, не больше `max_retries` раз
        подряд; затем выход, неотправленное остаётся в буфере. Отчёты, записанные во время flush(),
        досылаются следующим вызовом (иначе flush() под потоком мог бы не закончиться).
        """
        if not 1 <= max_batch <= 1000:
            raise ValueError("max_batch: от 1 до 1000 (ограничение API)")
        with self._flush_lock:
            return self._flush_locked(max_batch, max_retries, pause=True)

    def _flush_locked(self, max_batch: int, max_retries: int, *, pause: bool) -> int:
        delivered = 0
        failures = 0
        upto = self._buffer.last_seq()
        while True:
            rows = self._buffer.take(max_batch, upto)
            if not rows:
                return delivered
            n, ok = self._send_batch(rows)
            delivered += n
            if ok:
                failures = 0
                continue
            if failures >= max_retries or not pause:
                return delivered
            self._sleep(min(PAUSE_BASE_S * 2**failures, PAUSE_MAX_S))
            failures += 1

    def _send_batch(self, rows: list[tuple[str, dict[str, Any]]]) -> tuple[int, bool]:
        """Отправить пакет. Возвращает (принято строк, пакет прошёл без временных ошибок)."""
        try:
            resp = self._http.request(
                "POST", "/api/v1/usage/batch", self._flush_timeout_s, {"items": [b for _, b in rows]}
            )
        except TransportError as exc:
            log.warning("budget_client: досылка не удалась (%s), в буфере остаётся", exc)
            return 0, False
        results = resp.json() if resp.status == 200 else None
        if not isinstance(results, dict) or not isinstance(results.get("results"), list):
            log.warning("budget_client: досылка не удалась (http_%s)", resp.status)
            return 0, False
        by_index = {r.get("index"): r for r in results["results"] if isinstance(r, dict)}
        acked: list[str] = []
        permanent: dict[str, str] = {}
        transient: dict[str, str] = {}
        for i, (rid, _) in enumerate(rows):
            r = by_index.get(i)
            if r is None:
                transient[rid] = "нет результата строки"
            elif r.get("ok") is True:
                acked.append(rid)
            else:
                err = r.get("error") or {}
                status = err.get("status") if isinstance(err, dict) else None
                text = f"{status} {err.get('code', '')}" if isinstance(err, dict) else str(err)
                if isinstance(status, int) and 400 <= status < 500 and status not in _FAILURE_STATUSES:
                    permanent[rid] = text
                else:
                    transient[rid] = text
        self._buffer.ack(acked)
        self._buffer.fail(permanent, permanent=True)
        self._buffer.fail(transient, permanent=False)
        if permanent:
            log.error(
                "budget_client: сервер отклонил %d отчётов окончательно (см. rejected())", len(permanent)
            )
        return len(acked), not transient

    def pending(self) -> int:
        """Сколько отчётов ждут досылки."""
        return self._buffer.pending_count()

    def rejected(self) -> list[tuple[str, str]]:
        """Отчёты, окончательно отклонённые сервером: (report_id, ошибка)."""
        return self._buffer.rejected()

    # ------------------------------------------------------------ integration-config

    def config_stale(self) -> bool:
        return self._cache.is_stale()

    def refresh_config(self) -> bool:
        """Обновить integration-config (If-None-Match). False — утилита не ответила, кэш прежний."""
        etag = self._cache.etag
        headers = {"If-None-Match": etag} if etag else None
        try:
            resp = self._http.request(
                "GET", "/api/v1/integration-config", self._config_timeout_s, headers=headers
            )
            if resp.status == 304:
                self._cache.touch()
                return True
            if resp.status == 200:
                self._cache.apply_config(resp.json(), resp.headers.get("etag"))
                return True
        except (TransportError, ValueError, TypeError) as exc:
            log.warning("budget_client: integration-config не обновлена (%s)", type(exc).__name__)
            return False
        log.warning("budget_client: integration-config не обновлена (http_%s)", resp.status)
        return False

    # ------------------------------------------------------------ фон и завершение

    def _background(self) -> None:
        pause = 0.0
        next_flush = 0.0
        while not self._stop.is_set():
            self._wake.clear()
            if self._cache.is_stale():
                self.refresh_config()
            if time.monotonic() >= next_flush and self._buffer.pending_count():
                self._stop.wait(COALESCE_S)  # собрать отчёты, пришедшие почти одновременно, в один пакет
                if self._flush_lock.acquire(blocking=False):
                    try:
                        self._flush_locked(BATCH_SIZE, 0, pause=False)
                    finally:
                        self._flush_lock.release()
                if self._buffer.pending_count():
                    pause = min(max(pause * 2, PAUSE_BASE_S), BACKGROUND_PAUSE_MAX_S)
                    next_flush = time.monotonic() + pause
                else:
                    pause = 0.0
            wait = self._cache.refresh_s
            if next_flush > time.monotonic():
                wait = min(wait, next_flush - time.monotonic())
            self._wake.wait(timeout=wait)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=self._flush_timeout_s + self._config_timeout_s + 1)
        self._http.close()
        self._buffer.close()

    def __enter__(self) -> "BudgetClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _problem(resp: HttpResponse) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
