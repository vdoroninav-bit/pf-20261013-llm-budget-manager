#!/usr/bin/env python3
"""Проверка лицензий по SBOM CycloneDX (JSON) против белого списка (принцип V, T088).

Использование:
    check_licenses.py --allow licenses-allow.txt --exceptions licenses-exceptions.txt \
        sbom1.json [sbom2.json ...]

Правила:
- у компонента учитываются лицензии с SPDX id или выражением; классификаторы PyPI
  («License :: OSI Approved :: ...») — только если id и выражения нет;
- несколько записей лицензий у компонента — все должны быть разрешены;
- выражение: «A OR B» — достаточно одной разрешённой альтернативы, «A AND B» — нужны все;
  «X WITH exception» оценивается по X;
- GPL, AGPL, LGPL, SSPL без разрешённой альтернативы — запрещено;
- пакеты npm `@react-admin/ra-*` (enterprise-часть react-admin) — запрещены всегда, исключения не действуют;
- нет сведений о лицензии — нарушение (UNKNOWN), пока компонент не внесён в исключения.

Код выхода: 0 — нарушений нет, 1 — есть нарушения, 2 — ошибка запуска.
Только стандартная библиотека: скрипт должен работать в закрытом контуре.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

COPYLEFT = re.compile(r"\b(A?GPL|LGPL|SSPL)", re.IGNORECASE)
ENTERPRISE_NPM = re.compile(r"^@react-admin/ra-")

# Классификаторы PyPI и частые свободные названия -> SPDX id.
NAME_TO_SPDX = {
    "mit": "MIT",
    "mit license": "MIT",
    "license :: osi approved :: mit license": "MIT",
    "license :: osi approved :: mit no attribution license (mit-0)": "MIT-0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "license :: osi approved :: apache software license": "Apache-2.0",
    "bsd": "BSD-3-Clause",  # консервативно трактуется как BSD-3-Clause
    "bsd license": "BSD-3-Clause",
    "license :: osi approved :: bsd license": "BSD-3-Clause",
    "bsd 2-clause license": "BSD-2-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "isc license": "ISC",
    "license :: osi approved :: isc license (iscl)": "ISC",
    "license :: osi approved :: python software foundation license": "PSF-2.0",
    "license :: osi approved :: mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "postgresql license": "PostgreSQL",
}


def load_list(path: Path) -> set[str]:
    items: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            items.add(line)
    return items


def load_exceptions(path: Path) -> set[str]:
    """Исключения — узкие (пакет и версия, 14_tech_rulings.md, пакет 3, 3Д3): `<тип purl>:<имя>@<версия>`."""
    items = load_list(path)
    bad = sorted(i for i in items if ":" not in i or "@" not in i.split(":", 1)[1].lstrip("@"))
    if bad:
        raise ValueError(f"исключения без версии (нужно <тип>:<имя>@<версия>): {', '.join(bad)}")
    return items


def normalize(name: str) -> str:
    return NAME_TO_SPDX.get(name.strip().lower(), name.strip())


def expr_allowed(expr: str, allow: set[str]) -> bool:
    """Упрощённый разбор SPDX-выражения: OR верхнего уровня, внутри — AND, скобки снимаются."""
    cleaned = expr.replace("(", " ").replace(")", " ")
    alternatives = re.split(r"\s+OR\s+", cleaned.strip(), flags=re.IGNORECASE)
    for alt in alternatives:
        terms = re.split(r"\s+AND\s+", alt.strip(), flags=re.IGNORECASE)
        ids = [re.split(r"\s+WITH\s+", t.strip(), flags=re.IGNORECASE)[0].strip() for t in terms]
        if ids and all(normalize(i) in allow for i in ids):
            return True
    return False


def component_licenses(comp: dict) -> list[str]:
    """Список лицензионных выражений компонента (каждое — строка SPDX или название)."""
    strong: list[str] = []
    weak: list[str] = []
    for entry in comp.get("licenses") or []:
        if "expression" in entry:
            strong.append(entry["expression"])
            continue
        lic = entry.get("license") or {}
        if lic.get("id"):
            strong.append(lic["id"])
        elif lic.get("name"):
            name = lic["name"]
            (weak if name.startswith("License ::") else strong).append(name)
    return strong or weak


def ecosystem(comp: dict) -> str:
    purl = comp.get("purl") or ""
    m = re.match(r"pkg:([a-z0-9.+-]+)/", purl)
    return m.group(1) if m else "unknown"


def full_name(comp: dict) -> str:
    group = comp.get("group")
    return f"{group}/{comp['name']}" if group else comp["name"]


def walk(components: list[dict]):
    for comp in components:
        yield comp
        yield from walk(comp.get("components") or [])


def check(sbom_path: Path, allow: set[str], exceptions: set[str]) -> tuple[list[tuple], int]:
    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    violations: list[tuple] = []
    count = 0
    for comp in walk(data.get("components") or []):
        if comp.get("type") not in (None, "library", "framework"):
            continue
        count += 1
        eco, name, ver = ecosystem(comp), full_name(comp), comp.get("version", "")
        lics = component_licenses(comp)
        if eco == "npm" and ENTERPRISE_NPM.match(name):
            violations.append((eco, name, ver, "ENTERPRISE", "; ".join(lics) or "-"))
            continue
        if f"{eco}:{name}@{ver}" in exceptions and not any(COPYLEFT.search(lic) for lic in lics):
            continue  # на копилефт исключения не действуют
        if not lics:
            violations.append((eco, name, ver, "UNKNOWN", "-"))
            continue
        bad = [lic for lic in lics if not expr_allowed(lic, allow)]
        if bad:
            kind = "COPYLEFT" if any(COPYLEFT.search(b) for b in bad) else "NOT_ALLOWED"
            violations.append((eco, name, ver, kind, "; ".join(bad)))
    return violations, count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow", type=Path, required=True)
    ap.add_argument("--exceptions", type=Path)
    ap.add_argument("sboms", type=Path, nargs="+")
    args = ap.parse_args()

    allow = load_list(args.allow)
    try:
        has_file = args.exceptions is not None and args.exceptions.exists()
        exceptions = load_exceptions(args.exceptions) if has_file else set()
    except ValueError as e:
        print(f"ОШИБКА: {args.exceptions}: {e}", file=sys.stderr)
        return 2
    total_bad = 0
    for sbom in args.sboms:
        if not sbom.exists():
            print(f"ОШИБКА: нет файла SBOM {sbom}", file=sys.stderr)
            return 2
        violations, count = check(sbom, allow, exceptions)
        print(f"{sbom}: компонентов {count}, нарушений {len(violations)}")
        for eco, name, ver, kind, lic in violations:
            print(f"  {kind:<12} {eco}:{name}@{ver}  [{lic}]")
        total_bad += len(violations)
    if exceptions:
        print(f"Исключения (deploy/ci/licenses-exceptions.txt): {', '.join(sorted(exceptions))}")
    if total_bad:
        print(f"ЛИЦЕНЗИИ: FAIL ({total_bad} нарушений)")
        return 1
    print("ЛИЦЕНЗИИ: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
