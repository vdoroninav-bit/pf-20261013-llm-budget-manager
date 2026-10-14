"""Вход в админку: `login`, `logout`, `me`, `changePassword` (FR-030)."""

from fastapi import APIRouter, Response

from bm.access import auth
from bm.domain.periods import MSK
from bm.http.deps import SESSION_COOKIE, AdminSession, ClockDep, Conn, SettingsDep
from bm.http.errors import ProblemError
from bm.http.schemas import AdminUser, LoginRequest, PasswordChange, SessionOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _session_out(s: auth.Session) -> SessionOut:
    return SessionOut(user=AdminUser.of(s.principal), csrf_token=s.csrf_token)


@router.post("/login", operation_id="login")
async def login(
    body: LoginRequest, response: Response, conn: Conn, clock: ClockDep, settings: SettingsDep
) -> SessionOut:
    now = clock.now()
    try:
        result = await auth.login(
            conn, body.login, body.password, now=now, secret=settings.session_secret.get_secret_value()
        )
    except auth.LoginLocked as exc:
        retry = max(1, int((exc.until - now).total_seconds()))
        until = exc.until.astimezone(MSK).strftime("%H:%M")
        raise ProblemError(
            429,
            "login_locked",
            f"Слишком много неудачных попыток. Вход заблокирован до {until} МСК.",
            headers={"Retry-After": str(retry)},
        ) from exc
    except auth.InvalidCredentials as exc:
        raise ProblemError(401, "invalid_credentials", "Неверный логин или пароль.") from exc
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,  # по умолчанию API слушает 127.0.0.1 по HTTP; HTTPS — на обратном прокси заказчика
    )
    return _session_out(result.session)


@router.post("/logout", operation_id="logout", status_code=204)
async def logout(session: AdminSession, conn: Conn) -> Response:
    await auth.logout(conn, session)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return response


@router.get("/me", operation_id="me")
async def me(session: AdminSession) -> SessionOut:
    return _session_out(session)


@router.post("/password", operation_id="changePassword", status_code=204)
async def change_password(
    body: PasswordChange, session: AdminSession, conn: Conn, clock: ClockDep
) -> Response:
    try:
        await auth.change_password(conn, session, body.old_password, body.new_password, now=clock.now())
    except auth.InvalidCredentials as exc:
        raise ProblemError(422, "invalid_credentials", "Текущий пароль указан неверно.") from exc
    return Response(status_code=204)
