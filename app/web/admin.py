"""Panel del administrador de TenderoBot (/admin): proveedores, sus solicitudes, cuentas y productos.

Solo entra una cuenta de administrador, creada por comando (scripts/admin_user.py). Las cuentas de los
proveedores reciben un enlace por correo para crear su contraseña: el administrador nunca la conoce.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.emails import provider_link_email, provider_rejected_email
from app.mailer import email_enabled, send_email
from app.models import (
    Distributor,
    DistributorRequest,
    DistributorUser,
    Product,
    Store,
    StoreRequest,
    SuggestedOrder,
    SuggestedOrderItem,
)
from app.onboarding import DecisionError, parse_rejection
from app.providers import (
    CATEGORIES,
    REGULATED_CATEGORIES,
    ProviderError,
    add_user,
    block_product,
    categories_label,
    create_provider,
    delete_provider,
    password_link_token,
    regulated,
    unblock_product,
)
from app.web.auth import ADMIN_LOGIN_PATH, admin_logout_session, require_admin
from app.web.catalog import FormError
from app.web.provider_forms import parse_provider_edit
from app.web.routes import templates
from app.web.store_forms import parse_email

router = APIRouter(prefix="/admin")
templates.env.globals["provider_categories"] = CATEGORIES
templates.env.globals["regulated_categories"] = REGULATED_CATEGORIES
templates.env.filters["categories_label"] = categories_label
templates.env.filters["regulated"] = regulated

FLASH_KEY = "admin_flash"
RECENT_DECISIONS = 20
ORDERS_WINDOW_DAYS = 30


def _go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _flash(request: Request, message: str, link: str | None = None) -> None:
    """Mensaje para la siguiente página. Si no hay correo configurado, también el enlace para copiarlo."""
    request.session[FLASH_KEY] = {"message": message, "link": link}


def _page(request: Request, name: str, context: dict, status_code: int = status.HTTP_200_OK):
    context = {"flash": request.session.pop(FLASH_KEY, None), **context}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _base_url(request: Request) -> str:
    base = get_settings().public_base_url.strip().rstrip("/")
    return base if base.startswith("http") else str(request.base_url).rstrip("/")


def _send_link(
    request: Request, background: BackgroundTasks, user: DistributorUser, company: str, owner: str,
    token: str, purpose: str, approved: bool, message: str,
) -> None:
    """Manda el enlace de contraseña por correo. Sin SMTP, se le muestra al administrador para copiarlo."""
    link = f"{_base_url(request)}/panel/activar/{token}"
    if email_enabled():
        background.add_task(send_email, user.email, provider_link_email(owner, company, link, purpose, approved))
        _flash(request, message + f" Le enviamos el enlace a {user.email}.")
    else:
        _flash(request, message + " El correo no está configurado: copia este enlace y envíaselo tú.", link)


async def _provider(session: AsyncSession, provider_id: int) -> Distributor:
    distributor = await session.get(Distributor, provider_id)
    if distributor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return distributor


# --- Salir ---------------------------------------------------------------------


@router.post("/logout")
async def logout(request: Request):
    admin_logout_session(request)
    return _go(ADMIN_LOGIN_PATH)


# --- Resumen ------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def overview(request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(days=ORDERS_WINDOW_DAYS)

    async def count(query) -> int:
        return int(await session.scalar(query) or 0)

    orders_value = await session.scalar(
        select(func.coalesce(func.sum(SuggestedOrderItem.units * Product.unit_price), 0))
        .join(SuggestedOrder, SuggestedOrder.id == SuggestedOrderItem.order_id)
        .join(Product, Product.id == SuggestedOrderItem.product_id)
        .where(SuggestedOrder.status.in_(("sent", "received")), SuggestedOrder.created_at >= since)
    )
    context = {
        "admin_active": "overview",
        "providers_active": await count(select(func.count(Distributor.id)).where(Distributor.status == "active")),
        "providers_suspended": await count(select(func.count(Distributor.id)).where(Distributor.status == "suspended")),
        "provider_requests": await count(
            select(func.count(DistributorRequest.id)).where(DistributorRequest.status == "pending")
        ),
        "stores_total": await count(select(func.count(Store.id))),
        "stores_linked": await count(select(func.count(Store.id)).where(Store.telegram_chat_id.is_not(None))),
        "store_requests": await count(select(func.count(StoreRequest.id)).where(StoreRequest.status == "pending")),
        "orders_count": await count(
            select(func.count(SuggestedOrder.id)).where(
                SuggestedOrder.status.in_(("sent", "received")), SuggestedOrder.created_at >= since
            )
        ),
        "orders_value": Decimal(orders_value or 0),
        "orders_days": ORDERS_WINDOW_DAYS,
        "blocked_products": await count(select(func.count(Product.id)).where(Product.blocked_at.is_not(None))),
    }
    return _page(request, "admin_overview.html", context)


# --- Solicitudes de proveedores -----------------------------------------------


async def _requests_page(request: Request, session: AsyncSession, error_for: int | None = None,
                         error: str | None = None, status_code: int = status.HTTP_200_OK):
    pending = (
        await session.scalars(
            select(DistributorRequest).where(DistributorRequest.status == "pending").order_by(DistributorRequest.created_at)
        )
    ).all()
    decided = (
        await session.scalars(
            select(DistributorRequest)
            .where(DistributorRequest.status != "pending")
            .order_by(DistributorRequest.decided_at.desc())
            .limit(RECENT_DECISIONS)
        )
    ).all()
    context = {"admin_active": "requests", "pending": pending, "decided": decided, "error_for": error_for, "error": error}
    return _page(request, "admin_requests.html", context, status_code)


@router.get("/solicitudes", response_class=HTMLResponse)
async def requests_list(request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _requests_page(request, session)


async def _pending_request(session: AsyncSession, request_id: int) -> DistributorRequest | None:
    found = await session.get(DistributorRequest, request_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return found if found.status == "pending" else None


@router.post("/solicitudes/{request_id}/aprobar")
async def request_approve(
    request_id: int, request: Request, background: BackgroundTasks,
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    found = await _pending_request(session, request_id)
    if found is None:
        return _go("/admin/solicitudes")
    data = {
        "name": found.trade_name, "legal_name": found.legal_name, "nit": found.nit,
        "contact_name": found.contact_name, "contact_phone": found.contact_phone, "contact_email": found.contact_email,
        "city": found.city, "address": found.address, "coverage": found.coverage, "categories": found.categories,
    }
    try:
        distributor, token = await create_provider(session, data, found.contact_email)
    except ProviderError as exc:
        await session.rollback()
        return await _requests_page(request, session, request_id, str(exc), status.HTTP_400_BAD_REQUEST)
    found.status = "approved"
    found.decided_at = datetime.now(UTC)
    found.distributor_id = distributor.id
    user = await session.scalar(select(DistributorUser).where(DistributorUser.distributor_id == distributor.id))
    await session.commit()
    _send_link(request, background, user, distributor.name, found.contact_name, token, "activate", True,
               f"{distributor.name} ya es proveedor.")
    return _go(f"/admin/proveedores/{distributor.id}")


@router.post("/solicitudes/{request_id}/rechazar", response_class=HTMLResponse)
async def request_reject(
    request_id: int, request: Request, background: BackgroundTasks, reason: str = Form(""),
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    found = await _pending_request(session, request_id)
    if found is None:
        return _go("/admin/solicitudes")
    try:
        clean = parse_rejection(reason)
    except DecisionError as exc:
        return await _requests_page(request, session, request_id, str(exc), status.HTTP_400_BAD_REQUEST)
    found.status = "rejected"
    found.decided_at = datetime.now(UTC)
    found.rejection_reason = clean
    await session.commit()
    background.add_task(send_email, found.contact_email, provider_rejected_email(found.contact_name, found.trade_name, clean))
    _flash(request, f"Solicitud de {found.trade_name} rechazada. Se le avisó por correo.")
    return _go("/admin/solicitudes")


# --- Proveedores --------------------------------------------------------------


async def _providers_page(request: Request, session: AsyncSession, create_errors: list[str] | None = None,
                          form: dict | None = None, status_code: int = status.HTTP_200_OK):
    stores = select(Store.distributor_id, func.count(Store.id).label("n")).group_by(Store.distributor_id).subquery()
    products = (
        select(Product.distributor_id, func.count(Product.id).label("n"), func.count(Product.blocked_at).label("blocked"))
        .group_by(Product.distributor_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Distributor, stores.c.n, products.c.n, products.c.blocked)
            .outerjoin(stores, stores.c.distributor_id == Distributor.id)
            .outerjoin(products, products.c.distributor_id == Distributor.id)
            .order_by(Distributor.name)
        )
    ).all()
    providers = [
        {"provider": d, "stores": s or 0, "products": p or 0, "blocked": b or 0} for d, s, p, b in rows
    ]
    context = {"admin_active": "providers", "providers": providers, "create_errors": create_errors or [], "form": form or {}}
    return _page(request, "admin_providers.html", context, status_code)


@router.get("/proveedores", response_class=HTMLResponse)
async def providers_list(request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _providers_page(request, session)


@router.post("/proveedores/nuevo", response_class=HTMLResponse)
async def provider_create(
    request: Request, background: BackgroundTasks,
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    """Alta directa de un proveedor (sin solicitud). Su primera cuenta recibe el enlace de activación."""
    raw = await request.form()
    form = {key: str(value) for key, value in raw.items()}
    data, errors = parse_provider_edit(form, [str(v) for v in raw.getlist("categories")])
    login_email = None
    try:
        login_email = parse_email(form.get("login_email", ""))
    except FormError:
        errors.append("Escribe el correo con el que el proveedor va a entrar al panel.")
    if not errors:
        try:
            distributor, token = await create_provider(session, data, login_email)
        except ProviderError as exc:
            await session.rollback()
            errors.append(str(exc))
    if errors:
        return await _providers_page(request, session, errors, form, status.HTTP_400_BAD_REQUEST)
    user = await session.scalar(select(DistributorUser).where(DistributorUser.distributor_id == distributor.id))
    await session.commit()
    _send_link(request, background, user, distributor.name, distributor.contact_name or distributor.name, token,
               "activate", False, f"Proveedor {distributor.name} creado.")
    return _go(f"/admin/proveedores/{distributor.id}")


async def _provider_page(request: Request, session: AsyncSession, distributor: Distributor,
                         errors: list[str] | None = None, form: dict | None = None,
                         status_code: int = status.HTTP_200_OK):
    users = (
        await session.scalars(
            select(DistributorUser).where(DistributorUser.distributor_id == distributor.id).order_by(DistributorUser.email)
        )
    ).all()
    products = (
        await session.scalars(
            select(Product).where(Product.distributor_id == distributor.id)
            .order_by(Product.blocked_at.is_(None), Product.name)
        )
    ).all()
    stores_total = await session.scalar(select(func.count(Store.id)).where(Store.distributor_id == distributor.id))
    stores_linked = await session.scalar(
        select(func.count(Store.id)).where(Store.distributor_id == distributor.id, Store.telegram_chat_id.is_not(None))
    )
    context = {
        "admin_active": "providers", "provider": distributor, "users": users, "products": products,
        "stores_total": stores_total or 0, "stores_linked": stores_linked or 0,
        "errors": errors or [], "form": form,
    }
    return _page(request, "admin_provider.html", context, status_code)


@router.get("/proveedores/{provider_id}", response_class=HTMLResponse)
async def provider_detail(
    provider_id: int, request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    return await _provider_page(request, session, await _provider(session, provider_id))


@router.post("/proveedores/{provider_id}/datos", response_class=HTMLResponse)
async def provider_update(
    provider_id: int, request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    distributor = await _provider(session, provider_id)
    raw = await request.form()
    form = {key: str(value) for key, value in raw.items()}
    categories = [str(v) for v in raw.getlist("categories")]
    data, errors = parse_provider_edit(form, categories)
    if not errors and data["nit"]:
        taken = await session.scalar(
            select(Distributor.name).where(Distributor.nit == data["nit"], Distributor.id != distributor.id)
        )
        if taken is not None:
            errors.append(f"Ya hay un proveedor con ese NIT: {taken}.")
    if errors:
        form["categories"] = ",".join(categories)
        return await _provider_page(request, session, distributor, errors, form, status.HTTP_400_BAD_REQUEST)
    for field, value in data.items():
        setattr(distributor, field, value)
    await session.commit()
    _flash(request, "Datos guardados.")
    return _go(f"/admin/proveedores/{provider_id}")


@router.post("/proveedores/{provider_id}/suspender", response_class=HTMLResponse)
async def provider_suspend(
    provider_id: int, request: Request, reason: str = Form(""),
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    distributor = await _provider(session, provider_id)
    try:
        clean = parse_rejection(reason)
    except DecisionError:
        return await _provider_page(request, session, distributor, ["Escribe por qué se suspende el proveedor."],
                                    status_code=status.HTTP_400_BAD_REQUEST)
    distributor.status = "suspended"
    distributor.suspended_reason = clean
    await session.commit()
    _flash(request, f"{distributor.name} quedó suspendido: no entra al panel y sus tenderos no pueden usar el bot.")
    return _go(f"/admin/proveedores/{provider_id}")


@router.post("/proveedores/{provider_id}/reactivar")
async def provider_reactivate(
    provider_id: int, request: Request, _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    distributor = await _provider(session, provider_id)
    distributor.status = "active"
    distributor.suspended_reason = None
    await session.commit()
    _flash(request, f"{distributor.name} está activo otra vez.")
    return _go(f"/admin/proveedores/{provider_id}")


@router.post("/proveedores/{provider_id}/eliminar", response_class=HTMLResponse)
async def provider_delete(
    provider_id: int, request: Request, confirm: str = Form(""),
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    """Borra el proveedor y todo lo suyo. Hay que escribir su nombre exacto para confirmar."""
    distributor = await _provider(session, provider_id)
    if confirm.strip() != distributor.name:
        return await _provider_page(request, session, distributor,
                                    ["Para eliminarlo escribe el nombre exacto del proveedor."],
                                    status_code=status.HTTP_400_BAD_REQUEST)
    name = distributor.name
    await delete_provider(session, distributor)
    await session.commit()
    _flash(request, f"{name} fue eliminado con sus tiendas, productos, pedidos y cuentas.")
    return _go("/admin/proveedores")


# --- Cuentas del panel del proveedor ------------------------------------------


@router.post("/proveedores/{provider_id}/usuarios", response_class=HTMLResponse)
async def user_add(
    provider_id: int, request: Request, background: BackgroundTasks, email: str = Form(""),
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    distributor = await _provider(session, provider_id)
    try:
        user, token = await add_user(session, distributor.id, parse_email(email))
    except (FormError, ProviderError) as exc:
        await session.rollback()
        return await _provider_page(request, session, distributor, [str(exc)], status_code=status.HTTP_400_BAD_REQUEST)
    await session.commit()
    _send_link(request, background, user, distributor.name, user.email, token, "activate", False,
               f"Cuenta {user.email} creada.")
    return _go(f"/admin/proveedores/{provider_id}")


async def _provider_user(session: AsyncSession, provider_id: int, user_id: int) -> tuple[Distributor, DistributorUser]:
    distributor = await _provider(session, provider_id)
    user = await session.get(DistributorUser, user_id)
    if user is None or user.distributor_id != distributor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return distributor, user


@router.post("/proveedores/{provider_id}/usuarios/{user_id}/enlace")
async def user_reset_link(
    provider_id: int, user_id: int, request: Request, background: BackgroundTasks,
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    """Enlace nuevo para crear o cambiar la contraseña (por ejemplo, si el proveedor la olvidó)."""
    distributor, user = await _provider_user(session, provider_id, user_id)
    purpose = "reset" if user.activated_at else "activate"
    token = await password_link_token(session, user, purpose)
    await session.commit()
    _send_link(request, background, user, distributor.name, user.email, token, purpose, False,
               f"Enlace nuevo para {user.email}.")
    return _go(f"/admin/proveedores/{provider_id}")


@router.post("/proveedores/{provider_id}/usuarios/{user_id}/eliminar", response_class=HTMLResponse)
async def user_delete(
    provider_id: int, user_id: int, request: Request,
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    distributor, user = await _provider_user(session, provider_id, user_id)
    total = await session.scalar(select(func.count(DistributorUser.id)).where(DistributorUser.distributor_id == distributor.id))
    if (total or 0) <= 1:
        return await _provider_page(request, session, distributor,
                                    ["Es la única cuenta del proveedor. Agrega otra antes de quitar esta."],
                                    status_code=status.HTTP_400_BAD_REQUEST)
    email = user.email
    await session.delete(user)
    await session.commit()
    _flash(request, f"Cuenta {email} eliminada. Ya no puede entrar al panel.")
    return _go(f"/admin/proveedores/{provider_id}")


# --- Productos: lo que no es apropiado se bloquea -----------------------------


async def _provider_product(session: AsyncSession, provider_id: int, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.distributor_id != provider_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return product


@router.post("/proveedores/{provider_id}/productos/{product_id}/bloquear", response_class=HTMLResponse)
async def product_block(
    provider_id: int, product_id: int, request: Request, reason: str = Form(""),
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    product = await _provider_product(session, provider_id, product_id)
    try:
        clean = parse_rejection(reason)
    except DecisionError:
        return await _provider_page(request, session, await _provider(session, provider_id),
                                    [f"Escribe por qué se bloquea {product.name}: el proveedor lo va a ver."],
                                    status_code=status.HTTP_400_BAD_REQUEST)
    block_product(product, clean)
    await session.commit()
    _flash(request, f"{product.name} quedó bloqueado: sale del catálogo y del bot.")
    return _go(f"/admin/proveedores/{provider_id}#productos")


@router.post("/proveedores/{provider_id}/productos/{product_id}/desbloquear")
async def product_unblock(
    provider_id: int, product_id: int, request: Request,
    _admin: int = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    product = await _provider_product(session, provider_id, product_id)
    unblock_product(product)
    await session.commit()
    _flash(request, f"{product.name} ya no está bloqueado. Queda inactivo hasta que el proveedor lo active.")
    return _go(f"/admin/proveedores/{provider_id}#productos")
