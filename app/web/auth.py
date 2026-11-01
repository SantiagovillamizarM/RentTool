import math
from collections import defaultdict, deque
from time import monotonic

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AdminUser, Distributor, DistributorUser

LOGIN_PATH = "/login"
ADMIN_LOGIN_PATH = "/login"
# Claves de la sesión (misma cookie firmada): proveedor y administrador no se pisan entre sí.
DISTRIBUTOR_KEYS = ("user_id", "distributor_id")
ADMIN_KEY = "admin_id"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60.0
MIN_PASSWORD_LENGTH = 12
# Contraseñas de prueba o publicadas: con ellas no se entra aunque la cuenta exista.
WEAK_PASSWORDS = {
    "demo1234", "demo", "demodemo", "password", "contraseña", "12345678", "123456789", "1234567890",
    "admin", "admin123", "tenderobot", "tenderobot123", "qwerty123",
}
CHANGE_PASSWORD_HINT = "python -m scripts.panel_user"


def password_problem(password: str) -> str | None:
    """Por qué una contraseña no sirve para el panel, o None si está bien."""
    if password.strip().lower() in WEAK_PASSWORDS:
        return "Esa es una contraseña de prueba o muy conocida."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
    return None


def is_blocked_password(password: str) -> bool:
    """Contraseñas con las que el login se niega a entrar (las de prueba)."""
    return password.strip().lower() in WEAK_PASSWORDS

# Hash fijo para comparar cuando el correo no existe: así la respuesta tarda lo mismo
# exista o no el usuario, y no se puede adivinar qué correos están registrados.
_DUMMY_HASH = bcrypt.hashpw(b"tenderobot-dummy-password", bcrypt.gensalt()).decode("utf-8")


class LoginRateLimiter:
    """Máximo `max_attempts` intentos por clave (IP) en una ventana deslizante. En memoria."""

    def __init__(self, max_attempts: int = LOGIN_MAX_ATTEMPTS, window_seconds: float = LOGIN_WINDOW_SECONDS) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        """Registra un intento y dice si está permitido."""
        now = monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_attempts:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Segundos que faltan para poder intentar de nuevo (0 = puede intentar). No registra intento."""
        now = monotonic() if now is None else now
        hits = self._hits.get(key)
        if not hits:
            return 0
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) < self.max_attempts:
            return 0
        return max(1, math.ceil(self.window_seconds - (now - hits[0])))


login_limiter = LoginRateLimiter()


def client_ip(request: Request) -> str:
    # VERIFICAR: detrás del proxy de Render la IP real llega en X-Forwarded-For; uvicorn la
    # aplica a request.client solo si se arranca con --proxy-headers (ver README de la fase 6).
    return request.client.host if request.client else "unknown"


def verify_password(password: str, password_hash: str) -> bool:
    # Mismo mecanismo que scripts/seed.py (bcrypt.hashpw con utf-8).
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


async def _authenticate(session: AsyncSession, model, email: str, password: str):
    normalized = email.strip().lower()
    user = await session.scalar(select(model).where(func.lower(model.email) == normalized))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> DistributorUser | None:
    return await _authenticate(session, DistributorUser, email, password)


async def authenticate_admin(session: AsyncSession, email: str, password: str) -> AdminUser | None:
    return await _authenticate(session, AdminUser, email, password)


def login_session(request: Request, user: DistributorUser) -> None:
    logout_session(request)
    request.session["user_id"] = user.id
    request.session["distributor_id"] = user.distributor_id


def logout_session(request: Request) -> None:
    for key in DISTRIBUTOR_KEYS:
        request.session.pop(key, None)


def admin_login_session(request: Request, admin: AdminUser) -> None:
    request.session[ADMIN_KEY] = admin.id


def admin_logout_session(request: Request) -> None:
    request.session.pop(ADMIN_KEY, None)


def _to(path: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": path})


async def require_distributor(request: Request, session: AsyncSession = Depends(get_session)) -> int:
    """Dependencia: el distributor_id de la sesión. Sin sesión, cuenta borrada o proveedor suspendido: al login."""
    distributor_id = request.session.get("distributor_id")
    user_id = request.session.get("user_id")
    if not isinstance(distributor_id, int) or not isinstance(user_id, int):
        raise _to(LOGIN_PATH)
    status_value = await session.scalar(
        select(Distributor.status)
        .join(DistributorUser, DistributorUser.distributor_id == Distributor.id)
        .where(DistributorUser.id == user_id, Distributor.id == distributor_id)
    )
    if status_value is None:
        logout_session(request)
        raise _to(LOGIN_PATH)
    if status_value != "active":
        logout_session(request)
        raise _to(LOGIN_PATH + "?suspendido=1")
    return distributor_id


async def require_admin(request: Request, session: AsyncSession = Depends(get_session)) -> int:
    """Dependencia: el id del administrador de la sesión, o al login del administrador."""
    admin_id = request.session.get(ADMIN_KEY)
    if not isinstance(admin_id, int) or await session.get(AdminUser, admin_id) is None:
        admin_logout_session(request)
        raise _to(ADMIN_LOGIN_PATH)
    return admin_id
