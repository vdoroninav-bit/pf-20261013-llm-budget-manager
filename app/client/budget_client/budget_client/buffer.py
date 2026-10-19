"""Буфер отчётов о расходе на диске (FR-011, принцип III).

SQLite из stdlib, `PRAGMA synchronous=FULL` и WAL: после возврата `put()` запись переживает падение
процесса и отключение питания. Ключ — `report_id`: повтор того же отчёта в буфер не дублируется,
а сервер распознаёт повтор досылки по паре (ключ, report_id).

Строка живёт в одном из состояний:
- `pending`  — ждёт досылки;
- `rejected` — сервер отклонил строку окончательно (4xx строки). Хранится для разбора оператором
  и не блокирует досылку остальных.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  TEXT    NOT NULL UNIQUE,
    body       TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    state      TEXT    NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'rejected')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS report_pending ON report (state, seq);
"""


class ReportBuffer:
    """Потокобезопасный буфер: одно соединение SQLite под замком (вызовы короткие)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # isolation_level=None: транзакции явно, каждый put — отдельный COMMIT с fsync
        self._conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)

    def pragma(self, name: str) -> Any:
        if name not in {"synchronous", "journal_mode"}:
            raise ValueError(name)
        with self._lock:
            return self._conn.execute(f"PRAGMA {name}").fetchone()[0]

    def put(self, report_id: str, body: dict[str, Any]) -> bool:
        """Записать отчёт. False — отчёт с таким report_id уже в буфере (не перезаписывается)."""
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO report (report_id, body, created_at) VALUES (?, ?, ?)",
                (report_id, data, time.time()),
            )
            return cur.rowcount == 1

    def last_seq(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT coalesce(max(seq), 0) FROM report").fetchone()[0])

    def take(self, limit: int, max_seq: int | None = None) -> list[tuple[str, dict[str, Any]]]:
        """До `limit` ожидающих отчётов в порядке записи (не удаляет их)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT report_id, body FROM report"
                " WHERE state = 'pending' AND seq <= ? ORDER BY seq LIMIT ?",
                (max_seq if max_seq is not None else 2**62, limit),
            ).fetchall()
        return [(rid, json.loads(body)) for rid, body in rows]

    def ack(self, report_ids: list[str]) -> None:
        """Сервер подтвердил (charged или duplicate) — строки удаляются."""
        if not report_ids:
            return
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany("DELETE FROM report WHERE report_id = ?", [(r,) for r in report_ids])
            self._conn.execute("COMMIT")

    def fail(self, errors: dict[str, str], *, permanent: bool) -> None:
        """Отметить попытку: временная ошибка — строка остаётся pending, окончательная — rejected."""
        if not errors:
            return
        state = "rejected" if permanent else "pending"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(
                "UPDATE report SET attempts = attempts + 1, last_error = ?, state = ? WHERE report_id = ?",
                [(err[:500], state, rid) for rid, err in errors.items()],
            )
            self._conn.execute("COMMIT")

    def pending_count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT count(*) FROM report WHERE state = 'pending'").fetchone()[0]
            )

    def rejected(self) -> list[tuple[str, str]]:
        """Окончательно отклонённые сервером строки: (report_id, последняя ошибка)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT report_id, coalesce(last_error, '') FROM report WHERE state = 'rejected' ORDER BY seq"
            ).fetchall()
        return [(r, e) for r, e in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
