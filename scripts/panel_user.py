"""Cambia el correo y la contraseña de la cuenta del panel, sin borrar ningún dato.

Uso:
    python -m scripts.panel_user tu@correo.com
    python -m scripts.panel_user tu@correo.com --actual demo@demo.co   (si hay varias cuentas)

La contraseña se pide oculta: no se escribe en el comando ni queda en el historial de la terminal.
"""

import argparse
import asyncio
import getpass

import bcrypt
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import DistributorUser
from app.web.auth import MIN_PASSWORD_LENGTH, password_problem
from app.web.catalog import FormError
from app.web.store_forms import parse_email


def ask_password() -> str:
    while True:
        password = getpass.getpass(f"Contraseña nueva (mínimo {MIN_PASSWORD_LENGTH} caracteres, no se ve al escribir): ")
        problem = password_problem(password)
        if problem:
            print(problem)
            continue
        if getpass.getpass("Repítela: ") != password:
            print("No coinciden. Intenta otra vez.")
            continue
        return password


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cambia el correo y la contraseña del panel del distribuidor.")
    parser.add_argument("email", help="correo nuevo para entrar al panel")
    parser.add_argument("--actual", help="correo actual de la cuenta a cambiar (si hay más de una)")
    args = parser.parse_args()
    try:
        email = parse_email(args.email)
    except FormError as exc:
        raise SystemExit(str(exc)) from exc

    async with SessionLocal() as session:
        query = select(DistributorUser)
        if args.actual:
            query = query.where(func.lower(DistributorUser.email) == args.actual.strip().lower())
        users = (await session.scalars(query)).all()
        if len(users) != 1:
            emails = ", ".join(u.email for u in (await session.scalars(select(DistributorUser))).all()) or "ninguna"
            raise SystemExit(f"Di cuál cuenta cambiar con --actual. Cuentas del panel: {emails}")
        user = users[0]
        taken = await session.scalar(
            select(DistributorUser.id).where(func.lower(DistributorUser.email) == email, DistributorUser.id != user.id)
        )
        if taken is not None:
            raise SystemExit("Ese correo ya es de otra cuenta del panel.")
        password = ask_password()
        previous = user.email
        user.email = email
        user.password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        await session.commit()
    await engine.dispose()
    print(f"Listo: la cuenta {previous} ahora entra con {email} y la contraseña nueva.")
    print("Si alguien más tenía el panel abierto, cambia SESSION_SECRET en el .env para cerrarle la sesión.")


if __name__ == "__main__":
    asyncio.run(main())
