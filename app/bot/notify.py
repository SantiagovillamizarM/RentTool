"""Avisos del distribuidor a sus tenderos por Telegram (precio nuevo, producto nuevo)."""

import logging

from sqlalchemy import select
from telegram import Bot

from app.db import SessionLocal
from app.models import Store

logger = logging.getLogger(__name__)


async def notify_distributor_stores(bot: Bot, distributor_id: int, text: str) -> int:
    """Manda `text` a las tiendas vinculadas que aceptaron el tratamiento de datos."""
    async with SessionLocal() as session:
        chat_ids = (
            await session.scalars(
                select(Store.telegram_chat_id).where(
                    Store.distributor_id == distributor_id,
                    Store.telegram_chat_id.is_not(None),
                    Store.consent_at.is_not(None),
                )
            )
        ).all()
    sent = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except Exception:
            # Sin el chat ni el texto en el log: solo que falló.
            logger.warning("No se pudo mandar un aviso del distribuidor %s", distributor_id)
    return sent
