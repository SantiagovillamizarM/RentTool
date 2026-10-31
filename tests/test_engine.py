import math
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.inventory.engine import (
    COLOMBIA_TZ,
    CountEvent,
    ProductInput,
    ReceiptEvent,
    SaleEvent,
    analyze_store,
    build_reason,
    classify_abc,
    compute_stock,
    daily_sales_series,
    delivery_schedule,
)

# Miércoles 7 de octubre de 2026, mediodía en Colombia.
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=COLOMBIA_TZ)
TODAY = NOW.date()


def product(pid: int = 1, units_per_case: int = 12, price: str = "1000") -> ProductInput:
    return ProductInput(pid, f"SKU{pid}", f"Producto {pid}", units_per_case, Decimal(price))


def daily_sales(pid: int, per_day: int, days: int = 28, price: str = "1000") -> list[SaleEvent]:
    return [
        SaleEvent(pid, per_day, Decimal(price), NOW - timedelta(days=d, hours=1))
        for d in range(days)
        if per_day > 0
    ]


def count(pid: int, qty: int, days_ago: int = 30) -> CountEvent:
    # Exactamente `days_ago` días antes de NOW: las ventas simuladas van a NOW - d días - 1 h,
    # así un conteo con days_ago=0 queda después de todas las ventas.
    return CountEvent(pid, qty, NOW - timedelta(days=days_ago))


# Entregas jueves y viernes: un pedido del miércoles llega el jueves y debe durar hasta
# el viernes, así el horizonte es 2 días (cuentas fáciles en las pruebas).
def run(products, sales=(), counts=(), receipts=(), visits="3,4", lead=1):
    return analyze_store(1, visits, lead, list(products), list(sales), list(counts), list(receipts), NOW)


# --- Entregas ----------------------------------------------------------------


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=COLOMBIA_TZ)


@pytest.mark.parametrize(
    ("now", "next_delivery", "deadline", "following"),
    [
        # Entregas lunes (5, 12) y jueves (8, 15); se pide hasta las 6 p. m. del día anterior.
        (at(5, 9), date(2026, 10, 8), at(7, 18), date(2026, 10, 12)),  # lunes -> jueves
        (at(7, 17, 59), date(2026, 10, 8), at(7, 18), date(2026, 10, 12)),  # miércoles antes de la hora
        (at(7, 18), date(2026, 10, 12), at(11, 18), date(2026, 10, 15)),  # miércoles 6 p. m.: ya no alcanza
        (at(8, 9), date(2026, 10, 12), at(11, 18), date(2026, 10, 15)),  # jueves (hoy hay entrega)
        (at(11, 10), date(2026, 10, 12), at(11, 18), date(2026, 10, 15)),  # domingo en la mañana
    ],
)
def test_delivery_schedule_monday_and_thursday(now, next_delivery, deadline, following):
    schedule = delivery_schedule("0,3", 1, now)
    assert schedule.next_delivery == next_delivery
    assert schedule.order_deadline == deadline
    assert schedule.following_delivery == following


def test_delivery_schedule_lead_time_moves_deadline_back():
    # Con 2 días de anticipación, el jueves 8 se pide hasta el martes 6; el miércoles ya no alcanza.
    assert delivery_schedule("0,3", 2, at(6, 12)).next_delivery == date(2026, 10, 8)
    schedule = delivery_schedule("0,3", 2, at(7, 9))
    assert schedule.next_delivery == date(2026, 10, 12)
    assert schedule.order_deadline == at(10, 18)


def test_delivery_schedule_single_day_lasts_a_week():
    schedule = delivery_schedule("4", 1, at(5, 9))  # solo viernes
    assert (schedule.next_delivery, schedule.following_delivery) == (date(2026, 10, 9), date(2026, 10, 16))


def test_delivery_schedule_without_config_assumes_monday():
    assert delivery_schedule("", 1, NOW).next_delivery == date(2026, 10, 12)
    assert delivery_schedule("x,9", 1, NOW).next_delivery == date(2026, 10, 12)


def test_horizon_lasts_until_the_following_delivery():
    # Miércoles 7 al mediodía con entregas lunes y jueves: llega el jueves 8, dura hasta el lunes 12.
    result = run([product()], visits="0,3", lead=1)
    assert result.next_delivery == date(2026, 10, 8)
    assert result.horizon_days == 5


# --- Stock -------------------------------------------------------------------


def test_stock_without_count_is_zero_and_flagged():
    stock, has_count = compute_stock([], [], daily_sales(1, 3))
    assert (stock, has_count) == (0, False)

    p = run([product()], sales=daily_sales(1, 3)).products[0]
    assert p.stock == 0
    assert not p.has_count
    assert "sin conteo" in p.reason


def test_stock_uses_last_count_plus_receipts_minus_later_sales():
    counts = [count(1, 100, days_ago=10), count(1, 50, days_ago=5)]
    receipts = [
        ReceiptEvent(1, 24, NOW - timedelta(days=6)),  # antes del último conteo: no cuenta
        ReceiptEvent(1, 12, NOW - timedelta(days=2)),
    ]
    sales = daily_sales(1, 2, days=10)  # 2 al día; 5 días después del conteo -> 10
    stock, has_count = compute_stock(counts, receipts, sales)
    assert has_count
    assert stock == 50 + 12 - 10


def test_stock_never_negative():
    stock, _ = compute_stock([count(1, 5, days_ago=3)], [], daily_sales(1, 10, days=3))
    assert stock == 0


# --- Sin ventas --------------------------------------------------------------


def test_no_sales_means_no_order_and_infinite_coverage():
    result = run([product()], counts=[count(1, 0)])
    p = result.products[0]
    assert p.daily_sales == 0
    assert p.std_dev == 0
    assert math.isinf(p.coverage_days)
    assert p.cases == 0
    assert not p.at_risk
    assert result.order_lines == []


def test_no_sales_with_stock_is_stagnant():
    p = run([product()], counts=[count(1, 8)]).products[0]
    assert p.stagnant


def test_daily_series_counts_empty_days_as_zero():
    sales = [SaleEvent(1, 5, Decimal("1"), NOW), SaleEvent(1, 3, Decimal("1"), NOW - timedelta(days=27))]
    series = daily_sales_series(sales, TODAY)
    assert len(series) == 28
    assert series[-1] == 5
    assert series[0] == 3
    assert sum(series) == 8


def test_sales_outside_window_are_ignored():
    old = [SaleEvent(1, 100, Decimal("1"), NOW - timedelta(days=40))]
    assert sum(daily_sales_series(old, TODAY)) == 0


# --- Stock alto --------------------------------------------------------------


def test_high_stock_no_order_and_stagnant_by_coverage():
    # Vende 1 al día con 100 en el estante: cobertura ~100 días > 45.
    result = run([product()], sales=daily_sales(1, 1), counts=[count(1, 100, days_ago=0)])
    p = result.products[0]
    assert p.cases == 0
    assert not p.at_risk
    assert p.stagnant
    assert result.order_lines == []


# --- Riesgo y pedido ---------------------------------------------------------


def test_low_stock_is_at_risk_with_reason():
    # Vende 6 al día, quedan 6: alcanza para 1 día; horizonte = 2.
    result = run([product()], sales=daily_sales(1, 6), counts=[count(1, 6, days_ago=0)])
    p = result.products[0]
    assert p.daily_sales == 6
    assert p.coverage_days == pytest.approx(1.0)
    assert p.at_risk
    assert p.reason == "Vendes 6 al día, te alcanza para 1 día"


def test_rounding_up_to_cases():
    # Venta constante (desviación 0), horizonte 2: necesidad = 6*2 - 0 = 12 -> 1 caja de 12.
    p = run([product(units_per_case=12)], sales=daily_sales(1, 6), counts=[count(1, 0, days_ago=0)]).products[0]
    assert p.std_dev == 0
    assert p.need == pytest.approx(12)
    assert (p.cases, p.units) == (1, 12)

    # Misma necesidad (12) con cajas de 11 -> 2 cajas (22 unidades).
    p = run([product(units_per_case=11)], sales=daily_sales(1, 6), counts=[count(1, 0, days_ago=0)]).products[0]
    assert (p.cases, p.units) == (2, 22)


def test_units_in_transit_are_not_ordered_again():
    # Necesidad 12 (6 al día, horizonte 2, sin stock). Ya vienen 12 en camino: no se pide nada.
    def analyze(in_transit):
        return analyze_store(
            1, "3,4", 1, [product()], daily_sales(1, 6), [count(1, 0, days_ago=0)], [], NOW, in_transit=in_transit
        )

    p = analyze({1: 12}).products[0]
    assert p.in_transit == 12
    assert p.need == pytest.approx(0)
    assert p.cases == 0
    assert analyze({1: 12}).order_lines == []
    # Si solo vienen 6, falta la mitad: 1 caja.
    assert analyze({1: 6}).products[0].cases == 1
    assert analyze({}).products[0].cases == 1


def test_safety_stock_formula():
    sales = [SaleEvent(1, 10, Decimal("1000"), NOW - timedelta(days=d)) for d in range(0, 28, 2)]
    p = run([product()], sales=sales, counts=[count(1, 0, days_ago=0)]).products[0]
    series = [10 if d % 2 == 0 else 0 for d in range(28)]
    mean = sum(series) / 28
    std = math.sqrt(sum((x - mean) ** 2 for x in series) / 28)
    assert p.daily_sales == pytest.approx(5)
    assert p.std_dev == pytest.approx(std)
    assert p.safety_stock == pytest.approx(1.65 * std * math.sqrt(2))
    assert p.cases == math.ceil((5 * 2 + 1.65 * std * math.sqrt(2)) / 12)


def test_order_total_in_cop():
    result = run(
        [product(1, 12, "2500"), product(2, 6, "4000")],
        sales=daily_sales(1, 6, price="2500") + daily_sales(2, 3, price="4000"),
        counts=[count(1, 0, days_ago=0), count(2, 0, days_ago=0)],
    )
    # P1: necesidad 12 -> 1 caja (12 u); P2: necesidad 6 -> 1 caja (6 u).
    assert result.order_total == Decimal("12") * Decimal("2500") + Decimal("6") * Decimal("4000")
    assert [line.product_id for line in result.order_lines] == [1, 2]


# --- ABC ---------------------------------------------------------------------


def test_abc_classification():
    values = {1: Decimal("700"), 2: Decimal("150"), 3: Decimal("100"), 4: Decimal("50"), 5: Decimal("0")}
    assert classify_abc(values) == {1: "A", 2: "A", 3: "B", 4: "C", 5: "C"}


def test_abc_top_product_always_a():
    assert classify_abc({1: Decimal("900"), 2: Decimal("100")}) == {1: "A", 2: "B"}


def test_abc_all_zero_is_c():
    assert classify_abc({1: Decimal("0"), 2: Decimal("0")}) == {1: "C", 2: "C"}


def test_abc_applied_in_store_analysis():
    result = run(
        # Valor 5000/día vs 1000/día: el acumulado antes del 2 es 83 % -> B.
        [product(1), product(2)],
        sales=daily_sales(1, 5) + daily_sales(2, 1),
    )
    by_id = {p.product_id: p.abc_class for p in result.products}
    assert by_id == {1: "A", 2: "B"}


# --- Textos ------------------------------------------------------------------


def test_reason_texts():
    assert build_reason(2.5, 3.7, True) == "Vendes 2,5 al día, te alcanza para 3 días"
    assert build_reason(4, 0.5, True) == "Vendes 4 al día, ya casi no te queda"
