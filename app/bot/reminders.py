"""Recordatorio automático: el día de la hora límite, avisa al tendero que pida.

Corre como tarea de fondo dentro de la app (sin servicios extra). Cada tienda recibe
un solo recordatorio por entrega (Store.last_reminder_for) y no se le avisa si ya
envió el pedido para esa entrega.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time

from sqlalchemy import select
from telegram import Bot

from app.bot import texts
from app.db import SessionLocal
from app.inventory.engine import COLOMBIA_TZ, DeliverySchedule, delivery_schedule
from app.models import Store, SuggestedOrder

REMINDER_HOUR = 9
CHECK_INTERVAL_SECONDS = 600

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
                select(Store).where(
                    Store.telegram_chat_id.is_not(None),
                    Store.consent_at.is_not(None),
                    Store.delivery_days_set_at.is_not(None),
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


async def reminder_loop(bot: Bot) -> None:
    while True:
        try:
            sent = await send_due_reminders(bot)
            if sent:
                logger.info("Recordatorios de pedido enviados: %s", sent)
        except Exception:
            logger.exception("Falló la revisión de recordatorios")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
