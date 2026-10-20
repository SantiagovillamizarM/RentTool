from contextlib import asynccontextmanager

import pytest
from telegram import Update
from telegram.ext import ApplicationHandlerStop

from app.bot import handlers, texts


def message_update(text: str | None = None, voice: bool = False) -> Update:
    message = {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Tendero"},
    }
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if voice:
        message["voice"] = {"file_id": "v", "file_unique_id": "v", "duration": 3}
    return Update.de_json({"update_id": 1, "message": message}, None)


@pytest.fixture
def unlinked(monkeypatch):
    """Chat sin tienda: sin base de datos y guardando las respuestas en una lista."""
    replies: list[str] = []

    @asynccontextmanager
    async def fake_scope():
        yield None

    async def no_store(session, chat_id):
        return None

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", no_store)
    monkeypatch.setattr(handlers, "_reply", record)
    return replies


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/start", "/start@CO_Tendero_Bot", "MARTA01", "cualquier cosa"])
async def test_unlinked_chat_lets_start_and_codes_through(unlinked, text):
    await handlers.require_link(message_update(text), None)
    assert unlinked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("update_factory", [
    lambda: message_update("/ayuda"),
    lambda: message_update("/stock"),
    lambda: message_update(voice=True),
])
async def test_unlinked_chat_is_asked_for_store_code(unlinked, update_factory):
    with pytest.raises(ApplicationHandlerStop):
        await handlers.require_link(update_factory(), None)
    assert unlinked == [texts.ASK_STORE]


def test_texts():
    assert texts.UNKNOWN_CODE == "Código incorrecto o no registrado."
    assert texts.ASK_CODE.endswith("(Proporciona el código de tu tienda)")


class FakeQuery:
    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_reply_markup(self, reply_markup=None):
        return None


def callback_update():
    from types import SimpleNamespace

    return SimpleNamespace(callback_query=FakeQuery(), effective_chat=SimpleNamespace(id=555))


def patch_store(monkeypatch, store):
    replies: list[str] = []

    @asynccontextmanager
    async def fake_scope():
        yield None

    async def get_store(session, chat_id):
        return store

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", get_store)
    monkeypatch.setattr(handlers, "_reply", record)
    return replies


@pytest.mark.asyncio
async def test_reject_consent_unlinks_and_asks_code_again(monkeypatch):
    from types import SimpleNamespace

    store = SimpleNamespace(telegram_chat_id=555, consent_at=None)
    replies = patch_store(monkeypatch, store)
    await handlers.handle_reject_consent(callback_update(), None)
    assert store.telegram_chat_id is None
    assert replies == [texts.CONSENT_REJECTED]


@pytest.mark.asyncio
async def test_reject_after_accepting_keeps_link(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    store = SimpleNamespace(telegram_chat_id=555, consent_at=datetime.now(UTC))
    replies = patch_store(monkeypatch, store)
    await handlers.handle_reject_consent(callback_update(), None)
    assert store.telegram_chat_id == 555
    assert replies == [texts.CONSENT_ALREADY]


# --- Días de entrega ---------------------------------------------------------


class DaysQuery:
    def __init__(self, data):
        self.data = data
        self.alerts: list[str] = []
        self.markup = None
        self.removed = False

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        if reply_markup is None:
            self.removed = True
        self.markup = reply_markup


def days_update(data):
    from types import SimpleNamespace

    return SimpleNamespace(callback_query=DaysQuery(data), effective_chat=SimpleNamespace(id=555))


def days_store(**overrides):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    values = dict(
        consent_at=datetime.now(UTC),
        delivery_days_set_at=None,
        route_weekdays="0,2,4",
        visit_weekdays="0,3",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_days_texts():
    assert texts.join_days([4, 0, 2]) == "lunes, miércoles y viernes"
    assert texts.join_days([5]) == "sábado"
    assert texts.ask_days([0, 2, 4]).startswith("📅 Tu proveedor pasa por tu barrio los lunes, miércoles y viernes.")


@pytest.mark.asyncio
async def test_days_toggle_marks_and_unmarks_without_saving(monkeypatch):
    from app.bot import keyboards

    store = days_store()
    patch_store(monkeypatch, store)
    update = days_update("days:0:4")
    await handlers.handle_days(update, None)
    buttons = [row[0] for row in update.callback_query.markup.inline_keyboard]
    assert [b.text for b in buttons] == ["✅ Lunes", "⬜ Miércoles", "✅ Viernes", "Listo"]
    assert buttons[-1].callback_data == "days:04:ok"
    assert keyboards.parse_days_callback("days:04:0") == ([0, 4], "0")
    assert store.delivery_days_set_at is None


@pytest.mark.asyncio
async def test_days_ignores_days_outside_route(monkeypatch):
    store = days_store()
    patch_store(monkeypatch, store)
    update = days_update("days:16:ok")
    await handlers.handle_days(update, None)
    assert update.callback_query.alerts == [texts.DAYS_EMPTY]
    assert store.delivery_days_set_at is None


@pytest.mark.asyncio
async def test_days_ok_saves_selection(monkeypatch):
    store = days_store()
    replies = patch_store(monkeypatch, store)
    update = days_update("days:04:ok")
    await handlers.handle_days(update, None)
    assert store.visit_weekdays == "0,4"
    assert store.delivery_days_set_at is not None
    assert update.callback_query.removed
    assert replies == [texts.days_saved([0, 4])]


@pytest.mark.asyncio
async def test_days_cannot_be_changed_with_old_buttons(monkeypatch):
    from datetime import UTC, datetime

    store = days_store(delivery_days_set_at=datetime.now(UTC), visit_weekdays="0,4")
    replies = patch_store(monkeypatch, store)
    await handlers.handle_days(days_update("days:2:ok"), None)
    assert store.visit_weekdays == "0,4"
    assert replies == []


@pytest.mark.asyncio
async def test_store_without_days_is_asked_before_using_bot(monkeypatch):
    store = days_store()
    replies = patch_store(monkeypatch, store)
    assert await handlers.require_ready_store(callback_update(), None) is None
    assert replies == [texts.NEED_DAYS, texts.ask_days([0, 2, 4])]


@pytest.mark.asyncio
async def test_start_greets_before_asking_days(monkeypatch):
    store = days_store(name="Tienda Doña Marta")
    replies = patch_store(monkeypatch, store)
    await handlers.handle_start(callback_update(), None)
    assert replies == [texts.start_needs_days("Tienda Doña Marta"), texts.ask_days([0, 2, 4])]
    assert replies[0].startswith(texts.INTRO)


@pytest.mark.asyncio
async def test_start_greets_before_asking_consent(monkeypatch):
    store = days_store(consent_at=None)
    replies = patch_store(monkeypatch, store)
    await handlers.handle_start(callback_update(), None)
    assert replies == [texts.INTRO, texts.CONSENT_NOTICE]
