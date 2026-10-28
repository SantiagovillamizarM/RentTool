import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.grok_client import extract_order_changes
from app.ai.matching import match_order_changes
from app.bot import texts
from app.bot.reminders import idle_notice_due, reminder_due
from app.inventory.engine import COLOMBIA_TZ, delivery_schedule
from app.inventory.orders import ADDED_BY_YOU, apply_order_changes, order_total, to_order_lines
from app.schemas import OrderChange

COCA = SimpleNamespace(id=1, sku="GAS-COCA-1L", name="Coca-Cola 1 litro", units_per_case=12, unit_price=Decimal("4500"))
ARROZ = SimpleNamespace(id=2, sku="ARR-ROSA-500", name="Arroz Diana 500 g", units_per_case=20, unit_price=Decimal("2800"))
HUEVOS = SimpleNamespace(id=3, sku="HUE-AAA-30", name="Huevos AAA x 30", units_per_case=1, unit_price=Decimal("18000"))
BY_SKU = {p.sku: p for p in (COCA, ARROZ, HUEVOS)}
BY_ID = {p.id: p for p in (COCA, ARROZ, HUEVOS)}
CATALOG = [{"sku": p.sku, "name": p.name, "aliases": ""} for p in (COCA, ARROZ, HUEVOS)]

DRAFT = [
    {"product_id": 1, "cases": 2, "reason": "Vendes 6 al día, te alcanza para 1 día"},
    {"product_id": 2, "cases": 1, "reason": "Vendes 3 al día, te alcanza para 2 días"},
]


def change(sku, action, quantity=1, unit="case", text="producto"):
    return OrderChange(sku=sku, product_text=text, action=action, quantity=quantity, unit=unit)


# --- Aplicar cambios (código, sin IA) ----------------------------------------


def test_remove_drops_the_line():
    result = apply_order_changes(DRAFT, [change("ARR-ROSA-500", "remove", 0)], BY_SKU)
    assert [line["product_id"] for line in result.lines] == [1]


def test_add_sums_cases_and_keeps_reason():
    result = apply_order_changes(DRAFT, [change("GAS-COCA-1L", "add", 2)], BY_SKU)
    assert result.lines[0] == {"product_id": 1, "cases": 4, "reason": DRAFT[0]["reason"]}


def test_set_replaces_cases():
    result = apply_order_changes(DRAFT, [change("GAS-COCA-1L", "set", 5)], BY_SKU)
    assert result.lines[0]["cases"] == 5


def test_set_zero_removes():
    result = apply_order_changes(DRAFT, [change("GAS-COCA-1L", "set", 0)], BY_SKU)
    assert [line["product_id"] for line in result.lines] == [2]


def test_new_product_is_added_with_reason():
    result = apply_order_changes(DRAFT, [change("HUE-AAA-30", "add", 1)], BY_SKU)
    assert result.lines[-1] == {"product_id": 3, "cases": 1, "reason": ADDED_BY_YOU}


def test_loose_units_round_up_to_cases():
    # 6 cocas sueltas -> 1 caja de 12; 25 arroces -> 2 cajas de 20.
    result = apply_order_changes([], [change("GAS-COCA-1L", "add", 6, "unit"), change("ARR-ROSA-500", "set", 25, "unit")], BY_SKU)
    assert [(line["product_id"], line["cases"]) for line in result.lines] == [(1, 1), (2, 2)]


def test_unknown_product_is_reported_and_not_added():
    result = apply_order_changes(DRAFT, [change(None, "add", 1, text="unicornio")], BY_SKU)
    assert result.lines == DRAFT
    assert result.not_found == ["unicornio"]


def test_draft_is_not_mutated():
    original = [dict(line) for line in DRAFT]
    apply_order_changes(DRAFT, [change("GAS-COCA-1L", "add", 3)], BY_SKU)
    assert DRAFT == original


def test_to_order_lines_uses_catalog_units_and_prices():
    lines = to_order_lines(DRAFT, BY_ID)
    assert [(line.units, line.subtotal) for line in lines] == [(24, Decimal("108000")), (20, Decimal("56000"))]
    assert order_total(lines) == Decimal("164000")


# --- Grok simulado -----------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_order_changes_validates_and_matches():
    payload = {"changes": [{"sku": None, "product_text": "Coca-Cola 1 litro", "action": "add", "quantity": 2, "unit": "case"}]}
    with patch("app.ai.grok_client._chat_completion", new=AsyncMock(return_value=json.dumps(payload))) as chat:
        changes = await extract_order_changes(CATALOG, "ponle 2 cajas más de coca", "- sku=GAS-COCA-1L | 2 cajas")
    assert "ponle 2 cajas más de coca" in chat.await_args.args[0]
    assert "GAS-COCA-1L | 2 cajas" in chat.await_args.args[0]
    assert match_order_changes(changes, CATALOG)[0].sku == "GAS-COCA-1L"


@pytest.mark.asyncio
async def test_extract_order_changes_empty_when_not_about_order():
    with patch("app.ai.grok_client._chat_completion", new=AsyncMock(return_value='{"changes": []}')):
        assert await extract_order_changes(CATALOG, "vendí 2 cocas", "(vacío)") == []


# --- Textos ------------------------------------------------------------------


def test_date_labels():
    assert texts.day_label(date(2026, 10, 8)) == "jueves 8/10"
    assert texts.hour_label(18) == "6 p. m."
    assert texts.hour_label(9) == "9 a. m."
    assert texts.deadline_label(datetime(2026, 10, 7, 18, tzinfo=COLOMBIA_TZ)) == "miércoles 7/10 a las 6 p. m."


def test_order_draft_shows_deadline_and_edit_hint():
    deadline = datetime(2026, 10, 7, 18, tzinfo=COLOMBIA_TZ)
    lines = to_order_lines(DRAFT, BY_ID)
    text = texts.order_draft(lines, order_total(lines), date(2026, 10, 8), deadline, ["unicornio"])
    assert text.startswith("📦 Pedido para el jueves 8/10:")
    assert "• 2 cajas Coca-Cola 1 litro — Vendes 6 al día" in text
    assert "Total: $164.000 COP" in text
    assert "No encontré en el catálogo: unicornio." in text
    assert "quita el arroz" in text
    assert text.endswith("Tienes hasta el miércoles 7/10 a las 6 p. m. para enviarlo.")


def test_empty_order_draft_still_lets_add():
    text = texts.order_draft([], Decimal("0"), date(2026, 10, 8), datetime(2026, 10, 7, 18, tzinfo=COLOMBIA_TZ))
    assert text.startswith("📦 Por ahora no necesitas pedir nada para el jueves 8/10.")
    assert "agrega una cubeta de huevos" in text


# --- Recordatorio ------------------------------------------------------------


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=COLOMBIA_TZ)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(7, 8, 59), False),  # miércoles antes de las 9 a. m.
        (at(7, 9), True),  # miércoles 9 a. m.: entrega el jueves, límite hoy 6 p. m.
        (at(7, 17, 59), True),
        (at(6, 12), False),  # martes: todavía no es el día de la hora límite
    ],
)
def test_reminder_window_on_deadline_day(now, expected):
    schedule = delivery_schedule("0,3", 1, now)
    assert reminder_due(schedule, now, None) is expected


def test_reminder_only_once_per_delivery():
    now = at(7, 10)
    schedule = delivery_schedule("0,3", 1, now)
    assert schedule.next_delivery == date(2026, 10, 8)
    assert not reminder_due(schedule, now, date(2026, 10, 8))
    assert reminder_due(schedule, now, date(2026, 10, 5))


def test_reminder_after_deadline_targets_next_delivery():
    # Miércoles 6 p. m. ya pasó la hora del jueves; el próximo límite es el domingo.
    now = at(7, 18) + timedelta(minutes=1)
    schedule = delivery_schedule("0,3", 1, now)
    assert schedule.next_delivery == date(2026, 10, 12)
    assert not reminder_due(schedule, now, None)
    assert reminder_due(schedule, at(11, 9), None)


def test_reminder_text():
    text = texts.order_reminder(date(2026, 10, 8), datetime(2026, 10, 7, 18, tzinfo=COLOMBIA_TZ))
    assert text.startswith("⏰ El jueves 8/10 te llega mercancía.")
    assert "Escribe /pedido" in text


def test_idle_notice_after_a_day_without_activity():
    last = at(6, 10)
    assert not idle_notice_due(last, None, at(7, 9, 59))  # aún no pasan 24 horas
    assert idle_notice_due(last, None, at(7, 10))
    assert not idle_notice_due(None, None, at(7, 10))  # chat sin actividad registrada


def test_idle_notice_only_during_the_day():
    last = at(5, 12)
    assert not idle_notice_due(last, None, at(7, 8, 59))
    assert idle_notice_due(last, None, at(7, 9))
    assert idle_notice_due(last, None, at(7, 18, 59))
    assert not idle_notice_due(last, None, at(7, 19))


def test_idle_notice_once_per_idle_period():
    last = at(5, 12)
    assert not idle_notice_due(last, at(6, 12), at(7, 12))  # ya se avisó y no ha vuelto
    # Volvió a usar el bot después del aviso: se puede avisar de nuevo tras otras 24 horas.
    assert idle_notice_due(at(6, 13), at(6, 12), at(7, 13))


def test_idle_notice_text():
    text = texts.idle_notice("Tienda Doña Marta")
    assert "La sesión de Tienda Doña Marta sigue abierta" in text
