"""Кэш `integration-config` и `fail_mode` по субъекту на диске (FR-006, 12_arch_decision.md, вопрос 9).

Нужен, чтобы при недоступности утилиты — в том числе сразу после перезапуска процесса вызывающей
системы — клиент знал, что делать: fail-open или fail-closed.

Источники fail_mode субъекта, по убыванию приоритета:
1. подразделение субъекта (из последнего решения) и более свежая из двух записей — последнее решение
   или `integration-config` для этого подразделения;
2. ключ со scope=service: его единственное подразделение из `integration-config`;
3. `default_fail_mode` из `integration-config`;
4. `open` — принцип III (по умолчанию fail-open), если утилита ни разу не ответила.

Файл пишется атомарно (временный файл + `os.replace`) и только при изменении. Ключ API в файл не пишется.
"""

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_FAIL_MODE = "open"
DEFAULT_REFRESH_S = 60
FAIL_MODES = ("open", "closed")


def subject_key(subject: dict[str, Any]) -> str:
    if subject.get("type") == "employee":
        return f"employee:{subject.get('login', '')}"
    return str(subject.get("type", ""))


class ConfigCache:
    def __init__(self, path: Path, clock: Callable[[], float]) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"config": None, "etag": None, "fetched_at": None, "subjects": {}}
        self._load()

    # ------------------------------------------------------------ integration-config

    @property
    def etag(self) -> str | None:
        with self._lock:
            return self._state["etag"]

    @property
    def refresh_s(self) -> float:
        with self._lock:
            cfg = self._state["config"] or {}
        value = cfg.get("refresh_s")
        return float(value) if isinstance(value, int) and value > 0 else float(DEFAULT_REFRESH_S)

    def is_stale(self) -> bool:
        with self._lock:
            fetched = self._state["fetched_at"]
        return fetched is None or self._clock() - fetched >= self.refresh_s

    def apply_config(self, config: dict[str, Any], etag: str | None) -> None:
        if config.get("default_fail_mode") not in FAIL_MODES or not isinstance(
            config.get("departments"), list
        ):
            raise ValueError("некорректная integration-config")
        with self._lock:
            self._state.update(config=config, etag=etag, fetched_at=self._clock())
            self._save()

    def touch(self) -> None:
        """Ответ 304: конфигурация не изменилась, отсчёт 60 с начинается заново."""
        with self._lock:
            self._state["fetched_at"] = self._clock()
            self._save()

    # ------------------------------------------------------------ fail_mode по субъекту

    def remember(self, subject: dict[str, Any], department_id: str | None, fail_mode: str) -> None:
        """Запомнить fail_mode из ответа утилиты. Диск пишется только при изменении."""
        if fail_mode not in FAIL_MODES:
            return
        key = subject_key(subject)
        with self._lock:
            old = self._state["subjects"].get(key)
            entry = {"department_id": department_id, "fail_mode": fail_mode, "at": self._clock()}
            self._state["subjects"][key] = entry
            if old is None or (old["department_id"], old["fail_mode"]) != (department_id, fail_mode):
                self._save()

    def fail_mode_for(self, subject: dict[str, Any]) -> str:
        with self._lock:
            cfg = self._state["config"]
            fetched = self._state["fetched_at"] or 0.0
            entry = self._state["subjects"].get(subject_key(subject))
        depts = {
            d.get("department_id"): d.get("fail_mode")
            for d in (cfg or {}).get("departments", [])
            if d.get("fail_mode") in FAIL_MODES
        }
        if entry is not None:
            from_cfg = depts.get(entry["department_id"])
            if from_cfg is not None and fetched > entry["at"]:
                return str(from_cfg)
            return str(entry["fail_mode"])
        if cfg is None:
            return DEFAULT_FAIL_MODE
        if subject.get("type") == "service" and len(depts) == 1:
            return str(next(iter(depts.values())))
        return str(cfg["default_fail_mode"])

    # ------------------------------------------------------------ диск

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # нет файла или он повреждён — начинаем с пустого кэша (fail-open по умолчанию)
        if isinstance(data, dict) and isinstance(data.get("subjects"), dict):
            self._state.update({k: data.get(k) for k in ("config", "etag", "fetched_at")})
            self._state["subjects"] = data["subjects"]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
