from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot import texts
from app.inventory.orders import cap_to_available
from app.web.catalog import FormError, parse_available, parse_price, parse_required, parse_units_per_case


def product(pid, available, units_per_case=12, name=None, price="4500"):
    return SimpleNamespace(
        id=pid,
        name=name or f"Producto {pid}",
        units_per_case=units_per_case,
        available_units=available,
        unit_price=Decimal(price),
    )


# --- Formulario del panel ----------------------------------------------------


@pytest.mark.parametrize("raw", ["4500", "4.500", "$ 4.500", " 4500 "])
def test_parse_price_accepts_colombian_formats(raw):
    assert parse_price(raw) == Decimal("4500")


@pytest.mark.parametrize("raw", ["", "0", "abc", "-100"])
def test_parse_price_rejects_invalid(raw):
    with pytest.raises(FormError):
        parse_price(raw)


def test_parse_available_blank_means_no_limit():
    assert parse_available("") is None
    assert parse_available("2.000") == 2000
    assert parse_available("0") == 0
    with pytest.raises(FormError):
        parse_available("-5")


def test_parse_units_per_case_and_required():
    assert parse_units_per_case("12") == 12
    with pytest.raises(FormError):
        parse_units_per_case("0")
    assert parse_required("  Jabón Rey ", "el nombre", 160) == "Jabón Rey"
    with pytest.raises(FormError):
        parse_required("   ", "el nombre", 160)
    with pytest.raises(FormError):
        parse_required("x" * 33, "el código", 32)


# --- Existencias del distribuidor --------------------------------------------


def test_cap_without_limit_keeps_order():
    result = cap_to_available([{"product_id": 1, "cases": 5, "reason": "r"}], {1: product(1, None)})
    assert result.lines == [{"product_id": 1, "cases": 5, "reason": "r"}]
    assert result.not_found == []


def test_cap_trims_to_full_cases_available():
    # 30 unidades con cajas de 12 -> solo 2 cajas completas.
    result = cap_to_available([{"product_id": 1, "cases": 5, "reason": "r"}], {1: product(1, 30, name="Coca")})
    assert result.lines == [{"product_id": 1, "cases": 2, "reason": "r"}]
    assert result.not_found == ["Coca (solo hay 2 cajas)"]


def test_cap_drops_sold_out_products():
    lines = [{"product_id": 1, "cases": 1, "reason": "r"}, {"product_id": 2, "cases": 1, "reason": "r"}]
    result = cap_to_available(lines, {1: product(1, 11, name="Aceite"), 2: product(2, 500)})
    assert [line["product_id"] for line in result.lines] == [2]
    assert result.not_found == ["Aceite (agotado donde tu proveedor)"]


def test_cap_drops_inactive_or_unknown_products():
    result = cap_to_available([{"product_id": 9, "cases": 1, "reason": "r"}], {1: product(1, None)})
    assert result.lines == []


# --- Avisos y /catalogo ------------------------------------------------------


def test_price_changed_text():
    text = texts.price_changed("Jabón Rey x 3", Decimal("5500"), Decimal("6000"))
    assert text == "💲 Cambió el precio de Jabón Rey x 3: antes $5.500, ahora $6.000 la unidad."


def test_new_product_text():
    text = texts.new_product("Jabón Rey x 3", Decimal("500"), 12)
    assert text.startswith("🆕 Tu proveedor tiene un producto nuevo: Jabón Rey x 3, a $500 la unidad (caja de 12).")


def test_catalog_marks_sold_out():
    text = texts.catalog_list([product(1, None, name="Coca"), product(2, 5, name="Aceite", price="28000")])
    assert text.splitlines() == [
        "🛒 Lo que tiene tu proveedor (precio por unidad):",
        "• Coca: $4.500",
        "• Aceite: $28.000 — agotado",
    ]


def test_order_texts_show_availability_notes():
    from datetime import date, datetime

    from app.inventory.engine import COLOMBIA_TZ

    deadline = datetime(2026, 10, 7, 18, tzinfo=COLOMBIA_TZ)
    draft = texts.order_draft([], Decimal("0"), date(2026, 10, 8), deadline, availability_notes=["Aceite (agotado donde tu proveedor)"])
    assert "Ajusté según lo que tiene tu proveedor: Aceite (agotado donde tu proveedor)." in draft
    sent = texts.order_sent(date(2026, 10, 8), False, ["Coca (solo hay 2 cajas)"])
    assert sent.endswith("Ajusté según lo que tiene tu proveedor: Coca (solo hay 2 cajas).")


# --- Guardar todo el catálogo de una vez -------------------------------------


def catalog_product(pid, price="4500", available=None, active=True, name=None):
    return SimpleNamespace(id=pid, name=name or f"Producto {pid}", unit_price=Decimal(price),
                           available_units=available, active=active, units_per_case=12)


def test_plan_only_includes_changed_products():
    from app.web.catalog import plan_catalog_changes

    products = [catalog_product(1), catalog_product(2, available=100), catalog_product(3)]
    form = {
        "price-1": "4.500", "available-1": "", "active-1": "on",  # sin cambios
        "price-2": "4500", "available-2": "80", "active-2": "on",  # solo existencias
        "price-3": "5000", "available-3": "", "active-3": "on",  # precio nuevo
    }
    plan = plan_catalog_changes(products, form)
    assert plan.errors == []
    assert [(c.product_id, c.unit_price, c.available_units) for c in plan.changes] == [
        (2, Decimal("4500"), 80),
        (3, Decimal("5000"), None),
    ]
    assert plan.changes[0].notice is None  # cambiar existencias no avisa
    assert plan.changes[1].notice == texts.price_changed("Producto 3", Decimal("4500"), Decimal("5000"))


def test_plan_reactivation_notifies_and_deactivation_does_not():
    from app.web.catalog import plan_catalog_changes

    products = [catalog_product(1, active=False, name="Jabón"), catalog_product(2)]
    form = {"price-1": "4500", "available-1": "", "active-1": "on", "price-2": "4500", "available-2": ""}
    plan = plan_catalog_changes(products, form)
    assert [(c.product_id, c.active) for c in plan.changes] == [(1, True), (2, False)]
    assert plan.changes[0].notice.startswith("🆕 Tu proveedor tiene un producto nuevo: Jabón")
    assert plan.changes[1].notice is None


def test_plan_collects_every_error():
    from app.web.catalog import plan_catalog_changes

    products = [catalog_product(1, name="Coca"), catalog_product(2, name="Pan"), catalog_product(3)]
    form = {"price-1": "abc", "available-1": "", "active-1": "on",
            "price-2": "4500", "available-2": "-3", "active-2": "on",
            "price-3": "9000", "available-3": "", "active-3": "on"}
    plan = plan_catalog_changes(products, form)
    assert plan.error_ids == {1, 2}
    assert len(plan.errors) == 2 and plan.errors[0].startswith("Coca:") and plan.errors[1].startswith("Pan:")


def test_catalog_notice_combines_several_changes():
    assert texts.catalog_notice(["uno"]) == "uno"
    combined = texts.catalog_notice(["💲 Cambió A", "🆕 Nuevo B"])
    assert combined == "📣 Novedades de tu proveedor:\n\n💲 Cambió A\n\n🆕 Nuevo B"
