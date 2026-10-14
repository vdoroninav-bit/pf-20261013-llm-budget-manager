"""Деньги: стоимость события в копейках и формат `Money` (принцип VI, ADR-003).

Вычисления в `Decimal`, одно округление ROUND_HALF_UP на событие. `float` запрещён.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from bm.domain.types import Kop, Price

_THOUSAND = Decimal(1000)
_HUNDRED = Decimal(100)
_MONEY_RE = re.compile(r"^(-?)([0-9]+)\.([0-9]{2})$")


def round_half_up(x: Decimal) -> Kop:
    """Округление до целого по правилу ROUND_HALF_UP (0.5 → 1, −0.5 → −1)."""
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cost(tokens_in: int, tokens_out: int, tokens_cached: int, price: Price, fx: Decimal) -> Kop:
    """Стоимость события в копейках.

    round_half_up((in·p_in + out·p_out + cached·(p_cached или p_in)) / 1000 · fx · 100).
    `fx` — курс валюты модели к рублю (для RUB — 1). Кэшированные токены без своей цены идут по цене входа.
    """
    if not isinstance(fx, Decimal):
        raise TypeError("fx должен быть Decimal")
    if min(tokens_in, tokens_out, tokens_cached) < 0:
        raise ValueError("число токенов не может быть отрицательным")
    p_cached = price.in_per_1k if price.cached_per_1k is None else price.cached_per_1k
    units = tokens_in * price.in_per_1k + tokens_out * price.out_per_1k + tokens_cached * p_cached
    return round_half_up(units / _THOUSAND * fx * _HUNDRED)


def kop_to_str(k: Kop) -> str:
    """3000 → "30.00", −5 → "-0.05" (схема `Money` контракта)."""
    sign = "-" if k < 0 else ""
    rub, kop = divmod(abs(k), 100)
    return f"{sign}{rub}.{kop:02d}"


def str_to_kop(s: str) -> Kop:
    """ "30.00" → 3000. Принимает только формат `Money` (`^-?[0-9]+\\.[0-9]{2}$`), иначе ValueError."""
    m = _MONEY_RE.match(s)
    if m is None:
        raise ValueError(f"не денежная сумма: {s!r}")
    sign, rub, kop = m.groups()
    value = int(rub) * 100 + int(kop)
    return -value if sign else value
