#!/usr/bin/env python3
"""Перечень advisory из `npm audit --json` (14_tech_rulings.md, пакет 5, 5Д5). Только stdlib.

Гейт по npm в make check — `npm audit --audit-level=critical`; этот скрипт ничего не гейтует. Он печатает
счётчики по уровням, первичные advisory (GHSA, id npm, пакет, уязвимый диапазон, заголовок) и пакеты,
уязвимые транзитивно. Последняя строка — итог для строки WARN в summary.txt.

  npm_advisories.py <npm-audit.json>     код выхода 0; 2 — отчёт не разобран
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LEVELS = ("critical", "high", "moderate", "low", "info")


def summarize(report: dict) -> list[str]:
    counts = (report.get("metadata") or {}).get("vulnerabilities") or {}
    lines = ["npm audit: " + ", ".join(f"{level} {int(counts.get(level, 0))}" for level in LEVELS)]
    advisories: dict[str, dict] = {}
    transitive: list[str] = []
    for name, vuln in sorted((report.get("vulnerabilities") or {}).items()):
        direct = [v for v in vuln.get("via") or [] if isinstance(v, dict)]
        for adv in direct:
            url = str(adv.get("url") or "")
            key = url.rsplit("/", 1)[-1] if "/advisories/" in url else f"npm-{adv.get('source', '?')}"
            advisories.setdefault(key, adv)
        if not direct:
            transitive.append(name)
    for key, adv in advisories.items():
        lines.append(
            f"  {key} (npm {adv.get('source', '?')}) {adv.get('severity', '?')}"
            f" {adv.get('name', '?')} {adv.get('range', '?')} — {adv.get('title', '')}".rstrip(" —")
        )
    if transitive:
        lines.append("  транзитивно: " + ", ".join(transitive))
    total = sum(int(counts.get(level, 0)) for level in LEVELS)
    if not total and not advisories:
        lines.append("уязвимостей нет")
        return lines
    by_level = ", ".join(f"{int(counts[level])} {level}" for level in LEVELS if int(counts.get(level, 0)))
    ids = ", ".join(
        f"{key} ({adv.get('severity', '?')}, {adv.get('name', '?')} {adv.get('range', '?')})"
        for key, adv in advisories.items()
    )
    lines.append(f"{by_level or '0'}; advisory: {ids or '-'}")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("ожидался объект JSON")
        lines = summarize(report)
    except (OSError, ValueError, TypeError, AttributeError) as e:
        print(f"npm audit --json не разобран: {type(e).__name__}: {e}")
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
