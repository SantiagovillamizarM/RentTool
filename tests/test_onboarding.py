"""Solicitudes de acceso: formulario público, aprobación del distribuidor y avisos al tendero."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app import onboarding
from app.bot import handlers, texts
from app.models import Store
from app.web.store_forms import parse_coordinate, parse_document, parse_store_form, parse_store_request

VALID_REQUEST = {
    "store_name": "Tienda La Esperanza",
    "owner_name": "Rosa Pérez",
    "document_id": "1.098.765.432",
    "phone": "300 123 4567",
    "email": "Rosa@Gmail.com ",
    "city": "Bucaramanga",
    "neighborhood": "San Francisco",
    "address": "Calle 45 # 12-30",
    "receiving_from": "07:00",
    "receiving_to": "12:00",
    "payment_method": "contado",
    "latitude": "7.119349",
    "longitude": "-73.122741",
    "consent": "on",
}


# --- Formulario público -------------------------------------------------------


def test_valid_request_is_cleaned():
    data, errors = parse_store_request(VALID_REQUEST)
    assert errors == []
    assert data["phone"] == "573001234567"
    assert data["email"] == "rosa@gmail.com"
    assert data["document_id"] == "1098765432"
    assert data["latitude"] == Decimal("7.119349")


def test_request_lists_every_problem_at_once():
    form = {**VALID_REQUEST, "phone": "123", "email": "rosa", "address": " ", "consent": ""}
    _data, errors = parse_store_request(form)
    assert len(errors) == 4
    assert any("celular" in error for error in errors)
    assert any("correo" in error for error in errors)
    assert "Falta la dirección." in errors
    assert any("tratamiento de tus datos" in error for error in errors)


def test_location_is_optional_and_both_or_nothing():
    data, errors = parse_store_request({**VALID_REQUEST, "latitude": "", "longitude": ""})
    assert errors == [] and data["latitude"] is None
    data, _ = parse_store_request({**VALID_REQUEST, "longitude": "abc"})
    assert data["latitude"] is None and data["longitude"] is None
    assert parse_coordinate("200", 90) is None


def test_receiving_hours_must_be_ranges_inside_delivery_time():
    data, errors = parse_store_request(VALID_REQUEST)
    assert data["receiving_hours"] == "07:00-12:00"
    _data, errors = parse_store_request({**VALID_REQUEST, "receiving_to": ""})
    assert errors == ["Escribe desde qué hora y hasta qué hora puedes recibir la mercancía."]
    _data, errors = parse_store_request({**VALID_REQUEST, "receiving_from": "00:00", "receiving_to": "01:00"})
    assert errors == ["Los camiones reparten de 6 a. m. a 8 p. m. Escribe horas dentro de ese horario."]


def test_document_keeps_nit_check_digit():
    assert parse_document("900.123.456-7") == "900123456-7"
    with pytest.raises(ValueError):
        parse_document("12")


def test_panel_store_form_needs_route_days_and_optional_phone():
    form = {"name": "Tienda", "owner_name": "Ana", "lead_time_days": "1", "phone": ""}
    data, errors = parse_store_form(form, [], with_phone=True)
    assert errors == ["Marca al menos un día en que tu camión pasa por esa tienda."]
    data, errors = parse_store_form(form, ["4", "0", "9"], with_phone=True)
    assert errors == []
    assert data["route_days"] == [0, 4]
    assert data["owner_phone"] is None
    assert data["email"] is None


def test_rejection_needs_a_reason():
    with pytest.raises(onboarding.DecisionError):
        onboarding.parse_rejection("   ")
    assert onboarding.parse_rejection("  Nuestra ruta   no llega ") == "Nuestra ruta no llega"


# --- Aprobar ------------------------------------------------------------------


def store_request(**overrides):
    values = dict(
        id=3, distributor_id=1, status="pending", store_name="Tienda La Esperanza", owner_name="Rosa",
        document_id="1098765432", phone="573001234567", email="rosa@gmail.com", city="Bucaramanga",
        neighborhood="San Francisco", address="Calle 45 # 12-30", receiving_hours="7 a 12", payment_method="contado",
        latitude=None, longitude=None, telegram_chat_id=555, store_id=None, decided_at=None, rejection_reason=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class ApproveSession:
    def __init__(self, answers):
        self.answers = list(answers)  # respuestas de cada scalar(): celular ocupado, chat ocupado
        self.added = []

    async def scalar(self, statement):
        return self.answers.pop(0)

    def add(self, row):
        row.id = 99
        self.added.append(row)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_approve_creates_store_linked_to_the_chat_that_asked():
    session = ApproveSession([None, None])
    request = store_request()
    store = await onboarding.approve_request(session, request, onboarding.parse_approval(["0", "3"], "1"))
    assert isinstance(store, Store)
    assert (store.owner_phone, store.telegram_chat_id) == ("573001234567", 555)
    assert store.route_weekdays == "0,3"
    assert store.consent_at is None  # el permiso de datos se pide en el bot
    assert (request.status, request.store_id) == ("approved", 99)


@pytest.mark.asyncio
async def test_approve_fails_if_phone_already_has_a_store():
    session = ApproveSession(["Tienda Doña Marta"])
    request = store_request()
    with pytest.raises(onboarding.DecisionError, match="Tienda Doña Marta"):
        await onboarding.approve_request(session, request, onboarding.parse_approval(["0"], "1"))
    assert request.status == "pending"


# --- Avisos -------------------------------------------------------------------


class FakeBot:
    username = "TenderoPruebaBot"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_approval_by_telegram_asks_for_consent_next():
    bot = FakeBot()
    request = store_request(status="approved")
    store = SimpleNamespace(telegram_chat_id=555)
    assert await onboarding._notify_by_telegram(bot, request, "Distribuidora El Barrio", store)
    assert bot.sent == [
        (555, texts.request_approved("Distribuidora El Barrio", "Tienda La Esperanza")),
        (555, texts.CONSENT_NOTICE),
    ]


@pytest.mark.asyncio
async def test_without_chat_the_notice_goes_by_email():
    assert not await onboarding._notify_by_telegram(FakeBot(), store_request(telegram_chat_id=None), "D", None)
    email = onboarding.decision_email(store_request(status="approved"), "Distribuidora El Barrio", "https://t.me/X")
    assert "aprobada" in email.subject
    assert "https://t.me/X" in email.text and "Compartir mi número" in email.text
    assert 'href="https://t.me/X"' in email.html and "Distribuidora El Barrio" in email.html
    email = onboarding.decision_email(
        store_request(status="rejected", rejection_reason="No llegamos a <tu> barrio"), "D", ""
    )
    assert "Motivo: No llegamos a <tu> barrio" in email.text
    # Lo que escribe el distribuidor se escapa en el HTML.
    assert "No llegamos a &lt;tu&gt; barrio" in email.html


def test_reminder_email_points_to_the_bot():
    email = onboarding.reminder_email(store_request(), "https://t.me/TenderoPruebaBot")
    assert "Abre TenderoBot" in email.subject
    assert "https://t.me/TenderoPruebaBot" in email.text
    assert 'href="https://t.me/TenderoPruebaBot"' in email.html


@pytest.mark.asyncio
async def test_email_is_skipped_without_smtp(monkeypatch):
    from app import mailer

    monkeypatch.setattr(mailer, "email_enabled", lambda: False)
    assert await mailer.send_email("rosa@gmail.com", onboarding.reminder_email(store_request(), "")) is False


def test_message_has_text_html_and_inline_logo():
    from app import mailer

    message = mailer.build_message("rosa@gmail.com", onboarding.reminder_email(store_request(), ""))
    assert message.get_content_type() == "multipart/alternative"
    text, html = message.get_payload()
    assert text.get_content_type() == "text/plain"
    assert html.get_content_type() == "multipart/related"
    body, logo = html.get_payload()
    assert "cid:tenderobot-logo" in body.get_content()
    assert logo["Content-ID"] == "<tenderobot-logo>"


# --- Bot ----------------------------------------------------------------------


def contact_update():
    from telegram import Update

    message = {
        "message_id": 1, "date": 0, "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Rosa"},
        "contact": {"first_name": "Rosa", "phone_number": "+57 300 123 4567", "user_id": 555},
    }
    return Update.de_json({"update_id": 1, "message": message}, None)


def bot_with_request(monkeypatch, request):
    replies: list[str] = []

    class NoStoreSession:
        async def scalar(self, statement):
            return None

    @asynccontextmanager
    async def fake_scope():
        yield NoStoreSession()

    async def no_store(session, chat_id):
        return None

    async def find(session, phone):
        return request

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", no_store)
    monkeypatch.setattr(handlers, "find_request_by_phone", find)
    monkeypatch.setattr(handlers, "_reply", record)
    return replies


@pytest.mark.asyncio
async def test_sharing_phone_with_request_in_review_saves_the_chat(monkeypatch):
    request = store_request(telegram_chat_id=None)
    replies = bot_with_request(monkeypatch, request)
    await handlers.handle_contact(contact_update(), None)
    assert request.telegram_chat_id == 555
    assert replies == [texts.REQUEST_IN_REVIEW]


@pytest.mark.asyncio
async def test_sharing_phone_after_rejection_shows_the_reason(monkeypatch):
    request = store_request(status="rejected", rejection_reason="Falta la dirección exacta")
    replies = bot_with_request(monkeypatch, request)
    await handlers.handle_contact(contact_update(), None)
    assert len(replies) == 1 and "Motivo: Falta la dirección exacta" in replies[0]


@pytest.mark.asyncio
async def test_chat_waiting_for_answer_is_told_it_is_in_review(monkeypatch):
    from telegram.ext import ApplicationHandlerStop

    from tests.test_bot_gate import message_update

    replies = bot_with_request(monkeypatch, None)

    async def in_review(session, chat_id):
        return True

    monkeypatch.setattr(handlers, "has_request_in_review", in_review)
    with pytest.raises(ApplicationHandlerStop):
        await handlers.require_link(message_update("hola"), None)
    assert replies == [texts.REQUEST_IN_REVIEW]


# --- Panel --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_pages_need_login():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in [
            ("GET", "/panel/solicitudes"),
            ("POST", "/panel/solicitudes/1/aprobar"),
            ("POST", "/panel/solicitudes/1/rechazar"),
            ("POST", "/panel/tiendas/nueva"),
            ("POST", "/panel/tiendas/1/datos"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 303, path
            assert response.headers["location"] == "/login"


def test_texts_for_requests():
    assert "solicitud" in texts.REQUEST_IN_REVIEW
    assert texts.phone_not_registered("https://x.co/#solicitar").endswith("vuelve a tocar 📱 Compartir mi número.")
    assert "https://x.co/#solicitar" in texts.phone_not_registered("https://x.co/#solicitar")
    assert onboarding.bot_link("TenderoBot") == "https://t.me/TenderoBot"
    assert onboarding.bot_link(None) == ""
