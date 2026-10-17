from collections import defaultdict, deque
from time import monotonic

import bcrypt
from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DistributorUser

LOGIN_PATH = "/panel/login"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60.0

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


async def authenticate(session: AsyncSession, email: str, password: str) -> DistributorUser | None:
    normalized = email.strip().lower()
    user = await session.scalar(select(DistributorUser).where(func.lower(DistributorUser.email) == normalized))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def login_session(request: Request, user: DistributorUser) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["distributor_id"] = user.distributor_id


def logout_session(request: Request) -> None:
    request.session.clear()


def require_distributor(request: Request) -> int:
    """Dependencia: devuelve el distributor_id de la sesión o redirige al login."""
    distributor_id = request.session.get("distributor_id")
    if not isinstance(distributor_id, int) or not isinstance(request.session.get("user_id"), int):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": LOGIN_PATH})
    return distributor_id
