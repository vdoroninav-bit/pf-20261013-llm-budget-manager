"""Вход по локальным учётным записям админки (FR-030), сессии и первый администратор.

- Пароли — argon2id (argon2-cffi, параметры по умолчанию RFC 9106 low-memory).
- Сессия — случайный токен в cookie `bm_session`; в БД хранится HMAC-SHA256(SESSION_SECRET, токен),
  поэтому дамп БД не даёт войти. CSRF-токен сессии сверяется на изменяющих запросах (bm.http.deps).
- Блокировка: 10 неудачных попыток подряд → вход закрыт на 15 минут (`429 login_locked`),
  даже с верным паролем.
- Первый администратор создаётся из ADMIN_INITIAL_LOGIN / ADMIN_INITIAL_PASSWORD с must_change_password=true.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from bm.access import audit
from bm.access.authorize import Principal
from bm.access.permissions import Role

SESSION_TTL = timedelta(hours=12)
MAX_FAILED_ATTEMPTS = 10
LOCK_DURATION = timedelta(minutes=15)

_hasher = PasswordHasher()  # тип по умолчанию — argon2id


class InvalidCredentials(Exception):
    """Неверный логин или пароль, либо учётная запись выключена."""


class LoginLocked(Exception):
    def __init__(self, until: datetime) -> None:
        super().__init__(f"вход заблокирован до {until.isoformat()}")
        self.until = until


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def token_hash(secret: str, token: str) -> bytes:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class Session:
    principal: Principal
    csrf_token: str
    token_hash: bytes
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    session: Session
    token: str  # отдаётся только в cookie, в БД не хранится


def _principal(row: asyncpg.Record) -> Principal:
    return Principal(
        user_id=row["id"],
        login=row["login"],
        role=Role(row["role"]),
        department_id=row["department_id"],
        must_change_password=row["must_change_password"],
    )


_USER_COLS = (
    "id, login, role, department_id, must_change_password, active, argon2_hash, failed_attempts, locked_until"
)


async def login(
    conn: asyncpg.Connection, login_name: str, password: str, *, now: datetime, secret: str
) -> LoginResult:
    """Проверяет пароль и создаёт сессию. Счётчик неудач фиксируется даже при отказе."""
    async with conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_USER_COLS} FROM admin_user WHERE login = $1 FOR UPDATE",  # noqa: S608 — константа
            login_name,
        )
        if row is None or not row["active"]:
            verify_password(_dummy_hash(), password)  # одинаковое время ответа
            raise InvalidCredentials
        locked_until: datetime | None = row["locked_until"]
        if locked_until is not None and locked_until > now:
            raise LoginLocked(locked_until)
        if not verify_password(row["argon2_hash"], password):
            failed = (0 if locked_until is not None else row["failed_attempts"]) + 1
            if failed >= MAX_FAILED_ATTEMPTS:
                until = now + LOCK_DURATION
                await conn.execute(
                    "UPDATE admin_user SET failed_attempts = 0, locked_until = $2 WHERE id = $1",
                    row["id"],
                    until,
                )
                await audit.write(
                    conn,
                    actor=login_name,
                    action="login_locked",
                    object_type="admin_user",
                    object_id=str(row["id"]),
                    after={"locked_until": until.isoformat()},
                    at=now,
                )
            else:
                await conn.execute(
                    "UPDATE admin_user SET failed_attempts = $2, locked_until = NULL WHERE id = $1",
                    row["id"],
                    failed,
                )
            failed_login = True
        else:
            failed_login = False
            new_hash = hash_password(password) if _hasher.check_needs_rehash(row["argon2_hash"]) else None
            await conn.execute(
                "UPDATE admin_user SET failed_attempts = 0, locked_until = NULL, "
                "argon2_hash = coalesce($2, argon2_hash) WHERE id = $1",
                row["id"],
                new_hash,
            )
            token = secrets.token_urlsafe(32)
            session = Session(
                principal=_principal(row),
                csrf_token=secrets.token_urlsafe(32),
                token_hash=token_hash(secret, token),
                expires_at=now + SESSION_TTL,
            )
            await conn.execute(
                "INSERT INTO session(token_sha256, user_id, csrf_token, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                session.token_hash,
                row["id"],
                session.csrf_token,
                now,
                session.expires_at,
            )
            await audit.write(
                conn,
                actor=login_name,
                action="login",
                object_type="admin_user",
                object_id=str(row["id"]),
                at=now,
            )
    if failed_login:
        raise InvalidCredentials
    return LoginResult(session=session, token=token)


async def resolve_session(
    conn: asyncpg.Connection, token: str, *, now: datetime, secret: str
) -> Session | None:
    """Действующая сессия по токену из cookie или None."""
    th = token_hash(secret, token)
    row = await conn.fetchrow(
        "SELECT u.id, u.login, u.role, u.department_id, u.must_change_password, s.csrf_token, s.expires_at "
        "FROM session s JOIN admin_user u ON u.id = s.user_id "
        "WHERE s.token_sha256 = $1 AND s.expires_at > $2 AND u.active",
        th,
        now,
    )
    if row is None:
        return None
    return Session(
        principal=_principal(row), csrf_token=row["csrf_token"], token_hash=th, expires_at=row["expires_at"]
    )


async def logout(conn: asyncpg.Connection, session: Session) -> None:
    await conn.execute("DELETE FROM session WHERE token_sha256 = $1", session.token_hash)


async def change_password(
    conn: asyncpg.Connection, session: Session, old_password: str, new_password: str, *, now: datetime
) -> None:
    """Смена своего пароля: остальные сессии пользователя закрываются, текущая остаётся."""
    user_id = session.principal.user_id
    async with conn.transaction():
        current = await conn.fetchval("SELECT argon2_hash FROM admin_user WHERE id = $1 FOR UPDATE", user_id)
        if current is None or not verify_password(current, old_password):
            raise InvalidCredentials
        await conn.execute(
            "UPDATE admin_user SET argon2_hash = $2, must_change_password = false WHERE id = $1",
            user_id,
            hash_password(new_password),
        )
        await conn.execute(
            "DELETE FROM session WHERE user_id = $1 AND token_sha256 <> $2", user_id, session.token_hash
        )
        await audit.write(
            conn,
            actor=session.principal.login,
            action="change_password",
            object_type="admin_user",
            object_id=str(user_id),
            at=now,
        )


_BOOTSTRAP_LOCK = 0x626D_6164  # «bmad»


async def ensure_initial_admin(
    conn: asyncpg.Connection, login_name: str, password: str | None, *, now: datetime
) -> UUID | None:
    """Создаёт первого администратора, если пользователей админки нет. Возвращает id созданного или None."""
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _BOOTSTRAP_LOCK)
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM admin_user)"):
            return None
        if not password:
            raise RuntimeError(
                "в БД нет пользователей админки, а ADMIN_INITIAL_PASSWORD не задан — "
                "задайте его в deploy/.env"
            )
        user_id: UUID = await conn.fetchval(
            "INSERT INTO admin_user(login, argon2_hash, role, must_change_password) "
            "VALUES ($1, $2, 'admin', true) RETURNING id",
            login_name,
            hash_password(password),
        )
        await audit.write(
            conn,
            actor=audit.SYSTEM_ACTOR,
            action="create",
            object_type="admin_user",
            object_id=str(user_id),
            after={"login": login_name, "role": "admin", "must_change_password": True},
            at=now,
        )
        return user_id
