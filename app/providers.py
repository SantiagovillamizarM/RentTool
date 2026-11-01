"""Proveedores (distribuidores): datos de la empresa, alta, baja y enlaces para crear la contraseña.

Lo usa el panel del administrador de TenderoBot. Nada de esto se hace desde el panel del proveedor.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Distributor, DistributorUser, PasswordToken, Product, Store

# Qué vende un proveedor. Las reguladas se marcan para que el administrador las revise con cuidado.
CATEGORIES = {
    "bebidas": "Bebidas y gaseosas",
    "lacteos": "Lácteos y huevos",
    "abarrotes": "Abarrotes, granos y enlatados",
    "snacks": "Snacks, dulces y galletas",
    "panaderia": "Panadería",
    "aseo": "Aseo y limpieza del hogar",
    "personal": "Cuidado personal",
    "carnes": "Carnes, pollo y embutidos",
    "fruver": "Frutas y verduras",
    "licores": "Licores y cigarrillos",
    "otros": "Otros",
}
REGULATED_CATEGORIES = {"licores"}

ACTIVATE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=24)

_NIT_WEIGHTS = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]


class ProviderError(ValueError):
    pass


def nit_check_digit(base: str) -> int:
    """Dígito de verificación del NIT (método de la DIAN, módulo 11)."""
    total = sum(int(digit) * weight for digit, weight in zip(reversed(base), _NIT_WEIGHTS))
    remainder = total % 11
    return remainder if remainder < 2 else 11 - remainder


def clean_nit(raw: str) -> str:
    """'900.123.456-8' -> '900123456-8'. Valida el dígito de verificación."""
    value = "".join(ch for ch in (raw or "") if ch.isdigit() or ch == "-").strip("-")
    base, sep, digit = value.partition("-")
    if not sep or not base.isdigit() or not digit.isdigit() or len(digit) != 1 or not 6 <= len(base) <= 15:
        raise ProviderError("Escribe el NIT con su dígito de verificación, por ejemplo 900123456-8.")
    if nit_check_digit(base) != int(digit):
        raise ProviderError("El dígito de verificación del NIT no coincide. Revísalo en el RUT.")
    return f"{base}-{digit}"


def categories_label(value: str | None) -> list[str]:
    return [CATEGORIES.get(key, key) for key in (value or "").split(",") if key]


def regulated(value: str | None) -> list[str]:
    return [CATEGORIES[key] for key in (value or "").split(",") if key in REGULATED_CATEGORIES]


def _unusable_hash() -> str:
    """Contraseña al azar que nadie conoce: la cuenta solo se usa después de crear la suya con el enlace."""
    return bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode("utf-8")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def password_link_token(
    session: AsyncSession, user: DistributorUser, purpose: str, now: datetime | None = None
) -> str:
    """Crea un enlace de un solo uso (los anteriores de esa cuenta dejan de servir). Devuelve el token."""
    now = now or datetime.now(UTC)
    await session.execute(delete(PasswordToken).where(PasswordToken.user_id == user.id, PasswordToken.used_at.is_(None)))
    raw = secrets.token_urlsafe(32)
    session.add(
        PasswordToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            purpose=purpose,
            created_at=now,
            expires_at=now + (ACTIVATE_TTL if purpose == "activate" else RESET_TTL),
        )
    )
    return raw


async def find_token(session: AsyncSession, raw: str, now: datetime | None = None) -> PasswordToken | None:
    now = now or datetime.now(UTC)
    token = await session.scalar(select(PasswordToken).where(PasswordToken.token_hash == hash_token(raw or "")))
    if token is None or token.used_at is not None or token.expires_at <= now:
        return None
    return token


async def add_user(session: AsyncSession, distributor_id: int, email: str) -> tuple[DistributorUser, str]:
    """Cuenta nueva del panel para un proveedor. Devuelve la cuenta y el token del enlace de activación."""
    taken = await session.scalar(select(DistributorUser.id).where(DistributorUser.email == email))
    if taken is not None:
        raise ProviderError("Ese correo ya tiene una cuenta en el panel.")
    user = DistributorUser(distributor_id=distributor_id, email=email, password_hash=_unusable_hash())
    session.add(user)
    await session.flush()
    return user, await password_link_token(session, user, "activate")


async def create_provider(session: AsyncSession, data: dict, login_email: str) -> tuple[Distributor, str]:
    """Da de alta un proveedor con su primera cuenta. Devuelve el proveedor y el token de activación."""
    if data.get("nit"):
        taken = await session.scalar(select(Distributor.name).where(Distributor.nit == data["nit"]))
        if taken is not None:
            raise ProviderError(f"Ya hay un proveedor con ese NIT: {taken}.")
    distributor = Distributor(status="active", **data)
    session.add(distributor)
    await session.flush()
    _user, token = await add_user(session, distributor.id, login_email)
    return distributor, token


async def delete_provider(session: AsyncSession, distributor: Distributor) -> None:
    """Borra el proveedor con todo lo suyo. Primero las tiendas (se llevan ventas, conteos y pedidos)
    y luego los productos, que esas filas referencian con RESTRICT."""
    await session.execute(delete(Store).where(Store.distributor_id == distributor.id))
    await session.execute(delete(Product).where(Product.distributor_id == distributor.id))
    await session.execute(delete(DistributorUser).where(DistributorUser.distributor_id == distributor.id))
    await session.delete(distributor)


def block_product(product: Product, reason: str, now: datetime | None = None) -> None:
    product.active = False
    product.blocked_at = now or datetime.now(UTC)
    product.blocked_reason = reason


def unblock_product(product: Product) -> None:
    # Queda inactivo: el proveedor decide si lo vuelve a vender.
    product.blocked_at = None
    product.blocked_reason = None
