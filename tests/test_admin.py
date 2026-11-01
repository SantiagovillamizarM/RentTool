"""Administrador de TenderoBot: proveedores, sus solicitudes, enlaces de contraseña y productos bloqueados."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.bot import handlers, texts
from app.providers import (
    ProviderError,
    block_product,
    categories_label,
    clean_nit,
    find_token,
    hash_token,
    nit_check_digit,
    regulated,
    unblock_product,
)
from app.web.catalog import plan_catalog_changes
from app.web.provider_forms import parse_provider_request

# --- NIT ----------------------------------------------------------------------


@pytest.mark.parametrize(("base", "digit"), [("899999068", 1), ("890903938", 8), ("800197268", 4), ("860002964", 4)])
def test_nit_check_digit_matches_real_nits(base, digit):
    assert nit_check_digit(base) == digit


def test_nit_is_cleaned_and_checked():
    assert clean_nit("899.999.068-1") == "899999068-1"
    with pytest.raises(ProviderError, match="dígito de verificación"):
        clean_nit("899999068-2")
    with pytest.raises(ProviderError, match="con su dígito"):
        clean_nit("899999068")


# --- Solicitud de proveedor ---------------------------------------------------

VALID = {
    "legal_name": "Distribuciones Ejemplo S.A.S.", "trade_name": "Ejemplo Distribuciones", "nit": "899999068-1",
    "chamber_registration": "05-123456", "legal_rep_name": "Ana Pérez", "legal_rep_document": "1098765432",
    "contact_name": "Luis Gómez", "contact_role": "Gerente", "contact_phone": "300 111 2233", "city": "Bucaramanga",
    "address": "Calle 1 # 2-3", "coverage": "Bucaramanga, Girón", "vehicles": "4", "stores_estimate": "1.250",
    "documents": "on", "consent": "on",
}


def test_valid_provider_request():
    data, errors = parse_provider_request(VALID, ["licores", "bebidas", "inventada"])
    assert errors == []
    assert data["categories"] == "bebidas,licores"  # en el orden del catálogo, sin categorías inventadas
    assert data["chamber_registration"] == "05123456"
    assert data["stores_estimate"] == 1250
    assert data["contact_phone"] == "573001112233"
    assert data["website"] is None


def test_provider_request_needs_documents_categories_and_consent():
    form = {**VALID, "documents": "", "consent": "", "nit": "123", "vehicles": "muchos"}
    _data, errors = parse_provider_request(form, [])
    assert "Marca al menos una categoría de lo que vendes." in errors
    assert any("RUT" in error for error in errors)
    assert any("tratamiento de los datos" in error for error in errors)
    assert any("NIT" in error for error in errors)
    assert any("vehículos" in error for error in errors)


def test_regulated_categories_are_flagged():
    assert categories_label("bebidas,licores") == ["Bebidas y gaseosas", "Licores y cigarrillos"]
    assert regulated("bebidas,licores") == ["Licores y cigarrillos"]
    assert regulated("bebidas") == []


# --- Enlaces de contraseña ----------------------------------------------------


class TokenSession:
    def __init__(self, token):
        self.token = token

    async def scalar(self, statement):
        return self.token


@pytest.mark.asyncio
async def test_password_link_works_once_and_expires():
    now = datetime(2026, 10, 31, tzinfo=UTC)
    token = SimpleNamespace(token_hash=hash_token("abc"), used_at=None, expires_at=now + timedelta(days=1))
    assert await find_token(TokenSession(token), "abc", now) is token
    token.used_at = now
    assert await find_token(TokenSession(token), "abc", now) is None
    token.used_at = None
    assert await find_token(TokenSession(token), "abc", now + timedelta(days=2)) is None
    assert hash_token("abc") != "abc"  # en la base solo queda el hash


# --- Productos bloqueados -----------------------------------------------------


def test_blocked_product_cannot_be_reactivated_by_the_provider():
    product = SimpleNamespace(id=1, name="Aguardiente", unit_price=Decimal("45000"), available_units=None,
                              active=True, blocked_at=None, units_per_case=12)
    block_product(product, "Requiere permiso")
    assert product.active is False and product.blocked_reason == "Requiere permiso"
    plan = plan_catalog_changes([product], {"price-1": "45000", "available-1": "", "active-1": "on"})
    assert plan.changes == []  # "activo" se ignora mientras esté bloqueado
    unblock_product(product)
    assert product.blocked_at is None and product.active is False  # el proveedor decide si lo reactiva


# --- Bot: proveedor suspendido ------------------------------------------------


@pytest.mark.asyncio
async def test_bot_tells_tenderos_when_their_provider_is_suspended(monkeypatch):
    replies: list[str] = []
    store = SimpleNamespace(id=7, distributor_id=3, consent_at=datetime.now(UTC), delivery_days_set_at=datetime.now(UTC))

    class StatusSession:
        async def scalar(self, statement):
            return "suspended"

    async def get_store(session, chat_id):
        return store

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "get_store_by_chat", get_store)
    monkeypatch.setattr(handlers, "_reply", record)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=555))
    assert await handlers.require_ready_store(update, StatusSession()) is None
    assert replies == [texts.PROVIDER_SUSPENDED]


# --- Acceso -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_pages_need_admin_login():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in [
            ("GET", "/admin"),
            ("GET", "/admin/solicitudes"),
            ("GET", "/admin/proveedores"),
            ("POST", "/admin/proveedores/nuevo"),
            ("POST", "/admin/proveedores/1/eliminar"),
            ("POST", "/admin/proveedores/1/productos/1/bloquear"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 303, path
            assert response.headers["location"] == "/login"
        # La entrada vieja del administrador lleva al login único.
        old = await client.get("/admin/login")
        assert old.status_code == 303 and old.headers["location"] == "/login"
