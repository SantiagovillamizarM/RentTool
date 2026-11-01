"""Un solo login para todo TenderoBot (/login).

- Proveedores y administradores: correo y contraseña. El sistema reconoce la cuenta y lleva a cada uno
  a su panel (/panel o /admin). Ambos tipos de cuenta se revisan siempre, así la respuesta tarda lo mismo
  y no se puede adivinar de qué tipo es un correo.
- Tenderos: no tienen contraseña (usan el bot de Telegram). En la misma página entran con un código al
  correo para ver cómo va su solicitud.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Distributor
from app.onboarding import bot_link
from app.web.auth import (
    ADMIN_KEY,
    CHANGE_PASSWORD_HINT,
    admin_login_session,
    authenticate,
    authenticate_admin,
    client_ip,
    is_blocked_password,
    login_limiter,
    login_session,
)
from app.web.public import bot_username
from app.web.routes import templates

LOGIN_ERROR = "Correo o contraseña incorrectos."
SUSPENDED_ERROR = "Tu cuenta de proveedor está suspendida en TenderoBot. Escríbenos para revisarla."
BLOCKED_ERROR = f"Esta cuenta usa una contraseña de prueba y está bloqueada. Cámbiala con: {CHANGE_PASSWORD_HINT}"

router = APIRouter()


def _go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _page(request: Request, error: str | None = None, email: str = "", retry_after: int = 0,
          status_code: int = status.HTTP_200_OK):
    context = {"error": error, "email": email, "retry_after": retry_after, "bot_link": bot_link(bot_username(request))}
    return templates.TemplateResponse(request, "login.html", context, status_code=status_code)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if isinstance(request.session.get(ADMIN_KEY), int):
        return _go("/admin")
    if isinstance(request.session.get("distributor_id"), int):
        return _go("/panel")
    # Si recarga mientras está bloqueado, sigue viendo el contador.
    error = SUSPENDED_ERROR if request.query_params.get("suspendido") else None
    return _page(request, error, retry_after=login_limiter.retry_after(client_ip(request)))


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    ip = client_ip(request)
    if not login_limiter.allow(ip):
        return _page(request, email=email, retry_after=login_limiter.retry_after(ip),
                     status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    admin = await authenticate_admin(session, email, password)
    user = await authenticate(session, email, password)
    if admin is not None:
        admin_login_session(request, admin)
        return _go("/admin")
    if user is None:
        return _page(request, LOGIN_ERROR, email, status_code=status.HTTP_401_UNAUTHORIZED)
    if is_blocked_password(password):
        # La cuenta existe pero usa una contraseña de prueba (publicada): no se entra hasta cambiarla.
        return _page(request, BLOCKED_ERROR, email, status_code=status.HTTP_403_FORBIDDEN)
    if await session.scalar(select(Distributor.status).where(Distributor.id == user.distributor_id)) != "active":
        return _page(request, SUSPENDED_ERROR, email, status_code=status.HTTP_403_FORBIDDEN)
    login_session(request, user)
    return _go("/panel")


# Entradas viejas: llevan al login único (307 conserva el formulario enviado).


def _to_login(request: Request) -> RedirectResponse:
    query = request.url.query
    return _go("/login" + (f"?{query}" if query else ""))


@router.get("/panel/login", include_in_schema=False)
@router.get("/admin/login", include_in_schema=False)
@router.get("/entrar", include_in_schema=False)
async def old_login_pages(request: Request) -> RedirectResponse:
    return _to_login(request)


@router.post("/panel/login", include_in_schema=False)
@router.post("/admin/login", include_in_schema=False)
async def old_login_forms() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
