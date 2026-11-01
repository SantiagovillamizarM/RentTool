"""Crea la cuenta de administrador de TenderoBot o le cambia la contraseña.

Uso:
    python -m scripts.admin_user tu@correo.com

Si el correo ya es administrador, le cambia la contraseña. La contraseña se pide oculta: no se
escribe en el comando ni queda en el historial de la terminal. Desde la web no se pueden crear administradores.
"""

import argparse
import asyncio

import bcrypt
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import AdminUser
from app.web.catalog import FormError
from app.web.store_forms import parse_email
from scripts.panel_user import ask_password


async def main() -> None:
    parser = argparse.ArgumentParser(description="Crea o actualiza la cuenta de administrador de TenderoBot.")
    parser.add_argument("email", help="correo del administrador")
    args = parser.parse_args()
    try:
        email = parse_email(args.email)
    except FormError as exc:
        raise SystemExit(str(exc)) from exc
    password = ask_password()
    async with SessionLocal() as session:
        admin = await session.scalar(select(AdminUser).where(func.lower(AdminUser.email) == email))
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        if admin is None:
            session.add(AdminUser(email=email, password_hash=password_hash))
            action = "creada"
        else:
            admin.password_hash = password_hash
            action = "actualizada"
        await session.commit()
    await engine.dispose()
    print(f"Listo: cuenta de administrador {email} {action}. Entra en /login.")


if __name__ == "__main__":
    asyncio.run(main())
