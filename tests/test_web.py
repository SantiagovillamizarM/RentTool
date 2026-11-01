import math
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.inventory.engine import ProductAnalysis, StoreAnalysis
from app.phones import format_phone, normalize_phone
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
        next_delivery=date(2026, 10, 8),
        order_deadline=datetime(2026, 10, 7, 23, tzinfo=timezone.utc),
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
        assert response.headers["location"] == "/login"

        login_page = await client.get("/login")
        assert login_page.status_code == 200
        assert "Iniciar sesión" in login_page.text and "¿Eres tendero?" in login_page.text

        css = await client.get("/panel/static/panel.css")
        assert css.status_code == 200

        health = await client.get("/health")
        assert health.status_code == 200


@pytest.mark.asyncio
async def test_root_asks_to_register_before_showing_the_request_form():
    from types import SimpleNamespace

    from app.db import get_session
    from app.main import app

    class DistributorsSession:
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [SimpleNamespace(id=1, name="Distribuidora El Barrio")])

    async def fake_session():
        yield DistributorsSession()

    app.dependency_overrides[get_session] = fake_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            root = await client.get("/")
            assert root.status_code == 200
            assert "¿Quieres usar TenderoBot?" in root.text
            assert 'action="/registro"' in root.text
            # Sin correo confirmado no hay formulario de solicitud, y enviarlo a mano no sirve.
            assert 'action="/solicitud"' not in root.text
            direct = await client.post("/solicitud", data={"store_name": "Robot"})
            assert direct.status_code == 303
            assert direct.headers["location"] == "/#solicitar"
            # Sin un código pedido, la página del código vuelve al inicio.
            code = await client.get("/registro/codigo")
            assert code.status_code == 303
            sent = await client.get("/solicitud/enviada")
            assert sent.status_code == 200
            assert "abre el bot" in sent.text
    finally:
        app.dependency_overrides.clear()


def test_rate_limiter_reports_seconds_left():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.retry_after("ip", now=0) == 0
    assert limiter.allow("ip", now=0)
    assert limiter.allow("ip", now=10)
    assert limiter.retry_after("ip", now=10) == 50  # el intento del segundo 0 vence en el 60
    assert not limiter.allow("ip", now=20)
    assert limiter.retry_after("ip", now=59.2) == 1
    assert limiter.retry_after("ip", now=60) == 0  # ya venció el primero: puede intentar
    assert limiter.retry_after("otra", now=0) == 0


@pytest.mark.asyncio
async def test_login_shows_countdown_overlay_when_blocked():
    from app.main import app
    from app.web.auth import login_limiter

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert 'id="lock"' not in (await client.get("/login")).text
        saved = login_limiter._hits.pop("127.0.0.1", None)
        try:
            for _ in range(login_limiter.max_attempts):
                login_limiter.allow("127.0.0.1")
            page = await client.get("/login")
            assert 'id="lock"' in page.text
            assert "Demasiados intentos" in page.text
            blocked = await client.post("/login", data={"email": "x@x.co", "password": "x"})
            assert blocked.status_code == 429
            assert 'id="lock"' in blocked.text
        finally:
            login_limiter._hits.pop("127.0.0.1", None)
            if saved is not None:
                login_limiter._hits["127.0.0.1"] = saved


def test_weekday_sales_by_store_in_colombia_time():
    from datetime import timedelta

    from app.inventory.engine import COLOMBIA_TZ
    from app.web.aggregations import weekday_sales

    friday_noon = datetime(2026, 10, 9, 12, tzinfo=COLOMBIA_TZ)  # viernes
    rows = [
        (1, friday_noon, 5),
        (1, friday_noon + timedelta(days=7), 3),  # otro viernes
        (2, datetime(2026, 10, 5, 9, tzinfo=COLOMBIA_TZ), 4),  # lunes
        # Sábado 00:30 en UTC = viernes 7:30 p. m. en Colombia: cuenta como viernes.
        (2, datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc), 2),
        (9, friday_noon, 100),  # tienda de otro distribuidor: se ignora
    ]
    data = weekday_sales(rows, [(1, "Doña Marta"), (2, "El Parque"), (3, "Sin ventas")])
    assert data["labels"] == ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    by_id = {store["id"]: store for store in data["stores"]}
    assert by_id[1]["values"] == [0, 0, 0, 0, 8, 0, 0] and by_id[1]["total"] == 8
    assert by_id[2]["values"] == [4, 0, 0, 0, 2, 0, 0] and by_id[2]["total"] == 6
    assert by_id[3]["values"] == [0] * 7 and by_id[3]["total"] == 0
    assert [store["name"] for store in data["stores"]] == ["Doña Marta", "El Parque", "Sin ventas"]


def test_demand_skips_products_without_sales():
    rows = aggregate_demand([store(1, [product(1, 0.0), product(2, 1.0)]), store(2, [product(1, 0.0)])])
    assert [row.product_id for row in rows] == [2]
    assert aggregate_demand([store(1, [product(1, 0.0)])]) == []


@pytest.mark.asyncio
async def test_live_version_requires_login():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/panel/actualizacion")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- Celular del tendero -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3001234567", "573001234567"),
        ("+57 300 123 4567", "573001234567"),
        ("573001234567", "573001234567"),  # así lo manda Telegram
        ("(300) 123-4567", "573001234567"),
        ("+1 415 555 0100", "14155550100"),
        ("12345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_format_phone():
    assert format_phone("573001234567") == "+57 300 123 4567"
    assert format_phone("14155550100") == "+14155550100"
    assert format_phone(None) == ""


@pytest.mark.asyncio
async def test_store_phone_requires_login():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/panel/tiendas/1/celular", data={"phone": "3001234567"})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


# --- Contraseñas del panel ----------------------------------------------------


def test_password_rules():
    from app.web.auth import is_blocked_password, password_problem

    assert password_problem("demo1234") == "Esa es una contraseña de prueba o muy conocida."
    assert password_problem("corta123") == "La contraseña debe tener al menos 12 caracteres."
    assert password_problem("Tienda-del-barrio-2026") is None
    assert is_blocked_password(" DEMO1234 ")
    assert not is_blocked_password("Tienda-del-barrio-2026")


async def _login_with(password_hash: str, password: str):
    from types import SimpleNamespace

    from app.db import get_session
    from app.main import app

    user = SimpleNamespace(id=1, distributor_id=1, email="demo@demo.co", password_hash=password_hash)

    class UserSession:
        # Primero se busca como administrador (no es), luego como proveedor y el estado de su proveedor.
        answers = [None, user, "active"]

        async def scalar(self, statement):
            return self.answers.pop(0)

    async def fake_session():
        yield UserSession()

    app.dependency_overrides[get_session] = fake_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/login", data={"email": "demo@demo.co", "password": password})
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_test_password_cannot_enter_the_panel_even_if_correct():
    import bcrypt

    weak = bcrypt.hashpw(b"demo1234", bcrypt.gensalt()).decode("utf-8")
    response = await _login_with(weak, "demo1234")
    assert response.status_code == 403
    assert "contraseña de prueba" in response.text
    assert "python -m scripts.panel_user" in response.text


@pytest.mark.asyncio
async def test_strong_password_enters_the_panel():
    import bcrypt

    strong = bcrypt.hashpw(b"Tienda-del-barrio-2026", bcrypt.gensalt()).decode("utf-8")
    response = await _login_with(strong, "Tienda-del-barrio-2026")
    assert response.status_code == 303
    assert response.headers["location"] == "/panel"


def test_public_templates_have_no_broken_placeholders():
    """Ninguna plantilla muestra una variable sin llenar (p. ej. "{ applicant }" con llaves sencillas)."""
    import re
    from pathlib import Path

    from app.providers import CATEGORIES
    from app.web.routes import templates
    from app.web.store_forms import PAYMENT_METHODS

    html = templates.env.get_template("landing.html").render(
        distributors=[], payment_methods=PAYMENT_METHODS, form={}, errors=[], register_errors=[], register_email="",
        register_for="", applicant="tendero@gmail.com", role="provider", existing=None, provider_request=None,
        provider_account=1, provider_form={}, provider_errors=[], provider_categories=CATEGORIES, bot_link="",
        static_version=1,
    )
    assert "tendero@gmail.com" in html
    assert not re.search(r"\{\s*[a-z_]+\s*\}", re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S))
    for template in Path("app/web/templates").rglob("*.html"):
        assert not re.search(r"(?<!\{)\{ [a-z_.]+ \}(?!\})", template.read_text(encoding="utf-8")), template


@pytest.mark.asyncio
async def test_single_login_sends_the_admin_to_admin():
    from types import SimpleNamespace

    import bcrypt

    from app.db import get_session
    from app.main import app

    admin = SimpleNamespace(id=1, email="admin@tenderobot.co",
                            password_hash=bcrypt.hashpw(b"Admin-de-prueba-2026", bcrypt.gensalt()).decode("utf-8"))

    class AdminSession:
        answers = [admin, None]  # es administrador; no es proveedor

        async def scalar(self, statement):
            return self.answers.pop(0)

    async def fake_session():
        yield AdminSession()

    app.dependency_overrides[get_session] = fake_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/login", data={"email": admin.email, "password": "Admin-de-prueba-2026"})
            assert response.status_code == 303 and response.headers["location"] == "/admin"
            for old in ("/panel/login", "/admin/login", "/entrar"):
                assert (await client.get(old)).headers["location"] == "/login"
    finally:
        app.dependency_overrides.clear()
