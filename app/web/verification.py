"""Registro con correo antes de la solicitud: se manda un código de 6 dígitos y el tendero lo escribe.

Así un robot no puede llenar solicitudes en masa: cada una necesita un buzón de verdad.
Límites: el código vence en 10 minutos y admite 5 intentos; por correo, un código por minuto
y 5 por hora; por IP, 5 pedidos cada 10 minutos (en app.web.public).
"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.emails import Email
from app.emails import code_email as build_code_email
from app.mailer import email_enabled, send_email
from app.models import EmailVerification

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_CODES_PER_HOUR = 5

logger = logging.getLogger(__name__)


class VerificationError(ValueError):
    pass


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(email: str, code: str) -> str:
    """HMAC con la clave del servidor: el código no se puede sacar de la base aunque se filtre."""
    key = get_settings().session_secret.encode("utf-8")
    return hmac.new(key, f"{email}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def clean_code(raw: str) -> str:
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def code_email(code: str) -> Email:
    return build_code_email(code, int(CODE_TTL.total_seconds() // 60))


async def send_code(session: AsyncSession, email: str, now: datetime | None = None) -> None:
    """Crea un código nuevo para el correo y lo envía. Los anteriores dejan de servir."""
    now = now or datetime.now(UTC)
    last = await session.scalar(
        select(func.max(EmailVerification.created_at)).where(EmailVerification.email == email)
    )
    if last is not None and now - last < RESEND_AFTER:
        raise VerificationError("Ya te mandamos un código. Espera un minuto para pedir otro.")
    recent = await session.scalar(
        select(func.count(EmailVerification.id)).where(
            EmailVerification.email == email, EmailVerification.created_at > now - timedelta(hours=1)
        )
    )
    if (recent or 0) >= MAX_CODES_PER_HOUR:
        raise VerificationError("Pediste muchos códigos para este correo. Intenta de nuevo en una hora.")

    code = new_code()
    session.add(
        EmailVerification(email=email, code_hash=hash_code(email, code), created_at=now, expires_at=now + CODE_TTL)
    )
    await session.commit()
    if not email_enabled():
        # Sin SMTP (desarrollo local): el código sale en la consola del servidor para poder probar.
        logger.warning("SMTP sin configurar. Código de verificación para %s: %s", email, code)
        return
    if not await send_email(email, code_email(code)):
        raise VerificationError("No pudimos enviarte el correo. Revisa que esté bien escrito e intenta de nuevo.")


async def check_code(session: AsyncSession, email: str, raw_code: str, now: datetime | None = None) -> None:
    """Valida el último código del correo. Si está bien, queda usado; si no, suma un intento."""
    now = now or datetime.now(UTC)
    row = await session.scalar(
        select(EmailVerification)
        .where(EmailVerification.email == email, EmailVerification.used_at.is_(None))
        .order_by(EmailVerification.id.desc())
        .with_for_update()
    )
    if row is None or row.expires_at <= now:
        raise VerificationError("El código venció. Pide uno nuevo.")
    if row.attempts >= MAX_ATTEMPTS:
        raise VerificationError("Escribiste mal el código muchas veces. Pide uno nuevo.")
    code = clean_code(raw_code)
    if len(code) != 6 or not hmac.compare_digest(row.code_hash, hash_code(email, code)):
        row.attempts += 1
        await session.commit()
        left = MAX_ATTEMPTS - row.attempts
        if left <= 0:
            raise VerificationError("Escribiste mal el código muchas veces. Pide uno nuevo.")
        raise VerificationError(f"Ese código no es. Te quedan {left} intento{'s' if left != 1 else ''}.")
    row.used_at = now
    await session.commit()
