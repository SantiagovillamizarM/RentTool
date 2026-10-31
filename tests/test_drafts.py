"""Botones atados a su borrador, un pedido por entrega y /recibido con revisión."""

import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot import handlers, keyboards, texts
from app.models import Receipt


class ButtonQuery:
    def __init__(self, data: str):
        self.data = data
        self.removed = False

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_reply_markup(self, reply_markup=None):
        if reply_markup is None:
            self.removed = True


def button(data: str):
    return SimpleNamespace(callback_query=ButtonQuery(data), effective_chat=SimpleNamespace(id=555), effective_message=None)


class FakeSession:
    def __init__(self, products=(), orders=None):
        self.products = list(products)
        self.orders = orders or {}
        self.added: list = []
        self.deleted: list = []

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def get(self, model, pk):
        return self.orders.get(pk)

    async def scalars(self, statement):
        return SimpleNamespace(all=lambda: self.products)


def setup(monkeypatch, session, draft=None, sent_for=False, following=None):
    """Tienda lista, sesión falsa y las consultas del borrador y los pedidos sustituidas."""
    replies: list[str] = []
    store = SimpleNamespace(id=7, distributor_id=1, visit_weekdays="0,3", lead_time_days=1)

    @asynccontextmanager
    async def fake_scope():
        yield session

    async def ready(update, session):
        return store

    async def button_draft(update, session, store_id, kind):
        # Igual que la consulta real: el id del botón debe ser el del borrador vigente y del mismo tipo.
        pending_id = keyboards.draft_id(update.callback_query.data)
        if draft is not None and draft.id == pending_id and draft.kind == kind:
            return draft
        return None

    async def sent(session, store_id, delivery):
        return sent_for

    async def oldest(session, store_id, exclude_id=None):
        return following

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "require_ready_store", ready)
    monkeypatch.setattr(handlers, "get_button_draft", button_draft)
    monkeypatch.setattr(handlers, "order_sent_for", sent)
    monkeypatch.setattr(handlers, "oldest_sent_order", oldest)
    monkeypatch.setattr(handlers, "_reply", record)
    return replies


# --- Botones con el id del borrador -------------------------------------------


def test_buttons_carry_the_draft_id():
    markup = keyboards.confirm_sales_keyboard(42)
    confirm, cancel = markup.inline_keyboard[0]
    assert (confirm.callback_data, cancel.callback_data) == ("sales:confirm:42", "sales:cancel:42")
    assert keyboards.draft_id("sales:confirm:42") == 42
    # Botones de mensajes viejos (sin id) no apuntan a ningún borrador.
    assert keyboards.draft_id("sales:confirm") is None
    assert keyboards.draft_id("sales:confirm:x") is None


def test_draft_pattern_accepts_old_and_new_buttons_of_its_action_only():
    pattern = re.compile(keyboards.draft_pattern(keyboards.SEND_ORDER))
    assert pattern.match("order:send:7")
    assert pattern.match("order:send")
    assert not pattern.match("order:cancel:7")


@pytest.mark.asyncio
async def test_old_summary_button_does_not_confirm_newer_sales(monkeypatch):
    # El tendero mandó "2 cocas" (borrador 1) y luego "3 panes" (borrador 2); pulsa ✅ en el primero.
    newer = SimpleNamespace(id=2, kind="sales", payload={"source": "text", "lines": [{"sku": "PAN", "quantity": 3}]})
    session = FakeSession()
    replies = setup(monkeypatch, session, draft=newer)
    update = button("sales:confirm:1")
    await handlers.handle_confirm_sales(update, None)
    assert session.added == []
    assert session.deleted == []
    assert replies == [texts.STALE_BUTTON]
    assert update.callback_query.removed


@pytest.mark.asyncio
async def test_confirm_sales_saves_its_own_draft_and_removes_buttons(monkeypatch):
    product = SimpleNamespace(id=10, sku="PAN", unit_price=Decimal("500"))
    draft = SimpleNamespace(id=2, kind="sales", payload={"source": "text", "lines": [{"sku": "PAN", "quantity": 3}]})
    session = FakeSession(products=[product])
    replies = setup(monkeypatch, session, draft=draft)
    update = button("sales:confirm:2")
    await handlers.handle_confirm_sales(update, None)
    assert [(sale.product_id, sale.quantity) for sale in session.added] == [(10, 3)]
    assert session.deleted == [draft]
    assert replies == [texts.SAVED_SALES]
    assert update.callback_query.removed


@pytest.mark.asyncio
async def test_stock_button_cannot_confirm_a_sales_draft(monkeypatch):
    draft = SimpleNamespace(id=5, kind="sales", payload={"lines": []})
    session = FakeSession()
    replies = setup(monkeypatch, session, draft=draft)
    await handlers.handle_confirm_stock(button("stock:confirm:5"), None)
    assert session.added == []
    assert replies == [texts.STALE_BUTTON]


# --- Un pedido por entrega --------------------------------------------------


@pytest.mark.asyncio
async def test_pedido_after_sending_does_not_suggest_again(monkeypatch):
    delivery = date(2026, 11, 5)
    session = FakeSession()
    replies = setup(monkeypatch, session, sent_for=True)

    async def analysis(session, store_id):
        return SimpleNamespace(next_delivery=delivery)

    monkeypatch.setattr(handlers, "load_store_analysis", analysis)
    await handlers.handle_order(button("x"), None)
    assert replies == [texts.order_already_sent(delivery)]
    assert session.added == []


@pytest.mark.asyncio
async def test_second_draft_cannot_send_another_order_for_same_delivery(monkeypatch):
    draft = SimpleNamespace(id=3, kind="order", payload={"lines": [{"product_id": 10, "cases": 1}]})
    session = FakeSession()
    replies = setup(monkeypatch, session, draft=draft, sent_for=True)
    update = button("order:send:3")
    await handlers.handle_send_order(update, None)
    assert session.added == []
    assert session.deleted == [draft]
    assert len(replies) == 1 and replies[0].startswith("Ya enviaste el pedido")
    assert update.callback_query.removed


# --- /recibido con revisión -------------------------------------------------


def receipt_draft(units_coca=12):
    return SimpleNamespace(
        id=9,
        kind="receipt",
        payload={
            "order_id": 50,
            "delivery_date": "2026-11-05",
            "lines": [
                {"product_id": 10, "name": "Coca-Cola 1L", "units": units_coca, "ordered": 12},
                {"product_id": 11, "name": "Pan tajado", "units": 6, "ordered": 6},
            ],
        },
    )


def test_receipt_summary_shows_partial_lines():
    lines = receipt_draft(units_coca=8).payload["lines"]
    text = texts.receipt_summary(lines, date(2026, 11, 5))
    assert text.startswith("📦 Pedido del jueves 5/11. ¿Llegó así?")
    assert "• Coca-Cola 1L: llegaron 8 de 12 unidades" in text
    assert "• Pan tajado: 6 unidades" in text


@pytest.mark.asyncio
async def test_partial_receipt_adds_only_what_arrived(monkeypatch):
    order = SimpleNamespace(id=50, store_id=7, status="sent")
    draft = receipt_draft(units_coca=8)
    session = FakeSession(orders={50: order})
    following = SimpleNamespace(delivery_date=date(2026, 11, 9))
    replies = setup(monkeypatch, session, draft=draft, following=following)
    update = button("receipt:confirm:9")
    await handlers.handle_confirm_receipt(update, None)
    receipts = [(r.product_id, r.quantity) for r in session.added if isinstance(r, Receipt)]
    assert receipts == [(10, 8), (11, 6)]
    assert order.status == "received"
    assert replies == [texts.receipt_saved(["Coca-Cola 1L (4 de 12)"], date(2026, 11, 9))]
    assert "Te queda otro pedido en camino para el lunes 9/11" in replies[0]
    assert update.callback_query.removed


@pytest.mark.asyncio
async def test_receipt_of_order_already_received_is_not_added_twice(monkeypatch):
    order = SimpleNamespace(id=50, store_id=7, status="received")
    session = FakeSession(orders={50: order})
    replies = setup(monkeypatch, session, draft=receipt_draft())
    await handlers.handle_confirm_receipt(button("receipt:confirm:9"), None)
    assert session.added == []
    assert replies == [texts.STALE_BUTTON]


@pytest.mark.asyncio
async def test_receipt_correction_changes_units_of_that_product(monkeypatch):
    products = [
        SimpleNamespace(id=10, sku="COCA", name="Coca-Cola 1L", aliases="coca litro", presentation="1L"),
        SimpleNamespace(id=11, sku="PAN", name="Pan tajado", aliases="pan", presentation="und"),
    ]
    session = FakeSession(products=products)
    replies = setup(monkeypatch, session)
    saved: list[dict] = []

    async def replace(session, store_id, kind, payload, ttl=None):
        saved.append(payload)
        return SimpleNamespace(id=10)

    monkeypatch.setattr(handlers, "replace_pending", replace)
    store = SimpleNamespace(id=7, distributor_id=1)
    await handlers.apply_receipt_correction(button("x"), session, store, receipt_draft(), ("coca litro", 8))
    assert [line["units"] for line in saved[0]["lines"]] == [8, 6]
    assert "llegaron 8 de 12" in replies[0]

    replies.clear()
    await handlers.apply_receipt_correction(button("x"), session, store, receipt_draft(), ("arroz diana", 3))
    assert replies == [texts.RECEIPT_NOT_IN_ORDER]


def test_receipt_saved_complete():
    assert texts.receipt_saved([]) == texts.ORDER_RECEIVED
