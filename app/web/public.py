"""Página pública de TenderoBot: qué hace, registro con correo y solicitud de acceso.

Pasos del tendero: 1) escribe su correo y recibe un código, 2) lo escribe en la página
(queda registrado en su sesión del navegador), 3) se le despliega el formulario de solicitud.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Distributor, Store, StoreRequest
from app.onboarding import bot_link
from app.web.auth import LoginRateLimiter, client_ip
from app.web.catalog import FormError
from app.web.routes import templates
from app.web.store_forms import PAYMENT_METHODS, parse_email, parse_store_request
from app.web.verification import CODE_TTL, VerificationError, check_code, send_code

SENT_PATH = "/solicitud/enviada"
CODE_PATH = "/registro/codigo"
FORM_ANCHOR = "/#solicitar"
# Claves de la sesión del navegador (la misma cookie firmada del panel).
APPLICANT_KEY = "applicant_email"
PENDING_KEY = "pending_email"
# Pocas acciones por IP cada 10 minutos: suficiente para corregir y reenviar, no para spam.
request_limiter = LoginRateLimiter(max_attempts=5, window_seconds=600)
code_limiter = LoginRateLimiter(max_attempts=5, window_seconds=600)

router = APIRouter()


def bot_username(request: Request) -> str | None:
    telegram_app = getattr(request.app.state, "telegram_app", None)
    try:
        return telegram_app.bot.username if telegram_app is not None else None
    except RuntimeError:
        return None  # el bot aún no se inicializa


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


async def _landing(
    request: Request,
    session: AsyncSession,
    form: dict | None = None,
    errors: list[str] | None = None,
    register_errors: list[str] | None = None,
    register_email: str = "",
    status_code: int = status.HTTP_200_OK,
):
    applicant = request.session.get(APPLICANT_KEY)
    distributors = (await session.execute(select(Distributor.id, Distributor.name).order_by(Distributor.name))).all()
    existing = None
    if applicant:
        # Lo último que pidió con este correo: en revisión o aprobada se muestra el estado, no el formulario.
        existing = await session.scalar(
            select(StoreRequest).where(StoreRequest.email == applicant).order_by(StoreRequest.id.desc())
        )
    context = {
        "distributors": distributors,
        "payment_methods": PAYMENT_METHODS,
        "form": form or {},
        "errors": errors or [],
        "register_errors": register_errors or [],
        "register_email": register_email,
        "applicant": applicant,
        "existing": existing,
        "bot_link": bot_link(bot_username(request)),
    }
    return templates.TemplateResponse(request, "landing.html", context, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, session: AsyncSession = Depends(get_session)):
    return await _landing(request, session)


# --- Registro con correo ------------------------------------------------------


@router.post("/registro", response_class=HTMLResponse)
async def register(
    request: Request,
    email: str = Form(""),
    website: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Paso 1: manda el código al correo."""
    if website:  # campo oculto que solo llenan los robots
        return _redirect(FORM_ANCHOR)
    try:
        clean = parse_email(email)
    except FormError as exc:
        return await _landing(request, session, register_errors=[str(exc)], register_email=email,
                              status_code=status.HTTP_400_BAD_REQUEST)
    if not code_limiter.allow(client_ip(request)):
        return await _landing(request, session, register_errors=["Pediste muchos códigos seguidos. Espera unos minutos."],
                              register_email=email, status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    try:
        await send_code(session, clean)
    except VerificationError as exc:
        return await _landing(request, session, register_errors=[str(exc)], register_email=email,
                              status_code=status.HTTP_400_BAD_REQUEST)
    request.session[PENDING_KEY] = clean
    return _redirect(CODE_PATH)


def _code_page(request: Request, email: str, error: str | None = None, message: str | None = None,
               status_code: int = status.HTTP_200_OK):
    context = {"email": email, "error": error, "message": message, "minutes": int(CODE_TTL.total_seconds() // 60)}
    return templates.TemplateResponse(request, "verify_code.html", context, status_code=status_code)


@router.get(CODE_PATH, response_class=HTMLResponse)
async def code_form(request: Request):
    email = request.session.get(PENDING_KEY)
    if not email:
        return _redirect(FORM_ANCHOR)
    return _code_page(request, email)


@router.post(CODE_PATH, response_class=HTMLResponse)
async def code_submit(request: Request, code: str = Form(""), session: AsyncSession = Depends(get_session)):
    """Paso 2: con el código correcto, el correo queda registrado en este navegador."""
    email = request.session.get(PENDING_KEY)
    if not email:
        return _redirect(FORM_ANCHOR)
    try:
        await check_code(session, email, code)
    except VerificationError as exc:
        return _code_page(request, email, error=str(exc), status_code=status.HTTP_400_BAD_REQUEST)
    request.session.pop(PENDING_KEY, None)
    request.session[APPLICANT_KEY] = email
    return _redirect(FORM_ANCHOR)


@router.post("/registro/reenviar", response_class=HTMLResponse)
async def code_resend(request: Request, session: AsyncSession = Depends(get_session)):
    email = request.session.get(PENDING_KEY)
    if not email:
        return _redirect(FORM_ANCHOR)
    if not code_limiter.allow(client_ip(request)):
        return _code_page(request, email, error="Pediste muchos códigos seguidos. Espera unos minutos.",
                          status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    try:
        await send_code(session, email)
    except VerificationError as exc:
        return _code_page(request, email, error=str(exc), status_code=status.HTTP_400_BAD_REQUEST)
    return _code_page(request, email, message="Te mandamos un código nuevo.")


@router.post("/registro/salir")
async def register_logout(request: Request):
    """Cambiar de correo: olvida el registro de este navegador."""
    request.session.pop(APPLICANT_KEY, None)
    request.session.pop(PENDING_KEY, None)
    return _redirect(FORM_ANCHOR)


# --- Solicitud ----------------------------------------------------------------


@router.post("/solicitud", response_class=HTMLResponse)
async def submit_request(request: Request, session: AsyncSession = Depends(get_session)):
    """Paso 3: la solicitud. Solo con un correo ya verificado en este navegador."""
    applicant = request.session.get(APPLICANT_KEY)
    if not applicant:
        return _redirect(FORM_ANCHOR)
    raw = await request.form()
    form = {key: str(value) for key, value in raw.items()}
    if form.get("website"):  # campo oculto que solo llenan los robots
        return _redirect(SENT_PATH)
    if not request_limiter.allow(client_ip(request)):
        return await _landing(
            request, session, form, ["Enviaste muchas solicitudes seguidas. Espera unos minutos."],
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    form["email"] = applicant  # el correo es el verificado, no uno escrito en el formulario
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
            select(StoreRequest.id).where(
                StoreRequest.status == "pending",
                (StoreRequest.phone == data["phone"]) | (StoreRequest.email == applicant),
            )
        ):
            errors.append("Ya hay una solicitud en revisión con ese celular o ese correo.")
    if errors:
        return await _landing(request, session, form, errors, status_code=status.HTTP_400_BAD_REQUEST)

    session.add(StoreRequest(distributor_id=distributor_id, consent_at=datetime.now(UTC), **data))
    try:
        await session.commit()
    except IntegrityError:
        # Dos envíos casi al tiempo con el mismo celular: el índice único deja pasar solo uno.
        await session.rollback()
    return _redirect(SENT_PATH)


@router.get(SENT_PATH, response_class=HTMLResponse)
async def request_sent(request: Request):
    return templates.TemplateResponse(request, "request_sent.html", {"bot_link": bot_link(bot_username(request))})
