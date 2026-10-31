"""Página pública de TenderoBot: qué hace y formulario para que un tendero pida acceso."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Distributor, Store, StoreRequest
from app.onboarding import bot_link
from app.web.auth import LoginRateLimiter, client_ip
from app.web.routes import templates
from app.web.store_forms import PAYMENT_METHODS, parse_store_request

SENT_PATH = "/solicitud/enviada"
# Unas pocas solicitudes por IP cada 10 minutos: suficiente para corregir y reenviar, no para spam.
request_limiter = LoginRateLimiter(max_attempts=5, window_seconds=600)

router = APIRouter()


def bot_username(request: Request) -> str | None:
    telegram_app = getattr(request.app.state, "telegram_app", None)
    try:
        return telegram_app.bot.username if telegram_app is not None else None
    except RuntimeError:
        return None  # el bot aún no se inicializa


async def _landing(
    request: Request,
    session: AsyncSession,
    form: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    distributors = (await session.execute(select(Distributor.id, Distributor.name).order_by(Distributor.name))).all()
    context = {
        "distributors": distributors,
        "payment_methods": PAYMENT_METHODS,
        "form": form or {},
        "errors": errors or [],
        "bot_link": bot_link(bot_username(request)),
    }
    return templates.TemplateResponse(request, "landing.html", context, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, session: AsyncSession = Depends(get_session)):
    return await _landing(request, session)


@router.post("/solicitud", response_class=HTMLResponse)
async def submit_request(request: Request, session: AsyncSession = Depends(get_session)):
    raw = await request.form()
    form = {key: str(value) for key, value in raw.items()}
    # Campo oculto que solo llenan los robots: se les responde como si nada.
    if form.get("website"):
        return RedirectResponse(SENT_PATH, status_code=status.HTTP_303_SEE_OTHER)
    if not request_limiter.allow(client_ip(request)):
        return await _landing(
            request, session, form, ["Enviaste muchas solicitudes seguidas. Espera unos minutos."],
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    data, errors = parse_store_request(form)
    distributor_ids = list((await session.scalars(select(Distributor.id))).all())
    chosen = form.get("distributor_id", "")
    distributor_id = int(chosen) if chosen.isdigit() and int(chosen) in distributor_ids else None
    if distributor_id is None and len(distributor_ids) == 1:
        distributor_id = distributor_ids[0]
    if distributor_id is None:
        errors.append("Escoge tu distribuidor.")
    if not errors:
        if await session.scalar(select(Store.id).where(Store.owner_phone == data["phone"])):
            errors.append("Ese celular ya tiene una tienda en TenderoBot. Abre el bot y toca 📱 Compartir mi número.")
        elif await session.scalar(
            select(StoreRequest.id).where(StoreRequest.phone == data["phone"], StoreRequest.status == "pending")
        ):
            errors.append("Ya hay una solicitud en revisión con ese celular. Abre el bot para saber cuándo te responden.")
    if errors:
        return await _landing(request, session, form, errors, status.HTTP_400_BAD_REQUEST)

    session.add(StoreRequest(distributor_id=distributor_id, consent_at=datetime.now(UTC), **data))
    try:
        await session.commit()
    except IntegrityError:
        # Dos envíos casi al tiempo con el mismo celular: el índice único deja pasar solo uno.
        await session.rollback()
    return RedirectResponse(SENT_PATH, status_code=status.HTTP_303_SEE_OTHER)


@router.get(SENT_PATH, response_class=HTMLResponse)
async def request_sent(request: Request):
    return templates.TemplateResponse(request, "request_sent.html", {"bot_link": bot_link(bot_username(request))})
