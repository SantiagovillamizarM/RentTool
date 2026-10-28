from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.bot import texts as bot_texts
from app.bot.notify import notify_distributor_stores
from app.bot.texts import day_label, deadline_label, join_days
from app.inventory.engine import (
    COLOMBIA_TZ,
    StoreAnalysis,
    load_store_analysis,
    local_date,
    parse_visit_weekdays,
)
from app.models import Product, Receipt, Sale, StockCount, Store, SuggestedOrder, SuggestedOrderItem
from app.inventory.orders import cap_to_available, order_total, to_order_lines
from app.phones import format_phone, normalize_phone
from app.web import aggregations, catalog, formatting
from app.web.auth import (
    authenticate,
    client_ip,
    login_limiter,
    login_session,
    logout_session,
    require_distributor,
)

CHART_DAYS = 28
LOGIN_ERROR = "Correo o contraseña incorrectos."

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["cop"] = formatting.cop
templates.env.filters["coverage"] = formatting.coverage
templates.env.filters["qty"] = formatting.qty
templates.env.filters["local_dt"] = lambda value: value.astimezone(COLOMBIA_TZ).strftime("%d/%m/%Y %H:%M")
templates.env.globals["product_status"] = aggregations.product_status
# Versión de los archivos estáticos: cambia cuando cambian, así el navegador no usa una copia vieja.
STATIC_DIR = Path(__file__).parent / "static"
templates.env.globals["static_version"] = int(max(f.stat().st_mtime for f in STATIC_DIR.iterdir()))
templates.env.filters["day"] = day_label
templates.env.filters["deadline"] = deadline_label
templates.env.filters["phone"] = format_phone

router = APIRouter(prefix="/panel")


async def _distributor_stores(session: AsyncSession, distributor_id: int) -> list[Store]:
    result = await session.scalars(select(Store).where(Store.distributor_id == distributor_id).order_by(Store.name))
    return list(result.all())


async def _analyses(session: AsyncSession, stores: list[Store]) -> list[StoreAnalysis]:
    # MVP: pocas tiendas, se calculan en secuencia sobre la misma sesión.
    results = []
    for store in stores:
        analysis = await load_store_analysis(session, store.id)
        if analysis is not None:
            results.append(analysis)
    return results


async def _sent_orders(session: AsyncSession, distributor_id: int, store_id: int | None = None) -> list[dict]:
    """Pedidos con status "sent": fecha y valor (unidades × precio actual del producto)."""
    query = (
        select(
            SuggestedOrder.id,
            SuggestedOrder.store_id,
            SuggestedOrder.created_at,
            SuggestedOrder.delivery_date,
            func.coalesce(func.sum(SuggestedOrderItem.units * Product.unit_price), 0),
        )
        .join(Store, Store.id == SuggestedOrder.store_id)
        .outerjoin(SuggestedOrderItem, SuggestedOrderItem.order_id == SuggestedOrder.id)
        .outerjoin(Product, Product.id == SuggestedOrderItem.product_id)
        .where(Store.distributor_id == distributor_id, SuggestedOrder.status == "sent")
        .group_by(SuggestedOrder.id)
        .order_by(SuggestedOrder.created_at.desc())
    )
    if store_id is not None:
        query = query.where(SuggestedOrder.store_id == store_id)
    rows = (await session.execute(query)).all()
    return [
        {
            "id": row[0],
            "store_id": row[1],
            "created_at": row[2],
            "delivery_date": row[3],
            "value": Decimal(row[4]),
        }
        for row in rows
    ]


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if isinstance(request.session.get("distributor_id"), int):
        return RedirectResponse("/panel", status_code=status.HTTP_303_SEE_OTHER)
    # Si recarga mientras está bloqueado, sigue viendo el contador.
    retry_after = login_limiter.retry_after(client_ip(request))
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "email": "", "retry_after": retry_after}
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    ip = client_ip(request)
    if not login_limiter.allow(ip):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": None, "email": email, "retry_after": login_limiter.retry_after(ip)},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    user = await authenticate(session, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": LOGIN_ERROR, "email": email, "retry_after": 0},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    login_session(request, user)
    return RedirectResponse("/panel", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/panel/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("", response_class=HTMLResponse)
async def overview(
    request: Request,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    stores = await _distributor_stores(session, distributor_id)
    analyses = await _analyses(session, stores)
    sent = await _sent_orders(session, distributor_id)
    context = {
        "active": "overview",
        "stores_total": len(stores),
        "stores_linked": sum(1 for store in stores if store.telegram_chat_id is not None),
        "stores_at_risk": aggregations.count_stores_at_risk(analyses),
        "sent_value": sum((order["value"] for order in sent), Decimal("0")),
        "sent_count": len(sent),
        "top_risk": aggregations.top_risk_products(analyses),
    }
    return templates.TemplateResponse(request, "overview.html", context)


@router.get("/tiendas", response_class=HTMLResponse)
async def stores_list(
    request: Request,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    stores = await _distributor_stores(session, distributor_id)
    analyses = {analysis.store_id: analysis for analysis in await _analyses(session, stores)}
    rows = []
    for store in stores:
        analysis = analyses.get(store.id)
        rows.append(
            {
                "store": store,
                "linked": store.telegram_chat_id is not None,
                "delivery_days": join_days(parse_visit_weekdays(store.visit_weekdays)) or "—",
                "at_risk": len(analysis.at_risk) if analysis else 0,
                "stagnant": len(analysis.stagnant) if analysis else 0,
                "order_total": analysis.order_total if analysis else Decimal("0"),
            }
        )
    return templates.TemplateResponse(request, "stores.html", {"active": "stores", "rows": rows})


@router.get("/tiendas/{store_id}", response_class=HTMLResponse)
async def store_detail(
    store_id: int,
    request: Request,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    store = await session.get(Store, store_id)
    # Tienda inexistente o de otro distribuidor: 404 en ambos casos (no revelar que existe).
    if store is None or store.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    analysis = await load_store_analysis(session, store.id)
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    today = local_date(datetime.now(timezone.utc))
    window_start = datetime.combine(today - timedelta(days=CHART_DAYS - 1), datetime.min.time(), COLOMBIA_TZ)
    sales = (
        await session.execute(
            select(Sale.sold_at, Sale.quantity).where(Sale.store_id == store.id, Sale.sold_at >= window_start)
        )
    ).all()
    units_by_day: dict = {}
    for sold_at, quantity in sales:
        day = local_date(sold_at)
        units_by_day[day] = units_by_day.get(day, 0) + quantity
    series = formatting.daily_series(units_by_day, today, CHART_DAYS)
    chart = {
        "labels": [day.strftime("%d/%m") for day, _ in series],
        "values": [units for _, units in series],
    }

    # Mismo ajuste que en el bot: el pedido sugerido no pasa de lo que el distribuidor tiene.
    products_by_id = {
        p.id: p
        for p in (
            await session.scalars(
                select(Product).where(Product.distributor_id == distributor_id, Product.active.is_(True))
            )
        ).all()
    }
    capped = cap_to_available(
        [{"product_id": l.product_id, "cases": l.cases, "reason": l.reason} for l in analysis.order_lines],
        products_by_id,
    )
    order_lines = to_order_lines(capped.lines, products_by_id)

    context = {
        "active": "stores",
        "store": store,
        "order_lines": order_lines,
        "order_total": order_total(order_lines),
        "availability_notes": capped.not_found,
        "delivery_days": join_days(parse_visit_weekdays(store.visit_weekdays)) or "—",
        "analysis": analysis,
        "products": sorted(analysis.products, key=lambda p: (p.abc_class, p.name)),
        "chart": chart,
        "sent_orders": await _sent_orders(session, distributor_id, store.id),
        "phone_message": PHONE_MESSAGES.get(request.query_params.get("celular", "")),
        "phone_error": PHONE_ERRORS.get(request.query_params.get("celular", "")),
    }
    return templates.TemplateResponse(request, "store_detail.html", context)


PHONE_MESSAGES = {
    "ok": "Celular guardado. El tendero ya puede entrar al bot compartiendo su número.",
    "borrado": "Celular borrado. Nadie puede entrar al bot con esta tienda hasta que registres uno.",
}
PHONE_ERRORS = {
    "invalido": "Ese número no es válido. Escríbelo con indicativo, por ejemplo +57 300 123 4567.",
    "repetido": "Ese número ya está registrado en otra tienda.",
}


@router.post("/tiendas/{store_id}/celular")
async def store_phone(
    store_id: int,
    phone: str = Form(""),
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    """Celular del dueño: con él entra al bot (botón 📱 Compartir mi número)."""
    store = await session.get(Store, store_id)
    if store is None or store.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    url = f"/panel/tiendas/{store.id}?celular="
    clean = normalize_phone(phone) if phone.strip() else None
    if phone.strip() and clean is None:
        return RedirectResponse(url + "invalido", status_code=status.HTTP_303_SEE_OTHER)
    if clean is not None:
        taken = await session.scalar(select(Store.id).where(Store.owner_phone == clean, Store.id != store.id))
        if taken is not None:
            return RedirectResponse(url + "repetido", status_code=status.HTTP_303_SEE_OTHER)
    if clean != store.owner_phone:
        # Cambió el dueño del acceso: se cierra la sesión que estuviera abierta con el número anterior.
        store.owner_phone = clean
        store.telegram_chat_id = None
        store.consent_at = None
        store.last_activity_at = None
        store.idle_notice_sent_at = None
        await session.commit()
    return RedirectResponse(url + ("ok" if clean else "borrado"), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/demanda", response_class=HTMLResponse)
async def demand(
    request: Request,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    stores = await _distributor_stores(session, distributor_id)
    rows = aggregations.aggregate_demand(await _analyses(session, stores))
    return templates.TemplateResponse(
        request, "demand.html", {"active": "demand", "rows": rows, "stores_total": len(stores)}
    )


# --- Productos del distribuidor ----------------------------------------------

PRODUCT_MESSAGES = {
    "creado": "Producto agregado. Se les avisó a tus tiendas.",
}


def _batch_message(request: Request) -> str | None:
    """Mensaje después de guardar el catálogo: cuántos productos cambiaron y si se avisó."""
    params = request.query_params
    if params.get("ok") != "lote":
        return PRODUCT_MESSAGES.get(params.get("ok", ""))
    changed = int(params.get("n", "0")) if params.get("n", "0").isdigit() else 0
    if changed == 0:
        return "No había cambios para guardar."
    text = "1 producto actualizado." if changed == 1 else f"{changed} productos actualizados."
    if params.get("avisos", "0") not in ("", "0"):
        text += " Se les avisó a tus tiendas por Telegram."
    return text


def _notify(request: Request, background: BackgroundTasks, distributor_id: int, text: str) -> None:
    """Aviso a las tiendas en segundo plano, para no demorar la respuesta del panel."""
    telegram_app = getattr(request.app.state, "telegram_app", None)
    if telegram_app is not None:
        background.add_task(notify_distributor_stores, telegram_app.bot, distributor_id, text)


async def _products_page(
    request: Request, session: AsyncSession, distributor_id: int, error: str | None = None,
    form: dict | None = None, status_code: int = status.HTTP_200_OK, create_error: str | None = None,
    submitted: dict | None = None, error_ids: set[int] | None = None,
):
    products = (
        await session.scalars(
            select(Product)
            .where(Product.distributor_id == distributor_id)
            .order_by(Product.active.desc(), Product.name)
        )
    ).all()
    context = {
        "active": "products",
        "products": products,
        "error": error,
        # Error al crear: la ventana de "Agregar producto" se abre sola con el mensaje y lo escrito.
        "create_error": create_error,
        "message": _batch_message(request),
        # Si el lote tuvo errores, se muestran los valores que escribió (no se pierden).
        "submitted": submitted or {},
        "error_ids": error_ids or set(),
        "form": form or {},
    }
    return templates.TemplateResponse(request, "products.html", context, status_code=status_code)


@router.get("/productos", response_class=HTMLResponse)
async def products_list(
    request: Request,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    return await _products_page(request, session, distributor_id)


@router.post("/productos/nuevo", response_class=HTMLResponse)
async def product_create(
    request: Request,
    background: BackgroundTasks,
    sku: str = Form(""),
    name: str = Form(""),
    presentation: str = Form(""),
    aliases: str = Form(""),
    units_per_case: str = Form(""),
    unit_price: str = Form(""),
    available_units: str = Form(""),
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    form = {
        "sku": sku, "name": name, "presentation": presentation, "aliases": aliases,
        "units_per_case": units_per_case, "unit_price": unit_price, "available_units": available_units,
    }
    try:
        clean_sku = catalog.parse_required(sku, "el código (SKU)", 32).upper()
        product = Product(
            distributor_id=distributor_id,
            sku=clean_sku,
            name=catalog.parse_required(name, "el nombre", 160),
            presentation=catalog.parse_required(presentation, "la presentación", 80),
            aliases=aliases.strip(),
            units_per_case=catalog.parse_units_per_case(units_per_case),
            unit_price=catalog.parse_price(unit_price),
            available_units=catalog.parse_available(available_units),
            active=True,
        )
    except catalog.FormError as exc:
        return await _products_page(
            request, session, distributor_id, form=form, status_code=status.HTTP_400_BAD_REQUEST, create_error=str(exc)
        )
    duplicate = await session.scalar(
        select(Product.id).where(Product.distributor_id == distributor_id, func.upper(Product.sku) == clean_sku)
    )
    if duplicate is not None:
        return await _products_page(
            request, session, distributor_id, form=form, status_code=status.HTTP_400_BAD_REQUEST,
            create_error=f"Ya tienes un producto con el código {clean_sku}.",
        )
    session.add(product)
    await session.commit()
    _notify(request, background, distributor_id, bot_texts.new_product(product.name, product.unit_price, product.units_per_case))
    return RedirectResponse("/panel/productos?ok=creado", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/productos/guardar", response_class=HTMLResponse)
async def products_save(
    request: Request,
    background: BackgroundTasks,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
):
    """Guarda todo el catálogo de una vez (precios, existencias y activos). Todo o nada."""
    raw_form = await request.form()
    form = {key: str(value) for key, value in raw_form.multi_items()}
    ids = {int(raw) for raw in raw_form.getlist("ids") if str(raw).isdigit()}
    # Solo productos de este distribuidor: los ids de otros se ignoran.
    products = (
        await session.scalars(
            select(Product).where(Product.distributor_id == distributor_id, Product.id.in_(ids)).order_by(Product.name)
        )
    ).all()
    plan = catalog.plan_catalog_changes(products, form)
    if plan.errors:
        return await _products_page(
            request, session, distributor_id, error="No se guardó nada. Corrige: " + " · ".join(plan.errors),
            status_code=status.HTTP_400_BAD_REQUEST, submitted=form, error_ids=plan.error_ids,
        )
    by_id = {product.id: product for product in products}
    for change in plan.changes:
        product = by_id[change.product_id]
        product.unit_price = change.unit_price
        product.available_units = change.available_units
        product.active = change.active
    await session.commit()

    notices = [change.notice for change in plan.changes if change.notice]
    if notices:
        _notify(request, background, distributor_id, bot_texts.catalog_notice(notices))
    return RedirectResponse(
        f"/panel/productos?ok=lote&n={len(plan.changes)}&avisos={len(notices)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


SALES_CHART_WEEKS = 8


@router.get("/productos/{product_id}/ventas")
async def product_sales(
    product_id: int,
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Ventas del producto por día de la semana y por tienda, últimas 8 semanas (para el gráfico)."""
    product = await session.get(Product, product_id)
    if product is None or product.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    stores = await _distributor_stores(session, distributor_id)
    today = local_date(datetime.now(timezone.utc))
    start = datetime.combine(today - timedelta(days=SALES_CHART_WEEKS * 7 - 1), datetime.min.time(), COLOMBIA_TZ)
    rows = (
        await session.execute(
            select(Sale.store_id, Sale.sold_at, Sale.quantity)
            .join(Store, Store.id == Sale.store_id)
            .where(Store.distributor_id == distributor_id, Sale.product_id == product_id, Sale.sold_at >= start)
        )
    ).all()
    data = aggregations.weekday_sales([tuple(row) for row in rows], [(store.id, store.name) for store in stores])
    return JSONResponse({"product": product.name, "weeks": SALES_CHART_WEEKS, **data})


@router.get("/actualizacion")
async def live_version(
    distributor_id: int = Depends(require_distributor),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Huella de los datos que llegan desde Telegram. El panel la consulta cada pocos segundos
    y se actualiza solo cuando cambia (venta, conteo, recibo, pedido o tienda vinculada)."""
    store_ids = select(Store.id).where(Store.distributor_id == distributor_id).scalar_subquery()
    row = (
        await session.execute(
            select(
                select(func.coalesce(func.max(Sale.id), 0)).where(Sale.store_id.in_(store_ids)).scalar_subquery(),
                select(func.count(Sale.id)).where(Sale.store_id.in_(store_ids)).scalar_subquery(),
                select(func.coalesce(func.max(StockCount.id), 0)).where(StockCount.store_id.in_(store_ids)).scalar_subquery(),
                select(func.coalesce(func.max(Receipt.id), 0)).where(Receipt.store_id.in_(store_ids)).scalar_subquery(),
                select(func.coalesce(func.max(SuggestedOrder.id), 0)).where(SuggestedOrder.store_id.in_(store_ids)).scalar_subquery(),
                select(func.count(SuggestedOrder.id))
                .where(SuggestedOrder.store_id.in_(store_ids), SuggestedOrder.status == "received")
                .scalar_subquery(),
                select(func.count(Store.id))
                .where(Store.distributor_id == distributor_id, Store.telegram_chat_id.is_not(None))
                .scalar_subquery(),
                select(func.count(Store.id))
                .where(Store.distributor_id == distributor_id, Store.delivery_days_set_at.is_not(None))
                .scalar_subquery(),
            )
        )
    ).one()
    return JSONResponse({"version": "-".join(str(value) for value in row)}, headers={"Cache-Control": "no-store"})
