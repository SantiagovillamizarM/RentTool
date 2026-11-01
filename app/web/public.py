"""Página pública de TenderoBot: qué hace, registro con correo y solicitudes.

Pasos (tenderos y proveedores): 1) escribe su correo y recibe un código, 2) lo escribe en la página
(queda registrado en su sesión del navegador), 3) se le despliega el formulario de solicitud.
Los tenderos los aprueba su proveedor; los proveedores, el administrador de TenderoBot.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AdminUser, Distributor, DistributorRequest, DistributorUser, Store, StoreRequest
from app.providers import CATEGORIES
from app.onboarding import bot_link
from app.web.auth import LoginRateLimiter, client_ip
from app.web.catalog import FormError
from app.web.routes import templates
from app.web.provider_forms import parse_provider_request
from app.web.store_forms import PAYMENT_METHODS, parse_email, parse_store_request
from app.web.verification import CODE_TTL, VerificationError, check_code, send_code

SENT_PATH = "/solicitud/enviada"
PROVIDER_SENT_PATH = "/proveedor/solicitud/enviada"
CODE_PATH = "/registro/codigo"
FORM_ANCHOR = "/#solicitar"
PROVIDER_ANCHOR = "/#distribuidores"
# Claves de la sesión del navegador (la misma cookie firmada del panel).
APPLICANT_KEY = "applicant_email"
PENDING_KEY = "pending_email"
AFTER_KEY = "after_verify"  # a qué sección volver después del código (tendero o proveedor)
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


ROLE_ERRORS = {
    "admin": "Ese correo es de una cuenta de administrador de TenderoBot. Usa otro correo.",
    "provider": "Ese correo es de un proveedor. Para pedir acceso como tendero, usa otro correo.",
    "tendero": "Ese correo es de un tendero. Para registrar una empresa proveedora, usa otro correo.",
}


async def email_role(session: AsyncSession, email: str) -> str | None:
    """Qué es este correo en TenderoBot. Cada correo tiene un solo papel: administrador, proveedor o tendero.

    Una solicitud rechazada no cuenta: puede volver a intentarlo.
    """
    if await session.scalar(select(AdminUser.id).where(func.lower(AdminUser.email) == email)):
        return "admin"
    if await session.scalar(select(DistributorUser.id).where(func.lower(DistributorUser.email) == email)) or (
        await session.scalar(
            select(DistributorRequest.id).where(
                DistributorRequest.contact_email == email, DistributorRequest.status.in_(("pending", "approved"))
            )
        )
    ):
        return "provider"
    if await session.scalar(select(Store.id).where(func.lower(Store.email) == email)) or (
        await session.scalar(
            select(StoreRequest.id).where(StoreRequest.email == email, StoreRequest.status.in_(("pending", "approved")))
        )
    ):
        return "tendero"
    return None


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


async def _landing(
    request: Request,
    session: AsyncSession,
    form: dict | None = None,
    errors: list[str] | None = None,
    register_errors: list[str] | None = None,
    register_email: str = "",
    register_for: str = "",
    provider_form: dict | None = None,
    provider_errors: list[str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    applicant = request.session.get(APPLICANT_KEY)
    # Los tenderos solo escogen entre proveedores activos.
    distributors = (
        await session.execute(
            select(Distributor.id, Distributor.name).where(Distributor.status == "active").order_by(Distributor.name)
        )
    ).all()
    existing = provider_request = provider_account = role = None
    if applicant:
        role = await email_role(session, applicant)
        # Lo último que pidió con este correo: en revisión o aprobada se muestra el estado, no el formulario.
        existing = await session.scalar(
            select(StoreRequest).where(StoreRequest.email == applicant).order_by(StoreRequest.id.desc())
        )
        provider_request = await session.scalar(
            select(DistributorRequest)
            .where(DistributorRequest.contact_email == applicant)
            .order_by(DistributorRequest.id.desc())
        )
        provider_account = await session.scalar(select(DistributorUser.id).where(DistributorUser.email == applicant))
    context = {
        "distributors": distributors,
        "payment_methods": PAYMENT_METHODS,
        "form": form or {},
        "errors": errors or [],
        "register_errors": register_errors or [],
        "register_email": register_email,
        "register_for": register_for,
        "applicant": applicant,
        "role": role,
        "existing": existing,
        "provider_request": provider_request,
        "provider_account": provider_account,
        "provider_form": provider_form or {},
        "provider_errors": provider_errors or [],
        "provider_categories": CATEGORIES,
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
    para: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Paso 1: manda el código al correo. `para=proveedor` vuelve a la sección de proveedores."""
    target = "proveedor" if para == "proveedor" else ""
    if website:  # campo oculto que solo llenan los robots
        return _redirect(FORM_ANCHOR)

    async def fail(message: str, code: int):
        return await _landing(request, session, register_errors=[message], register_email=email,
                              register_for=target, status_code=code)

    try:
        clean = parse_email(email)
    except FormError as exc:
        return await fail(str(exc), status.HTTP_400_BAD_REQUEST)
    if not code_limiter.allow(client_ip(request)):
        return await fail("Pediste muchos códigos seguidos. Espera unos minutos.", status.HTTP_429_TOO_MANY_REQUESTS)
    try:
        await send_code(session, clean)
    except VerificationError as exc:
        return await fail(str(exc), status.HTTP_400_BAD_REQUEST)
    request.session[PENDING_KEY] = clean
    request.session[AFTER_KEY] = PROVIDER_ANCHOR if target else FORM_ANCHOR
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
    return _redirect(request.session.pop(AFTER_KEY, FORM_ANCHOR))


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
    return _redirect(request.session.pop(AFTER_KEY, FORM_ANCHOR))


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

    role = await email_role(session, applicant)
    if role in ("admin", "provider"):
        return await _landing(request, session, form, [ROLE_ERRORS[role]], status_code=status.HTTP_403_FORBIDDEN)
    form["email"] = applicant  # el correo es el verificado, no uno escrito en el formulario
    data, errors = parse_store_request(form)
    distributor_ids = list((await session.scalars(select(Distributor.id).where(Distributor.status == "active"))).all())
    chosen = form.get("distributor_id", "")
    distributor_id = int(chosen) if chosen.isdigit() and int(chosen) in distributor_ids else None
    if distributor_id is None and len(distributor_ids) == 1:
        distributor_id = distributor_ids[0]
    if distributor_id is None:
        errors.append("Escoge tu proveedor." if distributor_ids else "Todavía no hay proveedores en TenderoBot.")
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


@router.post("/proveedor/solicitud", response_class=HTMLResponse)
async def submit_provider_request(request: Request, session: AsyncSession = Depends(get_session)):
    """Solicitud para ser proveedor. Solo con un correo ya verificado; la revisa el administrador."""
    applicant = request.session.get(APPLICANT_KEY)
    if not applicant:
        return _redirect(PROVIDER_ANCHOR)
    raw = await request.form()
    form = {key: str(value) for key, value in raw.items()}
    categories = [str(value) for value in raw.getlist("categories")]
    if form.get("website_url"):  # campo oculto que solo llenan los robots
        return _redirect(PROVIDER_SENT_PATH)

    async def fail(errors: list[str], code: int = status.HTTP_400_BAD_REQUEST):
        return await _landing(request, session, provider_form={**form, "categories": categories},
                              provider_errors=errors, status_code=code)

    if not request_limiter.allow(client_ip(request)):
        return await fail(["Enviaste muchas solicitudes seguidas. Espera unos minutos."], status.HTTP_429_TOO_MANY_REQUESTS)
    role = await email_role(session, applicant)
    if role in ("admin", "tendero"):
        return await fail([ROLE_ERRORS[role]], status.HTTP_403_FORBIDDEN)
    data, errors = parse_provider_request(form, categories)
    if not errors:
        if await session.scalar(select(DistributorUser.id).where(DistributorUser.email == applicant)):
            errors.append("Ese correo ya tiene una cuenta de proveedor. Entra a tu panel.")
        elif await session.scalar(select(Distributor.id).where(Distributor.nit == data["nit"])):
            errors.append("Ya hay un proveedor con ese NIT en TenderoBot.")
        elif await session.scalar(
            select(DistributorRequest.id).where(
                DistributorRequest.status == "pending",
                (DistributorRequest.nit == data["nit"]) | (DistributorRequest.contact_email == applicant),
            )
        ):
            errors.append("Ya hay una solicitud en revisión con ese NIT o ese correo.")
    if errors:
        return await fail(errors)
    session.add(DistributorRequest(contact_email=applicant, consent_at=datetime.now(UTC), **data))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # dos envíos casi al tiempo: el índice único deja pasar solo uno
    return _redirect(PROVIDER_SENT_PATH)


@router.get(PROVIDER_SENT_PATH, response_class=HTMLResponse)
async def provider_request_sent(request: Request):
    return templates.TemplateResponse(request, "provider_request_sent.html", {})


@router.get(SENT_PATH, response_class=HTMLResponse)
async def request_sent(request: Request):
    return templates.TemplateResponse(request, "request_sent.html", {"bot_link": bot_link(bot_username(request))})
