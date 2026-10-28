"""Запуск «Бюджет-менеджера LLM» из Mac-приложения (deploy/macos). Только стандартная библиотека.

Двойной клик по приложению:
  1. при первом запуске создаёт ~/Library/Application Support/LLM Budget Manager: config.env с секретами
     и базу PostgreSQL (initdb);
  2. запускает PostgreSQL (127.0.0.1, порт PG_PORT), api (`bm_appimage.serve`, админка из приложения) и worker;
  3. открывает админку в браузере и показывает окно «работает» с кнопками; «Остановить» — штатная остановка.
Если приложение уже запущено (порт API отвечает /readyz), только открывает админку.
Процесс упал — останавливает остальное и показывает ошибку с путём к журналам.
LLM_BUDGET_HOME — другой каталог данных; LLM_BUDGET_HEADLESS=1 — без окна и браузера (CI), остановка по SIGTERM.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

TITLE = "Бюджет-менеджер LLM"
RES = Path(__file__).resolve().parent
PY = RES / "python" / "bin" / "python3.13"
PGBIN = RES / "pg" / "pgserver" / "pginstall" / "bin"
ADMIN_DIR = RES / "admin"
SITE = RES / "site-packages"
LIBDIR = RES / "lib"
VERSION = (RES / "VERSION").read_text().strip() if (RES / "VERSION").exists() else "dev"

DATA = Path(
    os.environ.get("LLM_BUDGET_HOME")
    or Path.home() / "Library" / "Application Support" / "LLM Budget Manager"
)
CONF = DATA / "config.env"
PGDATA = DATA / "pgdata"
LOGS = DATA / "logs"
RUN = DATA / "run"

CONF_TEMPLATE = """# Настройки «Бюджет-менеджера LLM» (Mac, {version}). Создано {now}.
# Секреты сгенерированы при первом запуске. Файл не передавайте и не кладите в git.
# После правки перезапустите приложение. Переменные — как в deploy/.env.example (docs/admin-guide.md §4).
BIND_ADDR=127.0.0.1
API_PORT=8080
PG_PORT=55432
UVICORN_WORKERS=2
POSTGRES_PASSWORD={pg}
SESSION_SECRET={session}
SESSION_COOKIE_SECURE=false
ADMIN_INITIAL_LOGIN=admin
ADMIN_INITIAL_PASSWORD={admin}
LOG_LEVEL=INFO
# Импорт фактического расхода (docs/admin-guide.md §8)
IMPORT_ENABLED=false
ANTHROPIC_ADMIN_KEY=
OPENAI_ADMIN_KEY=
IMPORT_HTTPS_PROXY=
# Уведомления
WEBHOOK_URL=
"""


def osa_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dialog_cmd(
    text: str, buttons: list[str], default: str, icon: str = "note"
) -> list[str]:
    btns = "{" + ", ".join(osa_quote(b) for b in buttons) + "}"
    script = (
        f"display dialog {osa_quote(text)} with title {osa_quote(TITLE)} buttons {btns} "
        f"default button {osa_quote(default)} with icon {icon}"
    )
    return ["/usr/bin/osascript", "-e", script]


def alert(text: str) -> None:
    if os.environ.get("LLM_BUDGET_HEADLESS"):
        print(text, file=sys.stderr, flush=True)
        return
    subprocess.run(
        dialog_cmd(text, ["OK"], "OK", "stop"), capture_output=True, check=False
    )


def fail(text: str) -> None:
    alert(f"{text}\n\nЖурналы: {LOGS}")
    sys.exit(1)


def read_conf() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in CONF.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def ensure_conf() -> bool:
    for d in (DATA, LOGS, RUN):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    if CONF.exists():
        return False
    text = CONF_TEMPLATE.format(
        version=VERSION,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
        pg=secrets.token_hex(24),
        session=secrets.token_hex(32),
        admin=secrets.token_hex(12),
    )
    fd = os.open(CONF, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return True


def ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/readyz", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - любая ошибка соединения = «не готов»
        return False


def port_busy(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pg(
    cmd: str, *args: str, env: dict[str, str], log: str | None = None
) -> subprocess.CompletedProcess:
    out = open(LOGS / log, "a") if log else subprocess.PIPE  # noqa: SIM115
    try:
        return subprocess.run(
            [str(PGBIN / cmd), *args],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    finally:
        if log:
            out.close()


def main() -> None:
    first = ensure_conf()
    conf = read_conf()
    api_port = int(conf.get("API_PORT") or 8080)
    pg_port = int(conf.get("PG_PORT") or 55432)
    bind = conf.get("BIND_ADDR") or "127.0.0.1"
    url = f"http://127.0.0.1:{api_port}"

    if ready(url):  # уже запущено — только открыть админку
        subprocess.run(["/usr/bin/open", url + "/admin/"], check=False)
        return
    if port_busy(api_port):
        fail(
            f"Порт {api_port} занят другой программой.\nЗадайте другой API_PORT в файле:\n{CONF}"
        )

    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update({k: v for k, v in conf.items() if v != ""})
    env.update(
        PGPASSWORD=conf["POSTGRES_PASSWORD"],
        DATABASE_URL=f"postgresql://bm@127.0.0.1:{pg_port}/bm",
        PYTHONPATH=f"{LIBDIR}:{SITE}",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        BM_APPIMAGE_ADMIN_DIR=str(ADMIN_DIR),
        LC_ALL="C",
    )

    # --- PostgreSQL
    if not (PGDATA / "PG_VERSION").exists():
        pw = DATA / ".pw"
        fd = os.open(pw, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(conf["POSTGRES_PASSWORD"] + "\n")
        r = pg(
            "initdb",
            "-D",
            str(PGDATA),
            "-U",
            "bm",
            f"--pwfile={pw}",
            "--auth-local=scram-sha-256",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
            env=env,
            log="initdb.log",
        )
        pw.unlink(missing_ok=True)
        if r.returncode != 0:
            fail("Не удалось создать базу данных (initdb).")
        with open(PGDATA / "postgresql.conf", "a") as f:
            f.write(
                "\n# LLM Budget Manager (Mac)\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = ''\n"
                "timezone = 'UTC'\nmax_connections = 100\n"
            )

    procs: list[subprocess.Popen] = []
    dialog: subprocess.Popen | None = None

    def stop_all() -> None:
        if dialog and dialog.poll() is None:
            dialog.terminate()
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        pg("pg_ctl", "-D", str(PGDATA), "-m", "fast", "-w", "stop", env=env)

    def on_signal(signum, frame):  # выход из системы, kill
        stop_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    if pg("pg_ctl", "-D", str(PGDATA), "status", env=env).returncode != 0:
        if port_busy(pg_port):
            fail(
                f"Порт {pg_port} занят другой программой.\nЗадайте другой PG_PORT в файле:\n{CONF}"
            )
        r = pg(
            "pg_ctl",
            "-D",
            str(PGDATA),
            "-o",
            f"-p {pg_port}",
            "-l",
            str(LOGS / "postgres.log"),
            "-w",
            "-t",
            "60",
            "start",
            env=env,
        )
        if r.returncode != 0:
            fail("PostgreSQL не запустился (см. postgres.log).")
    q = pg(
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        str(pg_port),
        "-U",
        "bm",
        "-d",
        "postgres",
        "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='bm'",
        env=env,
    )
    if (
        q.stdout.strip() != "1"
        and pg(
            "createdb", "-h", "127.0.0.1", "-p", str(pg_port), "-U", "bm", "bm", env=env
        ).returncode
        != 0
    ):
        stop_all()
        fail("Не удалось создать базу bm.")

    # --- api и worker
    metrics = RUN / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    for f in metrics.iterdir():
        f.unlink()
    api_env = dict(env, PROMETHEUS_MULTIPROC_DIR=str(metrics))
    with open(LOGS / "api.log", "a") as log:
        procs.append(
            subprocess.Popen(
                [
                    str(PY),
                    "-m",
                    "bm_appimage.serve",
                    "--host",
                    bind,
                    "--port",
                    str(api_port),
                ],
                env=api_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
    deadline = time.monotonic() + 120
    while not ready(url):
        if procs[0].poll() is not None or time.monotonic() > deadline:
            stop_all()
            fail("Сервис не запустился (см. api.log).")
        time.sleep(1)
    with open(LOGS / "worker.log", "a") as log:
        procs.append(
            subprocess.Popen(
                [str(PY), "-m", "bm.worker"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )

    if os.environ.get(
        "LLM_BUDGET_HEADLESS"
    ):  # CI и проверки: без окна и браузера, остановка по SIGTERM
        print(f"ready {url}", flush=True)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        stop_all()
        sys.exit(1)

    subprocess.run(["/usr/bin/open", url + "/admin/"], check=False)

    text = f"Работает. Админка: {url}/admin/\n\n"
    buttons = ["Остановить", "Открыть админку"]
    if first:
        text += (
            f"Первый вход: логин {conf.get('ADMIN_INITIAL_LOGIN', 'admin')}, "
            f"пароль {conf['ADMIN_INITIAL_PASSWORD']}\n(система попросит сменить пароль)\n\n"
        )
        buttons.insert(1, "Скопировать пароль")
    text += f"Данные и настройки: {DATA}\n\nЗакрыть это окно — «Остановить»."

    while True:
        dialog = subprocess.Popen(
            dialog_cmd(text, buttons, "Открыть админку"),
            stdout=subprocess.PIPE,
            text=True,
        )
        crashed = None
        while dialog.poll() is None:
            for name, p in zip(("api", "worker"), procs):
                if p.poll() is not None:
                    crashed = name
            if crashed:
                break
            time.sleep(1)
        if crashed:
            stop_all()
            fail(f"Процесс {crashed} завершился, приложение остановлено.")
        answer = (dialog.stdout.read() if dialog.stdout else "").strip()
        if "Открыть админку" in answer:
            subprocess.run(["/usr/bin/open", url + "/admin/"], check=False)
        elif "Скопировать пароль" in answer:
            subprocess.run(
                ["/usr/bin/pbcopy"],
                input=conf["ADMIN_INITIAL_PASSWORD"],
                text=True,
                check=False,
            )
        else:  # «Остановить» или окно закрыто
            stop_all()
            return


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / "launcher.log", "a") as f:
            import traceback

            traceback.print_exc(file=f)
        alert(f"Ошибка запуска: {e}\n\nПодробности: {LOGS / 'launcher.log'}")
        sys.exit(1)
