"""Correo por SMTP (por ejemplo Gmail con contraseña de aplicación). Gratis, sin servicios extra.

Se usa para el código de registro y para avisar a tenderos que no han abierto el bot.
Sin SMTP_USER no se manda nada.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.emails import Email

SMTP_TIMEOUT_SECONDS = 20
LOGO_PATH = Path(__file__).parent / "web" / "static" / "email-logo.png"
LOGO_CID = "tenderobot-logo"

logger = logging.getLogger(__name__)


def email_enabled() -> bool:
    settings = get_settings()
    return bool(settings.smtp_user and settings.smtp_password)


@lru_cache
def _logo() -> bytes:
    return LOGO_PATH.read_bytes()


def build_message(to: str, email: Email) -> EmailMessage:
    """Texto plano + HTML con el logo adjunto dentro del correo (se ve sin descargar imágenes)."""
    settings = get_settings()
    message = EmailMessage()
    message["From"] = f"TenderoBot <{settings.smtp_from or settings.smtp_user}>"
    message["To"] = to
    message["Subject"] = email.subject
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    message.get_payload()[1].add_related(_logo(), maintype="image", subtype="png", cid=f"<{LOGO_CID}>")
    return message


def _send(message: EmailMessage) -> None:
    settings = get_settings()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_email(to: str, email: Email) -> bool:
    """Manda un correo. Devuelve False si no hay SMTP configurado o si falló."""
    if not email_enabled() or not to:
        return False
    try:
        await asyncio.to_thread(_send, build_message(to, email))
    except Exception:
        # Sin el destinatario en el log: solo que falló.
        logger.warning("No se pudo mandar un correo")
        return False
    return True
