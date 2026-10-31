"""Solicitudes de acceso: un tendero pide entrar desde la página y su distribuidor decide.

Avisos al tendero, siempre gratis:
- Por Telegram si ya abrió el bot y compartió su número (el chat queda en la solicitud).
- Si no, por correo: un recordatorio 24 horas después de pedir acceso y el resultado al decidir.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.bot import keyboards, texts
from app.config import get_settings
from app.db import SessionLocal
from app.mailer import email_enabled, send_email
from app.models import Distributor, Store, StoreRequest

REMINDER_AFTER = timedelta(hours=24)
# Al decidir se avisa enseguida; si ese aviso falló, la tarea de fondo lo reintenta después de esto.
DECISION_RETRY_AFTER = timedelta(minutes=5)
MAX_LEAD_TIME_DAYS = 7

logger = logging.getLogger(__name__)


def request_page_url() -> str:
    """Dirección de la página pública para pedir acceso ('' si no hay URL pública configurada)."""
    base = get_settings().public_base_url.strip().rstrip("/")
    return f"{base}/#solicitar" if base.startswith("http") else ""


def bot_link(bot_username: str | None) -> str:
    return f"https://t.me/{bot_username}" if bot_username else ""


# --- Aprobar y rechazar (panel) -----------------------------------------------


class DecisionError(ValueError):
    pass


@dataclass(frozen=True)
class Approval:
    route_days: list[int]
    lead_time_days: int


def parse_approval(route_days: list[str], lead_time: str) -> Approval:
    days = sorted({int(day) for day in route_days if day.isdigit() and 0 <= int(day) <= 6})
    if not days:
        raise DecisionError("Marca al menos un día en que tu camión pasa por esa tienda.")
    lead = (lead_time or "").strip()
    if not lead.isdigit() or not 1 <= int(lead) <= MAX_LEAD_TIME_DAYS:
        raise DecisionError(f"Los días de anticipación deben ser un número de 1 a {MAX_LEAD_TIME_DAYS}.")
    return Approval(days, int(lead))


def parse_rejection(reason: str) -> str:
    cleaned = " ".join((reason or "").split())
    if not cleaned:
        raise DecisionError("Escribe el motivo del rechazo: el tendero lo va a leer.")
    if len(cleaned) > 300:
        raise DecisionError("El motivo es muy largo (máximo 300 caracteres).")
    return cleaned


async def approve_request(session: AsyncSession, request: StoreRequest, approval: Approval) -> Store:
    """Crea la tienda con los datos de la solicitud. Si el tendero ya abrió el bot, queda vinculada a su chat."""
    taken = await session.scalar(select(Store.name).where(Store.owner_phone == request.phone))
    if taken is not None:
        raise DecisionError(f"Ese celular ya está registrado en la tienda {taken}.")
    chat_id = request.telegram_chat_id
    if chat_id is not None and await session.scalar(select(Store.id).where(Store.telegram_chat_id == chat_id)):
        chat_id = None  # ese chat ya usa otra tienda: entrará compartiendo su número
    store = Store(
        distributor_id=request.distributor_id,
        name=request.store_name,
        owner_name=request.owner_name,
        owner_phone=request.phone,
        telegram_chat_id=chat_id,
        route_weekdays=",".join(str(day) for day in approval.route_days),
        visit_weekdays=",".join(str(day) for day in approval.route_days),
        lead_time_days=approval.lead_time_days,
        last_activity_at=datetime.now(UTC) if chat_id is not None else None,
        document_id=request.document_id,
        email=request.email,
        city=request.city,
        neighborhood=request.neighborhood,
        address=request.address,
        receiving_hours=request.receiving_hours,
        payment_method=request.payment_method,
        latitude=request.latitude,
        longitude=request.longitude,
    )
    session.add(store)
    await session.flush()
    request.status = "approved"
    request.decided_at = datetime.now(UTC)
    request.store_id = store.id
    return store


def reject_request(request: StoreRequest, reason: str) -> None:
    request.status = "rejected"
    request.decided_at = datetime.now(UTC)
    request.rejection_reason = reason


# --- Correos ------------------------------------------------------------------


def reminder_email(request: StoreRequest, link: str) -> tuple[str, str]:
    open_bot = f"Ábrelo aquí: {link}" if link else "Búscalo en Telegram como TenderoBot."
    return (
        "Abre TenderoBot para saber cuándo te aprueban",
        f"Hola, {request.owner_name}.\n\n"
        f"Recibimos tu solicitud para usar TenderoBot en {request.store_name}. Tu distribuidor la está revisando.\n\n"
        "Para que te avisemos por Telegram apenas te respondan, abre el bot y toca "
        f"\"📱 Compartir mi número\" (usa el celular {request.phone}).\n{open_bot}\n\n"
        "— TenderoBot",
    )


def decision_email(request: StoreRequest, distributor: str, link: str) -> tuple[str, str]:
    if request.status == "approved":
        open_bot = f"Ábrelo aquí: {link}" if link else "Búscalo en Telegram como TenderoBot."
        return (
            "✅ Tu tienda fue aprobada en TenderoBot",
            f"Hola, {request.owner_name}.\n\n"
            f"{distributor} aprobó tu tienda {request.store_name}. Ya puedes usar TenderoBot.\n\n"
            "Abre el bot en Telegram y toca \"📱 Compartir mi número\" "
            f"(usa el celular {request.phone}).\n{open_bot}\n\n"
            "— TenderoBot",
        )
    page = request_page_url()
    retry = f"\n\nSi lo puedes corregir, envía una solicitud nueva en {page}" if page else ""
    return (
        "Tu solicitud en TenderoBot no fue aprobada",
        f"Hola, {request.owner_name}.\n\n"
        f"Tu solicitud para {request.store_name} no fue aprobada.\nMotivo: {request.rejection_reason}{retry}\n\n"
        "— TenderoBot",
    )


# --- Avisos -------------------------------------------------------------------


async def _notify_by_telegram(bot: Bot, request: StoreRequest, distributor: str, store: Store | None) -> bool:
    chat_id = request.telegram_chat_id
    if chat_id is None:
        return False
    try:
        if request.status == "approved":
            await bot.send_message(chat_id, texts.request_approved(distributor, request.store_name))
            if store is not None and store.telegram_chat_id == chat_id:
                # Ya quedó vinculada a este chat: sigue el permiso de datos y luego los días de entrega.
                await bot.send_message(chat_id, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
            else:
                await bot.send_message(chat_id, texts.ASK_LOGIN, reply_markup=keyboards.share_phone_keyboard())
        else:
            await bot.send_message(
                chat_id, texts.request_rejected(request.store_name, request.rejection_reason or "", request_page_url())
            )
    except Exception:
        logger.warning("No se pudo avisar por Telegram la solicitud %s", request.id)
        return False
    return True


async def notify_decision(bot: Bot, request_id: int) -> bool:
    """Le cuenta al tendero lo que decidió el distribuidor: por Telegram o, si no abrió el bot, por correo."""
    async with SessionLocal() as session:
        request = await session.get(StoreRequest, request_id)
        if request is None or request.status == "pending" or request.decision_notified_at is not None:
            return False
        distributor = await session.scalar(select(Distributor.name).where(Distributor.id == request.distributor_id))
        store = await session.get(Store, request.store_id) if request.store_id else None
        sent = await _notify_by_telegram(bot, request, distributor or "Tu distribuidor", store)
        if not sent:
            subject, body = decision_email(request, distributor or "Tu distribuidor", bot_link(bot.username))
            sent = await send_email(request.email, subject, body)
        if sent:
            request.decision_notified_at = datetime.now(UTC)
            await session.commit()
        return sent


async def send_request_followups(bot: Bot, now: datetime | None = None) -> int:
    """Tarea de fondo: recordatorio por correo a quien no abrió el bot y reintento de avisos que fallaron."""
    now = now or datetime.now(UTC)
    sent = 0
    async with SessionLocal() as session:
        decided = (
            await session.scalars(
                select(StoreRequest.id).where(
                    StoreRequest.status.in_(("approved", "rejected")),
                    StoreRequest.decision_notified_at.is_(None),
                    StoreRequest.decided_at <= now - DECISION_RETRY_AFTER,
                )
            )
        ).all()
        reminders = []
        if email_enabled():
            reminders = (
                await session.scalars(
                    select(StoreRequest).where(
                        StoreRequest.status == "pending",
                        StoreRequest.telegram_chat_id.is_(None),
                        StoreRequest.reminder_email_sent_at.is_(None),
                        StoreRequest.created_at <= now - REMINDER_AFTER,
                    )
                )
            ).all()
        for request in reminders:
            subject, body = reminder_email(request, bot_link(bot.username))
            if await send_email(request.email, subject, body):
                sent += 1
            # Un solo intento: un correo malo no se reintenta cada 10 minutos.
            request.reminder_email_sent_at = now
        await session.commit()
    for request_id in decided:
        if await notify_decision(bot, request_id):
            sent += 1
    return sent
