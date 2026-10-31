"""Correo de respaldo por SMTP (por ejemplo Gmail con contraseña de aplicación). Gratis, sin servicios extra.

Solo se usa para tenderos que pidieron acceso y no han abierto el bot. Sin SMTP_USER no se manda nada.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

SMTP_TIMEOUT_SECONDS = 20

logger = logging.getLogger(__name__)


def email_enabled() -> bool:
    settings = get_settings()
    return bool(settings.smtp_user and settings.smtp_password)


def _send(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = f"TenderoBot <{settings.smtp_from or settings.smtp_user}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_email(to: str, subject: str, body: str) -> bool:
    """Manda un correo de texto. Devuelve False si no hay SMTP configurado o si falló."""
    if not email_enabled() or not to:
        return False
    try:
        await asyncio.to_thread(_send, to, subject, body)
    except Exception:
        # Sin el destinatario en el log: solo que falló.
        logger.warning("No se pudo mandar un correo")
        return False
    return True
