#!/usr/bin/env python3
"""Аннотации GitHub Actions для гейтов CI (T092; 14_tech_rulings.md, пакет 4, 4Д1, 4Д3). Только stdlib.

Зачем: журнал прогона и артефакты GitHub отдаёт через blob-хранилище, а из среды оркестратора доступны только
API статусов шагов и аннотаций check-run. Поэтому причина падения должна попадать в аннотацию.

  gha.py run --title <шаг> [--stack] -- <команда...>
      Выполняет команду, вывод (stdout+stderr) идёт в журнал как обычно и в <GHA_STATE_DIR>/<шаг>.log.
      Код выхода — как у команды. При падении: `::error title=<шаг> (exit N)::<хвост вывода>`; если в выводе
      есть сводка pytest («short test summary info») — она вместо хвоста. --stack: ещё одна аннотация
      «<шаг>: состояние стека» — `compose ps` и хвост журналов сервисов, которые не running/healthy.
  gha.py notice --title <заголовок>     текст из stdin → `::notice`
  gha.py summary [--out deploy/ci/out]  сводка: шаги (STEPS_JSON = toJSON(steps)), гейты, sha256 SBOM,
                                        строки FAIL из summary.txt make check → одна `::notice`
  gha.py trivy [--pkg certifi ...] --json <trivy.json> [--json ...]
      Источник находок Trivy (T099, 5Д3) → одна `::notice` по всем отчётам (`trivy image --format json
      --list-all-pkgs`): для каждого отчёта — число CRITICAL; для каждой CRITICAL — Target, Class, Type,
      PkgPath, FilePath (из Packages по имени и версии); все установки пакетов --pkg и уязвимых пакетов
      (версия, Target, FilePath) или «не найден». Нет или битый отчёт — строка в той же notice.
      Код выхода всегда 0: гейт — шаг make check.

Ограничения: ≤ 4 КБ текста на аннотацию (UTF-8, обрезается по символам), ≤ 10 аннотаций на задачу (счётчик
в GHA_STATE_DIR), из них 2 оставлены под notice. Секреты: значения переменных SECRET_KEYS из GHA_SECRETS_FILE
(deploy/.env) и из окружения печатаются раннеру как `::add-mask::` и заменяются на *** в тексте аннотации;
пароль в URL (`scheme://user:pass@`) тоже заменяется.

Переменные: GHA_STATE_DIR (deploy/ci/out/gha), GHA_SECRETS_FILE (deploy/.env),
BM_COMPOSE (env -u DATABASE_URL docker compose -f deploy/docker-compose.yml --env-file deploy/.env).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

LIMIT_BYTES = 4000
MAX_ANNOTATIONS = 10
RESERVED_FOR_NOTICES = 2
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "SESSION_SECRET",
    "ADMIN_INITIAL_PASSWORD",
    "BM_ADMIN_PASSWORD",
    "ANTHROPIC_ADMIN_KEY",
    "OPENAI_ADMIN_KEY",
    "IMPORT_HTTPS_PROXY",
)
DEFAULT_COMPOSE = "env -u DATABASE_URL docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
PYTEST_SUMMARY = "short test summary info"
URL_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")


# ---------------------------------------------------------------- форматирование


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_prop(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def tail_utf8(text: str, limit: int = LIMIT_BYTES) -> str:
    """Последние ≤ limit байт текста в UTF-8, без разрыва символа; при обрезке — префикс «…»."""
    if len(text.encode("utf-8")) <= limit:
        return text
    budget = limit - len("…\n".encode())
    raw = text.encode("utf-8")[-budget:]
    cut = raw.decode("utf-8", errors="ignore")
    # начинаем с целой строки, если она есть
    if "\n" in cut[:-1]:
        cut = cut.split("\n", 1)[1]
    return "…\n" + cut


def excerpt(output: str) -> str:
    lines = output.rstrip("\n").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if PYTEST_SUMMARY in lines[i]:
            return tail_utf8("=== short test summary info ===\n" + "\n".join(lines[i + 1 :]))
    return tail_utf8("\n".join(lines))


# ---------------------------------------------------------------- секреты


def secret_values() -> list[str]:
    values: list[str] = []
    path = Path(os.environ.get("GHA_SECRETS_FILE", "deploy/.env"))
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in SECRET_KEYS:
                    values.append(value.strip())
    values += [os.environ.get(k, "") for k in SECRET_KEYS]
    # короткие значения не маскируем: они совпали бы с обычным текстом
    return sorted({v for v in values if len(v) >= 6}, key=len, reverse=True)


def mask(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "***")
    return URL_PASSWORD.sub(r"\1***@", text)


# ---------------------------------------------------------------- аннотации с бюджетом


class Annotator:
    def __init__(self) -> None:
        self.state = Path(os.environ.get("GHA_STATE_DIR", "deploy/ci/out/gha"))
        self.state.mkdir(parents=True, exist_ok=True)
        self.counter = self.state / "annotations"
        self.secrets = secret_values()
        self._masked = False

    def _used(self) -> int:
        try:
            return int(self.counter.read_text(encoding="utf-8").strip() or 0)
        except FileNotFoundError:
            return 0

    def emit(self, kind: str, title: str, text: str) -> bool:
        used = self._used()
        cap = MAX_ANNOTATIONS - (RESERVED_FOR_NOTICES if kind == "error" else 0)
        if used >= cap:
            print(f"gha.py: лимит аннотаций ({used}/{MAX_ANNOTATIONS}) — «{title}» только в журнале")
            return False
        if not self._masked:
            for value in self.secrets:
                print(f"::add-mask::{value}")
            self._masked = True
        body = tail_utf8(mask(text, self.secrets)) or "(нет вывода)"
        print(f"::{kind} title={escape_prop(mask(title, self.secrets))}::{escape_data(body)}", flush=True)
        self.counter.write_text(str(used + 1), encoding="utf-8")
        return True


# ---------------------------------------------------------------- команды


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_")[:80] or "step"


def _compose(*args: str) -> str:
    cmd = [*shlex.split(os.environ.get("BM_COMPOSE", DEFAULT_COMPOSE)), *args]
    try:
        # команда — константа или BM_COMPOSE оператора CI
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{' '.join(args)}: {type(e).__name__}: {e}"
    return proc.stdout + proc.stderr


def stack_state() -> str:
    rows, bad = [], []
    for line in _compose("ps", "-a", "--format", "json").splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            if line:
                rows.append(line)
            continue
        items = json.loads(line)
        for row in items if isinstance(items, list) else [items]:
            svc, state, health = row.get("Service", "?"), row.get("State", "?"), row.get("Health", "")
            rows.append(f"{svc} {state} {health or '-'} exit={row.get('ExitCode', '?')}")
            if state != "running" or health not in ("", "healthy"):
                bad.append(svc)
    logs = _compose("logs", "--no-color", "--tail", "40", *(bad or ["api", "worker"]))
    return "\n".join(rows) + f"\n--- logs ({', '.join(bad) or 'api, worker'}) ---\n" + logs


def cmd_run(args: argparse.Namespace) -> int:
    ann = Annotator()
    log_path = ann.state / f"{_slug(args.title)}.log"
    started = time.monotonic()
    chunks: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        try:
            # команда шага CI из workflow
            proc = subprocess.Popen(  # noqa: S603
                args.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
            )
        except OSError as e:
            chunks.append(f"{type(e).__name__}: {e}\n")
            rc = 127
        else:
            for line in proc.stdout or ():
                sys.stdout.write(line)
                log.write(line)
                chunks.append(line)
                if len(chunks) > 5000:  # для аннотации нужен только хвост
                    del chunks[:1000]
            rc = proc.wait()
        sys.stdout.flush()
    seconds = time.monotonic() - started
    status = "PASS" if rc == 0 else "FAIL"
    with (ann.state / "gates.tsv").open("a", encoding="utf-8") as f:
        f.write(f"{args.title}\t{status}\t{rc}\t{seconds:.0f}\n")
    if rc != 0:
        ann.emit("error", f"{args.title} (exit {rc})", excerpt("".join(chunks)))
        if args.stack:
            ann.emit("error", f"{args.title}: состояние стека", stack_state())
    return rc


def cmd_notice(args: argparse.Namespace) -> int:
    Annotator().emit("notice", args.title, sys.stdin.read())
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    ann = Annotator()
    lines: list[str] = []
    gates = ann.state / "gates.tsv"
    if gates.is_file():
        lines.append("Гейты:")
        for row in gates.read_text(encoding="utf-8").splitlines():
            title, status, rc, seconds = [*row.split("\t"), "", "", ""][:4]
            lines.append(
                f"  {title}: {status}"
                + (f" (exit {rc}, {seconds} с)" if status != "PASS" else f" ({seconds} с)")
            )
    steps = json.loads(os.environ.get("STEPS_JSON") or "{}")
    if steps:
        lines.append("Шаги: " + ", ".join(f"{k}: {v.get('outcome', '?')}" for k, v in steps.items()))
        # 5Д7: шаг, чьё предусловие не выполнено, — «пропущен», а не PASS
        skipped = [k for k, v in steps.items() if v.get("outcome") == "skipped"]
        if skipped:
            lines.append("Пропущены (предусловие не выполнено): " + ", ".join(skipped))
    out = Path(args.out)
    for sbom in sorted(out.glob("sbom-*.cdx.json")):
        lines.append(f"{sbom.name} sha256={hashlib.sha256(sbom.read_bytes()).hexdigest()}")
    summary = out / "summary.txt"
    if summary.is_file():
        text = summary.read_text(encoding="utf-8", errors="replace").splitlines()
        lines.append("make check: " + ("; ".join(t for t in text if not t.startswith("OK")) or "все OK"))
    ann.emit("notice", f"Сводка CI {os.environ.get('GITHUB_JOB', '')}".strip(), "\n".join(lines) or "(пусто)")
    return 0


def _trivy_lines(report: dict, pkgs: list[str]) -> list[str]:
    results = [r for r in report.get("Results") or [] if isinstance(r, dict)]
    lines: list[str] = []
    names = list(dict.fromkeys(pkgs))
    total = 0
    for result in results:
        found = result.get("Vulnerabilities") or []
        vulns = [v for v in found if v.get("Severity", "CRITICAL") == "CRITICAL"]
        if not vulns:
            continue
        total += len(vulns)
        packages = result.get("Packages") or []
        target, cls, kind = result.get("Target", "?"), result.get("Class", "?"), result.get("Type", "?")
        lines.append(f"  Target={target} Class={cls} Type={kind}")
        for v in vulns:
            name, version = v.get("PkgName", "?"), v.get("InstalledVersion", "?")
            same = [p for p in packages if p.get("Name") == name and p.get("Version") == version]
            files = [p.get("FilePath") or "-" for p in same] or ["-"]
            lines.append(
                f"    {v.get('VulnerabilityID', '?')} {name} {version} -> {v.get('FixedVersion') or '-'}"
                f" PkgPath={v.get('PkgPath') or '-'} FilePath={', '.join(files)}"
            )
            if name not in names:
                names.append(name)
    head = [f"{report.get('ArtifactName', '?')}: CRITICAL {total}"]
    for name in names:
        installs = [
            f"  {name} {p.get('Version', '?')} Target={r.get('Target', '?')}"
            f" FilePath={p.get('FilePath') or '-'}"
            for r in results
            for p in r.get("Packages") or []
            if p.get("Name") == name
        ]
        lines += installs or [f"  {name}: не найден"]
    return head + lines


def cmd_trivy(args: argparse.Namespace) -> int:
    lines: list[str] = []
    for raw in args.json:
        path = Path(raw)
        if not path.is_file():
            lines.append(f"{path}: нет отчёта (Trivy не запускался или упал)")
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            lines.append(f"{path}: отчёт не читается: {type(e).__name__}: {e}")
            continue
        lines += _trivy_lines(report if isinstance(report, dict) else {}, args.pkg)
    text = "\n".join(lines) or "(нет отчётов)"
    print(text)
    title = "Trivy: CRITICAL" + (f" и путь {', '.join(args.pkg)}" if args.pkg else "")
    Annotator().emit("notice", title, text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gha.py", description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--title", required=True)
    run.add_argument("--stack", action="store_true")
    run.add_argument("command", nargs="+")
    notice = sub.add_parser("notice")
    notice.add_argument("--title", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--out", default="deploy/ci/out")
    trivy = sub.add_parser("trivy")
    trivy.add_argument("--json", action="append", required=True)
    trivy.add_argument("--pkg", action="append", default=[])
    args = parser.parse_args(argv)
    commands = {"run": cmd_run, "notice": cmd_notice, "summary": cmd_summary, "trivy": cmd_trivy}
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
