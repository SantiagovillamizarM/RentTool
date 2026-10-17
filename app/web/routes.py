from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.inventory.engine import COLOMBIA_TZ, StoreAnalysis, load_store_analysis, local_date
from app.models import Product, Sale, Store, SuggestedOrder, SuggestedOrderItem
from app.web import aggregations, formatting
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
LOGIN_RATE_LIMITED = "Demasiados intentos. Espera un minuto."

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["cop"] = formatting.cop
templates.env.filters["coverage"] = formatting.coverage
templates.env.filters["qty"] = formatting.qty
templates.env.filters["local_dt"] = lambda value: value.astimezone(COLOMBIA_TZ).strftime("%d/%m/%Y %H:%M")
templates.env.globals["product_status"] = aggregations.product_status

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
        {"id": row[0], "store_id": row[1], "created_at": row[2], "value": Decimal(row[3])}
        for row in rows
    ]


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if isinstance(request.session.get("distributor_id"), int):
        return RedirectResponse("/panel", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None, "email": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    if not login_limiter.allow(client_ip(request)):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": LOGIN_RATE_LIMITED, "email": email},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    user = await authenticate(session, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": LOGIN_ERROR, "email": email},
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

    context = {
        "active": "stores",
        "store": store,
        "analysis": analysis,
        "products": sorted(analysis.products, key=lambda p: (p.abc_class, p.name)),
        "chart": chart,
        "sent_orders": await _sent_orders(session, distributor_id, store.id),
    }
    return templates.TemplateResponse(request, "store_detail.html", context)


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
