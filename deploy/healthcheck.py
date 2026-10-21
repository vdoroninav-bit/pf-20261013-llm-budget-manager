"""Healthcheck контейнеров api и worker (T087). Только stdlib + asyncpg из образа.

  python /app/healthcheck.py api     — GET /readyz: БД отвечает, применена последняя миграция образа
  python /app/healthcheck.py worker  — PID 1 — это `python -m bm.worker`, и БД отвечает на SELECT 1

Healthcheck запускается через `docker exec` и не видит переменных, которые выставил entrypoint, поэтому
пароль БД читается здесь же из POSTGRES_PASSWORD_FILE.
"""

import asyncio
import os
import sys
import urllib.request
from pathlib import Path


def check_api() -> int:
    # адрес — константа loopback внутри контейнера
    with urllib.request.urlopen("http://127.0.0.1:8080/readyz", timeout=3) as r:
        return 0 if r.status == 200 else 1


async def _db_ok() -> bool:
    import asyncpg

    pw_file = os.environ.get("POSTGRES_PASSWORD_FILE", "")
    password = (
        Path(pw_file).read_text(encoding="utf-8").strip() if pw_file and Path(pw_file).is_file() else None
    )
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], password=password, timeout=5)
    try:
        return await conn.fetchval("SELECT 1") == 1
    finally:
        await conn.close()


def check_worker() -> int:
    cmdline = Path("/proc/1/cmdline").read_bytes().split(b"\0")
    if b"bm.worker" not in cmdline:
        print(f"PID 1 не worker: {cmdline!r}", file=sys.stderr)
        return 1
    return 0 if asyncio.run(_db_ok()) else 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "api"
    try:
        return {"api": check_api, "worker": check_worker}[mode]()
    except Exception as e:
        print(f"healthcheck {mode}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
