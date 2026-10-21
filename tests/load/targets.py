"""Нагрузочный тест SC-001 и SC-004 (T086). Запуск: `make up-local && BM_STACK=local make load` (или `make up && make load`).

SC-001 (10_tech_spec.md): время решения внутри утилиты p95 ≤ 10 мс, p99 ≤ 25 мс; у вызывающей стороны с сетью
на том же хосте p95 ≤ 20 мс — при 50 rps.
  Подготовка через API админки: 30 моделей, 10 подразделений (enforce, большой месячный лимит, разрешены все
  30 моделей), 1 000 сотрудников (загрузка выгрузки AD, `importOrg`), по ключу `department_employees` на
  подразделение. Цели vegeta — `POST /api/v1/decisions` со случайной парой «сотрудник × модель».
  Нагрузка: vegeta (если есть в PATH или в VEGETA) с фиксированной частотой LOAD_RATE на LOAD_DURATION_S;
  иначе — генератор на httpx с фиксированной частотой (открытая модель: запросы не ждут предыдущих).
  p95/p99 внутри — из гистограммы `decision_latency_seconds` в `/metrics` (разность до/после нагрузки):
  у гистограммы есть границы 0,01 и 0,025 с, поэтому «p95 ≤ 10 мс» проверяется точно — доля ≤ 0,01 с не
  меньше 95%. Для справки печатается оценка квантиля линейной интерполяцией (как histogram_quantile).
  p95/p99 у клиента — из отчёта vegeta (или из замеров генератора httpx).

SC-004: 3 экземпляра вызывающей системы (3 ключа, у каждого свой HTTP-клиент) × 50 одновременных запросов на
  границе лимита подразделения. Лимит месяца 1 000 ₽, до теста израсходовано 990 ₽ (отчёт без решения), оценка
  вызова 1,40 ₽. Разрешённые вызовы отчитываются фактом = оценка × 1,1. Проверки: Σ резервов разрешённых
  ≤ остатка до теста (гонки не дают перерасхода); перерасход после отчётов ≤ 2% лимита.

ВНИМАНИЕ: `importOrg` выводит из подразделений всех сотрудников, которых нет в файле. Тест — только для
тестового стенда: при BM_STACK=compose нужно явно задать LOAD_STAND_OK=1.

Переменные: BASE_URL, BM_STACK, BM_ENV_FILE, BM_ADMIN_LOGIN/PASSWORD (как в tests/failover/stack.py),
LOAD_RATE (50), LOAD_DURATION_S (600), LOAD_OUT (каталог результатов), VEGETA (путь к vegeta).
"""

import asyncio
import base64
import json
import math
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from tests.failover.stack import BASE_URL, STACK, Admin

ROOT = Path(__file__).resolve().parents[2]
RATE = int(os.environ.get("LOAD_RATE", "50"))
DURATION_S = int(os.environ.get("LOAD_DURATION_S", "600"))
EMPLOYEES = 1000
MODELS = 30
DEPARTMENTS = 10

SC001_P95_S = 0.010
SC001_P99_S = 0.025
SC001_CLIENT_P95_S = 0.020
SC004_INSTANCES = 3
SC004_CONCURRENT = 50
SC004_MAX_OVERRUN = Decimal("0.02")


# ---------------------------------------------------------------- подготовка данных


@dataclass
class Plan:
    run: str
    models: list[str]
    keys: dict[str, str]  # department_id → ключ department_employees
    employees: dict[str, str]  # login → department_id


def provision(admin: Admin) -> Plan:
    run = uuid.uuid4().hex[:6]
    models = [f"load-{run}-m{i:02d}" for i in range(MODELS)]
    for i, m in enumerate(models):
        admin.call(
            "POST",
            "/api/v1/models",
            {
                "id": m,
                "provider": "internal",
                "kind": "chat",
                "supports_tools": True,
                "supports_json": True,
                "max_context": 128000,
                "default_input_tokens": 500,
                "default_output_tokens": 200,
                "tie_order": i,
                "price": {"input_per_1k": f"0.{i + 10:02d}", "output_per_1k": "0.5", "currency": "RUB"},
            },
            expect=201,
        )
    groups: dict[str, str] = {}  # группа AD → department_id
    keys: dict[str, str] = {}
    for d in range(DEPARTMENTS):
        group = f"load-{run}-d{d:02d}"
        dep = admin.call("POST", "/api/v1/departments", {"name": group, "ad_group": group}, expect=201)["id"]
        admin.call(
            "PATCH", f"/api/v1/departments/{dep}", {"policy_mode": "enforce", "allowed_models": models}
        )
        admin.call(
            "PUT", f"/api/v1/departments/{dep}/limits", {"day": None, "week": None, "month": "10000000.00"}
        )
        caller = admin.call(
            "POST",
            "/api/v1/callers",
            {"name": group, "subject_scope": {"type": "department_employees", "department_id": dep}},
            expect=201,
        )
        groups[group] = dep
        keys[dep] = caller["api_key"]
    employees: dict[str, str] = {}
    lines = ["login;display_name;group;is_primary"]
    group_names = sorted(groups)
    for e in range(EMPLOYEES):
        login = f"load{run}u{e:04d}"
        group = group_names[e % DEPARTMENTS]
        lines.append(f"{login};Load {e};{group};1")
        employees[login] = groups[group]
    csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    r = admin.http.post(
        "/api/v1/org/import", files={"file": ("load.csv", csv_bytes, "text/csv")}, headers=admin.csrf
    )
    assert r.status_code == 200, f"importOrg: {r.status_code} {r.text[:300]}"
    summary = r.json()
    assert summary["rows_applied"] == EMPLOYEES and not summary["errors"], f"importOrg: {summary}"
    return Plan(run, models, keys, employees)


def decision_body(login: str, model: str) -> dict[str, Any]:
    return {
        "subject": {"type": "employee", "login": login},
        "model": model,
        "kind": "chat",
        "input_tokens": 500,
        "max_output_tokens": 200,
    }


def write_targets(plan: Plan, out: Path, n: int) -> Path:
    """Цели vegeta (`-format=json`): n запросов решения со случайной парой «сотрудник × модель»."""
    rnd = random.Random(plan.run)  # noqa: S311 — выбор цели, не криптография
    logins = sorted(plan.employees)
    path = out / "targets.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for _ in range(n):
            login = rnd.choice(logins)
            body = json.dumps(decision_body(login, rnd.choice(plan.models))).encode()
            target = {
                "method": "POST",
                "url": f"{BASE_URL}/api/v1/decisions",
                "body": base64.b64encode(body).decode(),
                "header": {
                    "Content-Type": ["application/json"],
                    "X-Api-Key": [plan.keys[plan.employees[login]]],
                },
            }
            f.write(json.dumps(target) + "\n")
    os.chmod(path, 0o600)  # в целях — ключи API тестового стенда
    return path


# ---------------------------------------------------------------- метрики


def histogram() -> tuple[dict[float, float], float]:
    """Кумулятивные бакеты `decision_latency_seconds` и count из /metrics (сумма по воркерам api)."""
    r = httpx.get(f"{BASE_URL}/metrics", timeout=10, trust_env=False)
    assert r.status_code == 200, f"/metrics: {r.status_code}"
    buckets: dict[float, float] = {}
    count = 0.0
    for line in r.text.splitlines():
        if line.startswith("decision_latency_seconds_bucket{"):
            le = line.split('le="', 1)[1].split('"', 1)[0]
            buckets[math.inf if le == "+Inf" else float(le)] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("decision_latency_seconds_count"):
            count = float(line.rsplit(" ", 1)[1])
    assert buckets, "в /metrics нет decision_latency_seconds"
    return buckets, count


def share_le(delta: dict[float, float], total: float, le: float) -> float:
    return delta[le] / total if total else 0.0


def quantile(delta: dict[float, float], total: float, q: float) -> float:
    """Оценка квантиля по кумулятивным бакетам — линейная интерполяция, как histogram_quantile в Prometheus."""
    rank = q * total
    prev_le, prev_c = 0.0, 0.0
    for le in sorted(delta):
        c = delta[le]
        if c >= rank:
            if math.isinf(le):
                return prev_le
            return prev_le + (le - prev_le) * ((rank - prev_c) / (c - prev_c) if c > prev_c else 0)
        prev_le, prev_c = le, c
    return math.nan


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


# ---------------------------------------------------------------- нагрузка


def vegeta_bin() -> str | None:
    v = os.environ.get("VEGETA") or shutil.which("vegeta")
    return v if v and Path(v).is_file() else None


def attack_vegeta(vegeta: str, targets: Path, out: Path) -> dict[str, Any]:
    results = out / "results.bin"
    with targets.open("rb") as tf, results.open("wb") as rf:
        # vegeta — внешний инструмент нагрузки; путь из PATH или VEGETA оператора стенда
        subprocess.run(  # noqa: S603
            [vegeta, "attack", "-format=json", f"-rate={RATE}/s", f"-duration={DURATION_S}s", "-timeout=5s"],
            stdin=tf,
            stdout=rf,
            check=True,
        )
    rep = subprocess.run(  # noqa: S603
        [vegeta, "report", "-type=json", str(results)], capture_output=True, text=True, check=True
    )
    (out / "vegeta_report.json").write_text(rep.stdout, encoding="utf-8")
    txt = subprocess.run([vegeta, "report", str(results)], capture_output=True, text=True, check=True)  # noqa: S603
    (out / "vegeta_report.txt").write_text(txt.stdout, encoding="utf-8")
    data = json.loads(rep.stdout)
    lat = data["latencies"]
    return {
        "tool": "vegeta "
        + subprocess.run([vegeta, "-version"], capture_output=True, text=True).stdout.split()[1],  # noqa: S603
        "requests": data["requests"],
        "rate": data["rate"],
        "success": data["success"],
        "status_codes": data["status_codes"],
        "errors": data["errors"][:5],
        "p50_s": lat["50th"] / 1e9,
        "p95_s": lat["95th"] / 1e9,
        "p99_s": lat["99th"] / 1e9,
        "max_s": lat["max"] / 1e9,
    }


async def _attack_httpx_async(targets: list[dict[str, Any]]) -> dict[str, Any]:
    lat: list[float] = []
    codes: dict[str, int] = {}
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    # TCP_NODELAY, как у эталонного клиента (budget_client/transport.py): httpx пишет заголовки и тело двумя
    # сегментами, и без него Nagle + отложенный ACK сервера добавляют ~40 мс к каждому запросу keep-alive.
    transport = httpx.AsyncHTTPTransport(
        limits=limits, socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
    )
    async with httpx.AsyncClient(timeout=5, transport=transport, trust_env=False) as c:

        async def one(t: dict[str, Any]) -> None:
            started = time.perf_counter()
            try:
                r = await c.post(t["url"], content=t["body"], headers=t["headers"])
                key = str(r.status_code)
            except httpx.HTTPError as e:
                key = type(e).__name__
            lat.append(time.perf_counter() - started)
            codes[key] = codes.get(key, 0) + 1

        tasks = []
        t0 = time.perf_counter()
        n = RATE * DURATION_S
        for i in range(n):
            delay = t0 + i / RATE - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(targets[i % len(targets)])))
        elapsed = time.perf_counter() - t0
        await asyncio.gather(*tasks)
    return {
        "tool": "httpx (vegeta не использован)",
        "requests": len(lat),
        "rate": len(lat) / elapsed if elapsed else 0,
        "success": codes.get("200", 0) / len(lat) if lat else 0,
        "status_codes": codes,
        "errors": [],
        "p50_s": pct(lat, 0.50),
        "p95_s": pct(lat, 0.95),
        "p99_s": pct(lat, 0.99),
        "max_s": max(lat),
    }


def attack_httpx(targets_file: Path) -> dict[str, Any]:
    targets = []
    for line in targets_file.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        targets.append(
            {
                "url": t["url"],
                "body": base64.b64decode(t["body"]),
                "headers": {k: v[0] for k, v in t["header"].items()},
            }
        )
    return asyncio.run(_attack_httpx_async(targets))


# ---------------------------------------------------------------- SC-004


async def sc004(admin: Admin, run: str) -> dict[str, Any]:
    model = f"load-{run}-edge"
    admin.call(
        "POST",
        "/api/v1/models",
        {
            "id": model,
            "provider": "internal",
            "kind": "chat",
            "supports_tools": True,
            "supports_json": True,
            "max_context": 128000,
            "default_input_tokens": 1000,
            "default_output_tokens": 400,
            "price": {"input_per_1k": "1", "output_per_1k": "1", "currency": "RUB"},
        },
        expect=201,
    )
    dep = admin.call("POST", "/api/v1/departments", {"name": f"load-{run}-edge"}, expect=201)["id"]
    admin.call("PATCH", f"/api/v1/departments/{dep}", {"policy_mode": "enforce", "allowed_models": [model]})
    admin.call("PUT", f"/api/v1/departments/{dep}/limits", {"day": None, "week": None, "month": "1000.00"})
    keys = [
        admin.call(
            "POST",
            "/api/v1/callers",
            {"name": f"load-{run}-edge-{i}", "subject_scope": {"type": "service", "department_id": dep}},
            expect=201,
        )["api_key"]
        for i in range(SC004_INSTANCES)
    ]
    subject = {"type": "service"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10, trust_env=False) as c0:
        # 990 ₽ расхода до теста: отчёт без решения (как после fail-open), 990 000 входных токенов по 1 ₽/1k
        pre = await c0.post(
            "/api/v1/usage",
            json={
                "report_id": str(uuid.uuid4()),
                "tokens": {"input": 990_000, "output": 0},
                "call_status": "ok",
                "subject": subject,
                "model": model,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
            headers={"X-Api-Key": keys[0]},
        )
        assert pre.status_code == 200, f"предрасход: {pre.status_code} {pre.text[:300]}"
        before = await month_line(c0, keys[0])

    async def instance(key: str) -> list[httpx.Response]:
        limits = httpx.Limits(max_connections=SC004_CONCURRENT)
        async with httpx.AsyncClient(
            base_url=BASE_URL, timeout=30, limits=limits, headers={"X-Api-Key": key}, trust_env=False
        ) as c:
            body = {
                "subject": subject,
                "model": model,
                "kind": "chat",
                "input_tokens": 1000,
                "max_output_tokens": 400,
            }
            return list(
                await asyncio.gather(
                    *(c.post("/api/v1/decisions", json=body) for _ in range(SC004_CONCURRENT))
                )
            )

    per_instance = await asyncio.gather(*(instance(k) for k in keys))
    responses = [r for rs in per_instance for r in rs]
    # отчёт отправляет тот же экземпляр (ключ), что получил решение
    owner = [k for k, rs in zip(keys, per_instance, strict=True) for _ in rs]
    statuses = {r.status_code for r in responses}
    assert statuses == {200}, f"SC-004: статусы решений {statuses}"
    decisions = [r.json() | {"_key": k} for r, k in zip(responses, owner, strict=True)]
    allowed = [d for d in decisions if d["outcome"] == "allow"]
    denied = [d for d in decisions if d["outcome"] == "deny"]
    reserved = sum((Decimal(d["reserve_rub"]) for d in allowed), Decimal("0"))

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10, trust_env=False) as c0:
        # факт = оценка × 1,1 (1 100 / 440 токенов вместо 1 000 / 400)
        for d in allowed:
            r = await c0.post(
                "/api/v1/usage",
                json={
                    "report_id": str(uuid.uuid4()),
                    "decision_id": d["decision_id"],
                    "tokens": {"input": 1100, "output": 440},
                    "call_status": "ok",
                },
                headers={"X-Api-Key": d["_key"]},
            )
            assert r.status_code == 200, f"отчёт SC-004: {r.status_code} {r.text[:300]}"
        after = await month_line(c0, keys[0])

    limit = Decimal(after["limit_rub"])
    remaining_before = Decimal(before["remaining_rub"])
    remaining_after = Decimal(after["remaining_rub"])
    overrun = max(Decimal("0"), -remaining_after)
    return {
        "requests": len(decisions),
        "allowed": len(allowed),
        "denied": len(denied),
        "deny_codes": sorted({d.get("code") for d in denied}),
        "limit_rub": str(limit),
        "remaining_before_rub": str(remaining_before),
        "reserved_allowed_rub": str(reserved),
        "remaining_after_rub": str(remaining_after),
        "reserved_after_rub": after["reserved_rub"],
        "overrun_rub": str(overrun),
        "overrun_pct": str((overrun / limit * 100).quantize(Decimal("0.001"))),
        "race_free": reserved <= remaining_before,
        "pass": reserved <= remaining_before and overrun <= limit * SC004_MAX_OVERRUN and len(allowed) > 0,
    }


async def month_line(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    r = await c.get("/api/v1/balance", headers={"X-Api-Key": key})
    assert r.status_code == 200, f"getCallerBalance: {r.status_code} {r.text[:300]}"
    rows = [x for x in r.json()["limits"] if x["owner_type"] == "department" and x["period"] == "month"]
    assert len(rows) == 1, r.json()
    line: dict[str, Any] = rows[0]
    return line


# ---------------------------------------------------------------- прогон


def main() -> int:
    if STACK != "local" and os.environ.get("LOAD_STAND_OK") != "1":
        print(
            "make load: importOrg перезапишет оргструктуру стенда. Для стека compose задайте LOAD_STAND_OK=1"
        )
        return 2
    out = Path(os.environ.get("LOAD_OUT") or ROOT / "deploy" / "local" / ".state" / "load")
    out.mkdir(parents=True, exist_ok=True)
    print(
        f"make load: BASE_URL={BASE_URL}, BM_STACK={STACK}, {RATE} rps × {DURATION_S} с, результаты — {out}"
    )
    print(
        "ВНИМАНИЕ: числа — среда разработки (общая 2-CPU машина, генератор нагрузки на том же хосте), "
        "не эталонный стенд; статус гейта SC-001 — PASS-замена"
    )
    admin = Admin()
    t = time.monotonic()
    plan = provision(admin)
    targets = write_targets(plan, out, RATE * DURATION_S)
    print(
        f"подготовка: {MODELS} моделей, {DEPARTMENTS} подразделений, {len(plan.employees)} сотрудников, "
        f"{RATE * DURATION_S} целей за {time.monotonic() - t:.1f} с"
    )

    h0, c0 = histogram()
    vegeta = vegeta_bin()
    client = attack_vegeta(vegeta, targets, out) if vegeta else attack_httpx(targets)
    time.sleep(1)
    h1, c1 = histogram()
    delta = {le: h1[le] - h0.get(le, 0.0) for le in h1}
    total = c1 - c0
    in_p95_ok = share_le(delta, total, SC001_P95_S) >= 0.95
    in_p99_ok = share_le(delta, total, SC001_P99_S) >= 0.99
    client_ok = client["p95_s"] <= SC001_CLIENT_P95_S
    success_ok = client["success"] >= 0.999
    rate_ok = client["rate"] >= 0.98 * RATE
    sc001 = {
        "client": client,
        "metrics_count": total,
        "share_le_10ms": round(share_le(delta, total, SC001_P95_S), 5),
        "share_le_25ms": round(share_le(delta, total, SC001_P99_S), 5),
        "p95_s_est": quantile(delta, total, 0.95),
        "p99_s_est": quantile(delta, total, 0.99),
        "pass": in_p95_ok and in_p99_ok and client_ok and success_ok and rate_ok,
    }
    sc004_res = asyncio.run(sc004(admin, plan.run))
    (out / "summary.json").write_text(
        json.dumps({"sc001": sc001, "sc004": sc004_res}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    ms = 1000
    print(
        f"\nSC-001 нагрузка: {client['tool']}; запросов {client['requests']}, факт {client['rate']:.2f} rps, "
        f"успешных {client['success'] * 100:.2f}%, коды {client['status_codes']}"
    )
    print(
        f"  внутри (/metrics, {int(total)} решений): доля ≤10 мс {sc001['share_le_10ms'] * 100:.2f}% (нужно ≥95%), "
        f"≤25 мс {sc001['share_le_25ms'] * 100:.2f}% (нужно ≥99%); "
        f"p95 ≈ {sc001['p95_s_est'] * ms:.2f} мс, p99 ≈ {sc001['p99_s_est'] * ms:.2f} мс"
    )
    print(
        f"  у клиента: p50 {client['p50_s'] * ms:.2f} мс, p95 {client['p95_s'] * ms:.2f} мс (нужно ≤20), "
        f"p99 {client['p99_s'] * ms:.2f} мс, max {client['max_s'] * ms:.2f} мс"
    )
    print(f"SC-001: {'PASS' if sc001['pass'] else 'FAIL'}")
    s4 = sc004_res
    print(
        f"\nSC-004: {SC004_INSTANCES} экземпляра × {SC004_CONCURRENT} одновременных; разрешено {s4['allowed']}, "
        f"отказ {s4['denied']} {s4['deny_codes']}; остаток до {s4['remaining_before_rub']} ₽, "
        f"Σ резервов {s4['reserved_allowed_rub']} ₽; после отчётов (факт = оценка × 1,1) остаток "
        f"{s4['remaining_after_rub']} ₽, перерасход {s4['overrun_rub']} ₽ = {s4['overrun_pct']}% лимита (нужно ≤2%)"
    )
    print(f"SC-004: {'PASS' if s4['pass'] else 'FAIL'}")
    ok = sc001["pass"] and s4["pass"]
    print(f"\nmake load: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
