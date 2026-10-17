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
