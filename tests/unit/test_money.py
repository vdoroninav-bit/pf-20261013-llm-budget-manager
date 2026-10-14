"""T010: деньги — целые копейки, Decimal, одно округление ROUND_HALF_UP на событие (принцип VI, ADR-003)."""

from decimal import Decimal
from uuid import uuid4

import pytest

from bm.domain.money import cost, kop_to_str, str_to_kop
from bm.domain.types import Price


def price(inp: str, out: str, currency: str = "RUB", cached: str | None = None) -> Price:
    return Price(
        id=uuid4(),
        in_per_1k=Decimal(inp),
        out_per_1k=Decimal(out),
        cached_per_1k=None if cached is None else Decimal(cached),
        currency=currency,
    )


def test_cost_rub_example_from_spec() -> None:
    # 1 000 входных × 10 ₽/1k + 400 выходных × 50 ₽/1k = 10 + 20 = 30 ₽ = 3000 коп.
    assert cost(1000, 400, 0, price("10", "50"), Decimal(1)) == 3000


def test_cost_returns_int_kopecks() -> None:
    result = cost(1000, 400, 0, price("10", "50"), Decimal(1))
    assert type(result) is int


def test_cached_tokens_without_cached_price_are_billed_at_input_price() -> None:
    p = price("10", "50")
    # 500 кэшированных по цене входа 10 ₽/1k = 5 ₽
    assert cost(0, 0, 500, p, Decimal(1)) == 500
    assert cost(1000, 400, 500, p, Decimal(1)) == 3500


def test_cached_tokens_with_cached_price() -> None:
    p = price("10", "50", cached="1")
    # 500 × 1 ₽/1k = 0,50 ₽
    assert cost(0, 0, 500, p, Decimal(1)) == 50


def test_usd_converted_with_fx_and_round_half_up() -> None:
    # Случай ровно на половине копейки: 1 000 входных × 0.0005 USD/1k = 0.0005 USD;
    # × 10 = 0.005 ₽ = 0.5 коп. → ROUND_HALF_UP → 1 коп. (банковское округление дало бы 0).
    p = price("0.0005", "0", currency="USD")
    assert cost(1000, 0, 0, p, Decimal("10")) == 1
    # 2.5 коп. → 3 (банковское дало бы 2)
    assert cost(5000, 0, 0, p, Decimal("10")) == 3


def test_usd_realistic_price() -> None:
    # 12 345 входных × 3 USD/1M (= 0.003/1k) + 678 выходных × 0.015/1k, курс 92.5
    # = (37.035 + 10.17) / 1000 USD = 0.047205 USD × 92.5 = 4.3664625 ₽ = 436.64625 коп. → 437
    p = price("0.003", "0.015", currency="USD")
    assert cost(12345, 678, 0, p, Decimal("92.5")) == 437


def test_single_rounding_per_event() -> None:
    # Округление одно на событие, а не на каждую составляющую.
    # Вход 3 × 1 ₽/1k = 0.3 коп., выход 2 × 1 ₽/1k = 0.2 коп.
    # По отдельности: 0 + 0 = 0 коп. Вместе: 0.5 коп. → ROUND_HALF_UP → 1 коп.
    p = price("1", "1")
    assert cost(3, 2, 0, p, Decimal(1)) == 1


def test_zero_price_is_zero() -> None:
    assert cost(10_000, 10_000, 10_000, price("0", "0"), Decimal(1)) == 0


def test_cost_rejects_negative_tokens() -> None:
    with pytest.raises(ValueError):
        cost(-1, 0, 0, price("1", "1"), Decimal(1))


def test_cost_rejects_float_fx() -> None:
    with pytest.raises(TypeError):
        cost(1, 0, 0, price("1", "1"), 1.0)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kop", "text"),
    [(3000, "30.00"), (-5, "-0.05"), (0, "0.00"), (5, "0.05"), (-300, "-3.00"), (24193, "241.93"), (-105, "-1.05")],
)
def test_kop_to_str(kop: int, text: str) -> None:
    assert kop_to_str(kop) == text


@pytest.mark.parametrize(
    ("text", "kop"),
    [("30.00", 3000), ("-0.05", -5), ("0.00", 0), ("241.93", 24193), ("100000.00", 10_000_000)],
)
def test_str_to_kop(text: str, kop: int) -> None:
    assert str_to_kop(text) == kop


@pytest.mark.parametrize("bad", ["30", "30.0", "30.000", "1e3", "abc", "", " 1.00", "+1.00", "1,00"])
def test_str_to_kop_rejects_non_money(bad: str) -> None:
    with pytest.raises(ValueError):
        str_to_kop(bad)


def test_roundtrip() -> None:
    for k in (-123456, -1, 0, 1, 99, 100, 123456789):
        assert str_to_kop(kop_to_str(k)) == k
