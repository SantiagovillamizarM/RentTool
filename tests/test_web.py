import math
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.inventory.engine import ProductAnalysis, StoreAnalysis
from app.web.aggregations import aggregate_demand, count_stores_at_risk, product_status, top_risk_products
from app.web.auth import LoginRateLimiter
from app.web.formatting import cop, coverage, daily_series, qty


def product(pid: int, daily: float, units_per_case: int = 12, at_risk: bool = False, stagnant: bool = False):
    return ProductAnalysis(
        product_id=pid,
        sku=f"SKU{pid}",
        name=f"Producto {pid}",
        units_per_case=units_per_case,
        unit_price=Decimal("1000"),
        stock=10,
        has_count=True,
        daily_sales=daily,
        std_dev=0.0,
        coverage_days=math.inf if daily == 0 else 10 / daily,
        horizon_days=2,
        safety_stock=0.0,
        need=0.0,
        cases=0,
        units=0,
        sold_value_28d=Decimal("0"),
        last_sale_at=None,
        at_risk=at_risk,
        stagnant=stagnant,
        projected_units_7d=daily * 7,
    )


def store(store_id: int, products: list[ProductAnalysis]) -> StoreAnalysis:
    return StoreAnalysis(
        store_id=store_id,
        as_of=datetime(2026, 10, 7, tzinfo=timezone.utc),
        horizon_days=2,
        products=products,
    )


# --- Formato -----------------------------------------------------------------


def test_cop():
    assert cop(0) == "$ 0"
    assert cop(1500) == "$ 1.500"
    assert cop(1234567) == "$ 1.234.567"
    assert cop(Decimal("728600.00")) == "$ 728.600"


def test_coverage():
    assert coverage(math.inf) == "Sin ventas"
    assert coverage(1) == "1 día"
    assert coverage(5) == "5 días"
    assert coverage(5.8) == "5 días"


def test_qty():
    assert qty(6.0) == "6"
    assert qty(2.46) == "2,5"


def test_daily_series_fills_gaps():
    end = date(2026, 10, 7)
    series = daily_series({date(2026, 10, 7): 5, date(2026, 9, 10): 3, date(2026, 9, 1): 99}, end)
    assert len(series) == 28
    assert series[0] == (date(2026, 9, 10), 3)
    assert series[-1] == (date(2026, 10, 7), 5)
    assert [day for day, _ in series] == sorted(day for day, _ in series)
    assert sum(units for _, units in series) == 8  # el 1 de septiembre queda fuera


# --- Agregaciones ------------------------------------------------------------


def test_demand_sums_stores_and_rounds_cases_up():
    # Producto 1: (2 + 1,5) al día × 7 = 24,5 -> 25 unidades -> 3 cajas de 12.
    rows = aggregate_demand([store(1, [product(1, 2.0), product(2, 0.5)]), store(2, [product(1, 1.5)])])
    first = rows[0]
    assert (first.product_id, first.units, first.cases) == (1, 25, 3)
    assert (rows[1].product_id, rows[1].units, rows[1].cases) == (2, 4, 1)


def test_demand_float_noise_does_not_add_a_unit():
    # 0,1 × 7 × 20 = 14 exacto, aunque en punto flotante dé 14,000000000000002.
    rows = aggregate_demand([store(i, [product(1, 0.1)]) for i in range(20)])
    assert rows[0].units == 14


def test_risk_aggregations():
    analyses = [
        store(1, [product(1, 3, at_risk=True), product(2, 1, at_risk=True)]),
        store(2, [product(1, 3, at_risk=True)]),
        store(3, [product(2, 1)]),
    ]
    assert count_stores_at_risk(analyses) == 2
    top = top_risk_products(analyses)
    assert [(row.product_id, row.stores_at_risk) for row in top] == [(1, 2), (2, 1)]


def test_product_status():
    assert product_status(product(1, 1, at_risk=True, stagnant=True)) == "Riesgo"
    assert product_status(product(1, 1, stagnant=True)) == "Estancado"
    assert product_status(product(1, 1)) == "OK"


# --- Seguridad ---------------------------------------------------------------


def test_login_rate_limit_blocks_sixth_attempt():
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60)
    assert all(limiter.allow("1.2.3.4", now=100.0 + i) for i in range(5))
    assert not limiter.allow("1.2.3.4", now=105.0)
    assert limiter.allow("5.6.7.8", now=105.0)  # otra IP no se afecta
    assert limiter.allow("1.2.3.4", now=161.0)  # pasado el minuto se libera


@pytest.mark.asyncio
async def test_panel_without_session_redirects_to_login():
    # ASGITransport no ejecuta el lifespan de la app, así que el bot no arranca
    # ni se conecta a Telegram; tampoco se toca la base (la ruta corta antes).
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/panel")
        assert response.status_code == 303
        assert response.headers["location"] == "/panel/login"

        login_page = await client.get("/panel/login")
        assert login_page.status_code == 200
        assert "Ingresar" in login_page.text

        css = await client.get("/panel/static/panel.css")
        assert css.status_code == 200

        health = await client.get("/health")
        assert health.status_code == 200
