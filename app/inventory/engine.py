"""Motor de inventario determinista.

Todo el cálculo vive en funciones puras (sin base de datos) para poder probarlo.
`load_store_analysis` es el único punto que lee de la base y arma las entradas.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, Receipt, Sale, StockCount, Store

# Colombia no tiene horario de verano: UTC-5 fijo (evita depender de tzdata en Windows).
COLOMBIA_TZ = timezone(timedelta(hours=-5))

WINDOW_DAYS = 28
SAFETY_Z = 1.65
STAGNANT_NO_SALE_DAYS = 21
STAGNANT_COVERAGE_DAYS = 45
ABC_A_LIMIT = 0.80
ABC_B_LIMIT = 0.95
PROJECTION_DAYS = 7


@dataclass(frozen=True)
class ProductInput:
    product_id: int
    sku: str
    name: str
    units_per_case: int
    unit_price: Decimal


@dataclass(frozen=True)
class SaleEvent:
    product_id: int
    quantity: int
    unit_price: Decimal
    sold_at: datetime


@dataclass(frozen=True)
class CountEvent:
    product_id: int
    quantity: int
    counted_at: datetime


@dataclass(frozen=True)
class ReceiptEvent:
    product_id: int
    quantity: int
    received_at: datetime


@dataclass
class ProductAnalysis:
    product_id: int
    sku: str
    name: str
    units_per_case: int
    unit_price: Decimal
    stock: int
    has_count: bool
    daily_sales: float
    std_dev: float
    coverage_days: float
    horizon_days: int
    safety_stock: float
    need: float
    cases: int
    units: int
    sold_value_28d: Decimal
    last_sale_at: datetime | None
    abc_class: str = "C"
    at_risk: bool = False
    stagnant: bool = False
    reason: str = ""
    projected_units_7d: float = 0.0


@dataclass
class OrderLine:
    product_id: int
    sku: str
    name: str
    cases: int
    units: int
    subtotal: Decimal
    reason: str


@dataclass
class StoreAnalysis:
    store_id: int
    as_of: datetime
    horizon_days: int
    products: list[ProductAnalysis] = field(default_factory=list)
    order_lines: list[OrderLine] = field(default_factory=list)

    @property
    def order_total(self) -> Decimal:
        return sum((line.subtotal for line in self.order_lines), Decimal("0"))

    @property
    def at_risk(self) -> list[ProductAnalysis]:
        return [p for p in self.products if p.at_risk]

    @property
    def stagnant(self) -> list[ProductAnalysis]:
        return [p for p in self.products if p.stagnant]


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def local_date(value: datetime) -> date:
    return _ensure_aware(value).astimezone(COLOMBIA_TZ).date()


def parse_visit_weekdays(raw: str) -> list[int]:
    """'0,3' -> [0, 3] (0 = lunes ... 6 = domingo). Ignora valores inválidos."""
    days: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.add(int(part))
    return sorted(days)


def days_until_next_visit(visit_weekdays: str, today: date) -> int:
    """Días hasta la próxima visita, estrictamente después de hoy (1..7).

    Si hoy es día de visita, el pedido de hoy ya no alcanza: se cuenta la siguiente.
    Sin días configurados se asume una visita semanal (7).
    """
    days = parse_visit_weekdays(visit_weekdays)
    if not days:
        return 7
    current = today.weekday()
    return min(((d - current - 1) % 7) + 1 for d in days)


def compute_stock(
    counts: list[CountEvent],
    receipts: list[ReceiptEvent],
    sales: list[SaleEvent],
) -> tuple[int, bool]:
    """Stock = último conteo + recibidos posteriores − ventas posteriores (mínimo 0).

    Todos los eventos deben ser de un mismo producto. Devuelve (stock, tiene_conteo).
    """
    if not counts:
        return 0, False
    last = max(counts, key=lambda c: _ensure_aware(c.counted_at))
    since = _ensure_aware(last.counted_at)
    received = sum(r.quantity for r in receipts if _ensure_aware(r.received_at) > since)
    sold = sum(s.quantity for s in sales if _ensure_aware(s.sold_at) > since)
    return max(0, last.quantity + received - sold), True


def daily_sales_series(sales: list[SaleEvent], today: date, window: int = WINDOW_DAYS) -> list[int]:
    """Unidades vendidas por día en los últimos `window` días (incluye hoy). Días sin venta = 0."""
    start = today - timedelta(days=window - 1)
    series = [0] * window
    for sale in sales:
        day = local_date(sale.sold_at)
        if start <= day <= today:
            series[(day - start).days] += sale.quantity
    return series


def classify_abc(values: dict[int, Decimal]) -> dict[int, str]:
    """ABC por valor vendido. Se ordena de mayor a menor; un producto es A si el
    acumulado ANTES de sumarlo es menor al 80 %, B si es menor al 95 %, si no C.
    Así el producto más vendido siempre es A. Productos sin ventas son C."""
    total = sum(values.values(), Decimal("0"))
    result = {pid: "C" for pid in values}
    if total <= 0:
        return result
    cumulative = Decimal("0")
    for pid, value in sorted(values.items(), key=lambda kv: (-kv[1], kv[0])):
        if value <= 0:
            break
        share_before = cumulative / total
        if share_before < Decimal(str(ABC_A_LIMIT)):
            result[pid] = "A"
        elif share_before < Decimal(str(ABC_B_LIMIT)):
            result[pid] = "B"
        cumulative += value
    return result


def _fmt_qty(value: float) -> str:
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}".replace(".", ",")


def _days_word(value: float) -> str:
    return "día" if _fmt_qty(value) == "1" else "días"


def build_reason(daily: float, coverage: float, has_count: bool) -> str:
    if daily <= 0:
        return "No se ha vendido en 4 semanas"
    sells = f"Vendes {_fmt_qty(daily)} al día"
    if not has_count:
        return f"{sells}, sin conteo de estante"
    if coverage < 1:
        return f"{sells}, ya casi no te queda"
    days = math.floor(coverage)
    return f"{sells}, te alcanza para {days} {_days_word(days)}"


def analyze_product(
    product: ProductInput,
    sales: list[SaleEvent],
    counts: list[CountEvent],
    receipts: list[ReceiptEvent],
    today: date,
    horizon_days: int,
) -> ProductAnalysis:
    stock, has_count = compute_stock(counts, receipts, sales)
    series = daily_sales_series(sales, today)
    daily = sum(series) / WINDOW_DAYS
    std_dev = statistics.pstdev(series)
    coverage = stock / daily if daily > 0 else math.inf
    safety = SAFETY_Z * std_dev * math.sqrt(horizon_days)
    need = daily * horizon_days + safety - stock
    units_per_case = max(1, product.units_per_case)
    cases = math.ceil(max(0.0, need) / units_per_case)

    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    sold_value = sum(
        (Decimal(s.quantity) * s.unit_price for s in sales if local_date(s.sold_at) >= window_start
         and local_date(s.sold_at) <= today),
        Decimal("0"),
    )
    last_sale_at = max((_ensure_aware(s.sold_at) for s in sales), default=None)

    no_recent_sale = last_sale_at is None or (today - local_date(last_sale_at)).days >= STAGNANT_NO_SALE_DAYS
    stagnant = stock > 0 and (no_recent_sale or coverage > STAGNANT_COVERAGE_DAYS)
    at_risk = daily > 0 and coverage < horizon_days

    return ProductAnalysis(
        product_id=product.product_id,
        sku=product.sku,
        name=product.name,
        units_per_case=units_per_case,
        unit_price=product.unit_price,
        stock=stock,
        has_count=has_count,
        daily_sales=daily,
        std_dev=std_dev,
        coverage_days=coverage,
        horizon_days=horizon_days,
        safety_stock=safety,
        need=need,
        cases=cases,
        units=cases * units_per_case,
        sold_value_28d=sold_value,
        last_sale_at=last_sale_at,
        at_risk=at_risk,
        stagnant=stagnant,
        reason=build_reason(daily, coverage, has_count),
        projected_units_7d=daily * PROJECTION_DAYS,
    )


def analyze_store(
    store_id: int,
    visit_weekdays: str,
    lead_time_days: int,
    products: list[ProductInput],
    sales: list[SaleEvent],
    counts: list[CountEvent],
    receipts: list[ReceiptEvent],
    now: datetime,
) -> StoreAnalysis:
    now = _ensure_aware(now)
    today = local_date(now)
    horizon = days_until_next_visit(visit_weekdays, today) + max(0, lead_time_days)

    sales_by_product: dict[int, list[SaleEvent]] = defaultdict(list)
    counts_by_product: dict[int, list[CountEvent]] = defaultdict(list)
    receipts_by_product: dict[int, list[ReceiptEvent]] = defaultdict(list)
    for s in sales:
        if _ensure_aware(s.sold_at) <= now:
            sales_by_product[s.product_id].append(s)
    for c in counts:
        if _ensure_aware(c.counted_at) <= now:
            counts_by_product[c.product_id].append(c)
    for r in receipts:
        if _ensure_aware(r.received_at) <= now:
            receipts_by_product[r.product_id].append(r)

    analyses = [
        analyze_product(
            p,
            sales_by_product[p.product_id],
            counts_by_product[p.product_id],
            receipts_by_product[p.product_id],
            today,
            horizon,
        )
        for p in products
    ]

    abc = classify_abc({a.product_id: a.sold_value_28d for a in analyses})
    for a in analyses:
        a.abc_class = abc[a.product_id]

    order_lines = [
        OrderLine(
            product_id=a.product_id,
            sku=a.sku,
            name=a.name,
            cases=a.cases,
            units=a.units,
            subtotal=Decimal(a.units) * a.unit_price,
            reason=a.reason,
        )
        for a in analyses
        if a.cases > 0
    ]
    order_lines.sort(key=lambda line: (-line.subtotal, line.name))

    return StoreAnalysis(
        store_id=store_id,
        as_of=now,
        horizon_days=horizon,
        products=analyses,
        order_lines=order_lines,
    )


async def load_store_analysis(
    session: AsyncSession, store_id: int, now: datetime | None = None
) -> StoreAnalysis | None:
    """Lee de la base todo lo necesario para una tienda y corre el motor.

    Solo productos activos del distribuidor dueño de la tienda (aislamiento multi-tenant).
    """
    now = _ensure_aware(now or datetime.now(timezone.utc))
    store = await session.get(Store, store_id)
    if store is None:
        return None

    product_rows = (
        await session.scalars(
            select(Product)
            .where(Product.distributor_id == store.distributor_id, Product.active.is_(True))
            .order_by(Product.name)
        )
    ).all()
    products = [
        ProductInput(p.id, p.sku, p.name, p.units_per_case, Decimal(p.unit_price)) for p in product_rows
    ]

    count_rows = (
        await session.scalars(
            select(StockCount)
            .where(StockCount.store_id == store_id, StockCount.counted_at <= now)
            .order_by(StockCount.counted_at.desc())
        )
    ).all()
    latest_counts: dict[int, StockCount] = {}
    for c in count_rows:
        latest_counts.setdefault(c.product_id, c)
    counts = [CountEvent(c.product_id, c.quantity, c.counted_at) for c in latest_counts.values()]

    # Ventas necesarias: las de la ventana de 28 días, las posteriores al conteo más
    # antiguo vigente y la última venta (para detectar estancados).
    window_start = datetime.combine(
        local_date(now) - timedelta(days=WINDOW_DAYS - 1), datetime.min.time(), COLOMBIA_TZ
    )
    oldest_count = min((c.counted_at for c in latest_counts.values()), default=window_start)
    sales_since = min(_ensure_aware(oldest_count), window_start)

    sale_rows = (
        await session.scalars(
            select(Sale).where(Sale.store_id == store_id, Sale.sold_at > sales_since, Sale.sold_at <= now)
        )
    ).all()
    sales = [SaleEvent(s.product_id, s.quantity, Decimal(s.unit_price), s.sold_at) for s in sale_rows]

    receipt_rows = (
        await session.scalars(
            select(Receipt).where(
                Receipt.store_id == store_id, Receipt.received_at > sales_since, Receipt.received_at <= now
            )
        )
    ).all()
    receipts = [ReceiptEvent(r.product_id, r.quantity, r.received_at) for r in receipt_rows]

    return analyze_store(
        store_id=store.id,
        visit_weekdays=store.visit_weekdays,
        lead_time_days=store.lead_time_days,
        products=products,
        sales=sales,
        counts=counts,
        receipts=receipts,
        now=now,
    )
