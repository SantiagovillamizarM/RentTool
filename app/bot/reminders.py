"""Recordatorio automático: el día de la hora límite, avisa al tendero que pida.

Corre como tarea de fondo dentro de la app (sin servicios extra). Cada tienda recibe
un solo recordatorio por entrega (Store.last_reminder_for) y no se le avisa si ya
envió el pedido para esa entrega.

En la misma tarea se avisa al tendero que su sesión sigue abierta cuando lleva mucho
tiempo sin usar el bot, una sola vez por cada periodo de inactividad, y se hace el
seguimiento de las solicitudes de acceso (app.onboarding).
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from telegram import Bot

from app.bot import keyboards, texts
from app.db import SessionLocal
from app.inventory.engine import COLOMBIA_TZ, DeliverySchedule, delivery_schedule
from app.models import Distributor, Store, SuggestedOrder
from app.onboarding import send_request_followups

REMINDER_HOUR = 9
CHECK_INTERVAL_SECONDS = 600
IDLE_NOTICE_AFTER = timedelta(hours=24)
# El aviso de sesión abierta solo sale de día (hora Colombia), nunca en la noche.
IDLE_NOTICE_HOURS = range(9, 19)

logger = logging.getLogger(__name__)


def reminder_due(schedule: DeliverySchedule, now: datetime, last_reminder_for: date | None) -> bool:
    """Desde las 9 a. m. del día de la hora límite hasta la hora límite, una vez por entrega."""
    if last_reminder_for == schedule.next_delivery:
        return False
    local_now = now.astimezone(COLOMBIA_TZ)
    deadline = schedule.order_deadline.astimezone(COLOMBIA_TZ)
    starts = datetime.combine(deadline.date(), time(REMINDER_HOUR), COLOMBIA_TZ)
    return starts <= local_now < deadline


async def send_due_reminders(bot: Bot, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    sent = 0
    async with SessionLocal() as session:
        stores = (
            await session.scalars(
                select(Store)
                .join(Distributor, Distributor.id == Store.distributor_id)
                .where(
                    Store.telegram_chat_id.is_not(None),
                    Store.consent_at.is_not(None),
                    Store.delivery_days_set_at.is_not(None),
                    Distributor.status == "active",  # proveedor suspendido: sin recordatorios
                )
            )
        ).all()
        for store in stores:
            schedule = delivery_schedule(store.visit_weekdays, store.lead_time_days, now)
            if not reminder_due(schedule, now, store.last_reminder_for):
                continue
            already_ordered = await session.scalar(
                select(SuggestedOrder.id).where(
                    SuggestedOrder.store_id == store.id,
                    SuggestedOrder.delivery_date == schedule.next_delivery,
                    SuggestedOrder.status.in_(("sent", "received")),
                )
            )
            if already_ordered is None:
                try:
                    await bot.send_message(
                        store.telegram_chat_id,
                        texts.order_reminder(schedule.next_delivery, schedule.order_deadline),
                    )
                    sent += 1
                except Exception:
                    # Solo el id de la tienda: nunca el chat ni datos del tendero.
                    logger.warning("No se pudo mandar el recordatorio a la tienda %s", store.id)
                    continue
            store.last_reminder_for = schedule.next_delivery
        await session.commit()
    return sent


def idle_notice_due(last_activity_at: datetime | None, notice_sent_at: datetime | None, now: datetime) -> bool:
    """Tras IDLE_NOTICE_AFTER sin actividad, de día, y solo si no se avisó desde la última actividad."""
    if last_activity_at is None:
        return False
    if notice_sent_at is not None and notice_sent_at >= last_activity_at:
        return False
    if now - last_activity_at < IDLE_NOTICE_AFTER:
        return False
    return now.astimezone(COLOMBIA_TZ).hour in IDLE_NOTICE_HOURS


async def send_idle_notices(bot: Bot, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    sent = 0
    async with SessionLocal() as session:
        stores = (
            await session.scalars(
                select(Store).where(Store.telegram_chat_id.is_not(None), Store.consent_at.is_not(None))
            )
        ).all()
        for store in stores:
            if not idle_notice_due(store.last_activity_at, store.idle_notice_sent_at, now):
                continue
            try:
                await bot.send_message(
                    store.telegram_chat_id,
                    texts.idle_notice(store.name),
                    reply_markup=keyboards.logout_keyboard(),
                )
                sent += 1
            except Exception:
                logger.warning("No se pudo mandar el aviso de sesión abierta a la tienda %s", store.id)
            # Un solo intento por periodo de inactividad, aunque falle (p. ej. bot bloqueado).
            store.idle_notice_sent_at = now
        await session.commit()
    return sent


async def reminder_loop(bot: Bot) -> None:
    while True:
        try:
            sent = await send_due_reminders(bot)
            if sent:
                logger.info("Recordatorios de pedido enviados: %s", sent)
        except Exception:
            logger.exception("Falló la revisión de recordatorios")
        try:
            sent = await send_request_followups(bot)
            if sent:
                logger.info("Avisos de solicitudes de acceso enviados: %s", sent)
        except Exception:
            logger.exception("Falló la revisión de solicitudes de acceso")
        try:
            sent = await send_idle_notices(bot)
            if sent:
                logger.info("Avisos de sesión abierta enviados: %s", sent)
        except Exception:
            logger.exception("Falló la revisión de sesiones inactivas")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
