"""Datos base para arrancar: distribuidor, usuario del panel, catálogo y tiendas.

Sin opciones no inventa ventas, conteos ni pedidos: todo eso llega de verdad desde Telegram.
Con --demo además crea 60 días de historia inventada en las tres tiendas (ventas, conteos,
pedidos recibidos), con un producto que se agota cada fin de semana, uno estancado y uno que crece.
Volver a correrlo borra los datos de este distribuidor (también las ventas reales).
"""

import argparse
import asyncio
import math
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, engine
from app.inventory.engine import COLOMBIA_TZ, parse_visit_weekdays
from app.models import (
    Distributor,
    DistributorUser,
    Product,
    Receipt,
    Sale,
    StockCount,
    Store,
    SuggestedOrder,
    SuggestedOrderItem,
)

DISTRIBUTOR_NAME = "Distribuidora El Barrio"
DEMO_EMAIL = "demo@demo.co"

# (sku, nombre, otros nombres, presentación, unidades por caja, precio por unidad)
PRODUCTS = [
    ("GAS-COCA-1L", "Coca-Cola 1 litro", "coca litro,coca cola litro,coca 1l,gaseosa coca litro", "1 L", 12, 4500),
    ("GAS-COCA-400", "Coca-Cola 400 ml", "coca personal,coca 400,cocacola pequeña", "400 ml", 24, 2500),
    ("GAS-POST-1.5", "Postobón 1.5 litros", "postobon,postobón litro y medio,gaseosa postobon", "1.5 L", 12, 4000),
    ("GAS-COLO-400", "Colombiana 400 ml", "colombiana,kola,gaseosa colombiana", "400 ml", 24, 2200),
    ("GAS-MANZ-400", "Manzana Postobón 400 ml", "manzana,gaseosa manzana,postobon manzana", "400 ml", 24, 2200),
    ("MAL-PONY-330", "Pony Malta 330 ml", "pony,malta pony,pony malta", "330 ml", 24, 2500),
    ("AGU-CRIS-600", "Agua Cristal 600 ml", "agua,cristal,agua cristal,botella de agua", "600 ml", 24, 1800),
    ("AGU-BRIS-600", "Agua Brisa 600 ml", "brisa,agua brisa", "600 ml", 24, 1800),
    ("ARR-ROSA-500", "Arroz Diana 500 g", "arroz,diana,arroz diana,arroza", "500 g", 20, 2800),
    ("ARR-ROSA-1K", "Arroz Roa 1 kg", "arroz roa,roa,arroz kilo", "1 kg", 12, 4800),
    ("ACE-GOUR-1L", "Aceite Gourmet 1 litro", "aceite,gourmet,aceite litro", "1 L", 12, 12000),
    ("ACE-PREM-3L", "Aceite Premier 3 litros", "aceite grande,premier,aceite 3 litros", "3 L", 6, 28000),
    ("AZU-MANU-1K", "Azúcar Manuelita 1 kg", "azucar,azúcar,manuelita,azucar kilo", "1 kg", 12, 4500),
    ("HUE-AAA-30", "Huevos AAA x 30", "huevos,huevo,cubeta,huevos aaa", "x 30", 1, 18000),
    ("PAN-TAJA-UND", "Pan tajado", "pan,pan tajado,pan de caja,tajado", "unidad", 10, 5500),
    ("LEC-ALQU-1L", "Leche Alquería 1 litro", "leche,alqueria,alquería,leche litro", "1 L", 12, 4200),
    ("LEC-ALPI-1L", "Leche Alpina 1 litro", "leche alpina,alpina", "1 L", 12, 4500),
    ("CAF-SELLO-250", "Café Sello Rojo 250 g", "cafe,café,sello rojo,tinto", "250 g", 20, 9800),
    ("CAF-AGUI-125", "Café Águila Roja 125 g", "aguila roja,águila roja,cafe aguila", "125 g", 24, 5200),
    ("CON-JET-UND", "Jet chocolate", "jet,chocolatina jet,chocolate jet", "unidad", 24, 1200),
    ("CON-BUBA-UND", "Bubalú", "bubalu,bubalú,chicle", "unidad", 50, 500),
    ("CON-BONB-UND", "Bon Bon Bum", "bon bon bum,bombon,chupeta", "unidad", 48, 700),
    ("ASE-FAB-500", "Fab polvo 500 g", "fab,detergente,jabon en polvo,jabón en polvo", "500 g", 12, 6500),
    ("ASE-AXIO-400", "Axion 400 g", "axion,lavaloza,jabon de loza", "400 g", 12, 4800),
    ("ASE-JABO-3", "Jabón Rey x 3", "jabon rey,jabón,rey,jabon de ropa", "x 3", 12, 5500),
    ("ASE-BLAN-1L", "Blanqueador 1 litro", "cloro,blanqueador,límpido,limpido", "1 L", 12, 3500),
    ("ASE-PAPEL-4", "Papel higiénico x 4", "papel,papel higienico,papel higiénico,familia", "x 4", 10, 7500),
    ("SAL-REFIS-1K", "Sal Refisal 1 kg", "sal,refisal", "1 kg", 20, 1800),
    ("PAS-DORIA-250", "Pasta Doria 250 g", "pasta,espagueti,doria,fideo", "250 g", 20, 2200),
    ("ATU-VANC-170", "Atún Van Camps 170 g", "atun,atún,van camps,lata de atun", "170 g", 24, 6900),
]

# (nombre, dueño, días de ruta del proveedor, días de entrega iniciales,
#  días de anticipación para pedir). Los días de entrega iniciales se usan hasta que el tendero
#  escoja los suyos en el bot.
STORES = [
    ("Tienda Doña Marta", "Marta Gómez", "0,2,4", "0,4", 1),
    ("Mini Market El Parque", "Carlos Ruiz", "1,3,5", "1,5", 1),
    ("La Esquina de Luz", "Luz Helena Díaz", "0,2,4", "0,2", 2),
]


HISTORY_DAYS = 60
# Conteo con foto que "hizo" el tendero hace dos semanas (el stock actual parte de ahí).
PHOTO_COUNT_DAYS_AGO = 14

# Venta diaria promedio (unidades) de cada producto en una tienda de tamaño normal.
BASE_DEMAND = {
    "GAS-COCA-1L": 9, "GAS-COCA-400": 7, "GAS-POST-1.5": 3, "GAS-COLO-400": 3, "GAS-MANZ-400": 2,
    "MAL-PONY-330": 2, "AGU-CRIS-600": 4, "AGU-BRIS-600": 2, "ARR-ROSA-500": 4, "ARR-ROSA-1K": 2,
    "ACE-GOUR-1L": 1.5, "ACE-PREM-3L": 0.3, "AZU-MANU-1K": 2, "HUE-AAA-30": 0.6, "PAN-TAJA-UND": 3,
    "LEC-ALQU-1L": 5, "LEC-ALPI-1L": 2, "CAF-SELLO-250": 1, "CAF-AGUI-125": 1.5, "CON-JET-UND": 5,
    "CON-BUBA-UND": 6, "CON-BONB-UND": 6, "ASE-FAB-500": 1, "ASE-AXIO-400": 1, "ASE-JABO-3": 1,
    "ASE-BLAN-1L": 1, "ASE-PAPEL-4": 1.5, "SAL-REFIS-1K": 0.8, "PAS-DORIA-250": 2, "ATU-VANC-170": 1,
}
# lunes=0 ... domingo=6. El fin de semana se vende más.
WEEKDAY_FACTOR = [0.85, 0.8, 0.85, 0.9, 1.2, 1.5, 1.3]
# Tamaño relativo de cada tienda (mismo orden que STORES).
STORE_SCALE = [1.0, 1.3, 0.8]
# Días de venta que el tendero pide de cada producto en cada entrega.
COVER_DAYS = 7

# Patrones que pide el reto.
WEEKEND_RUSH_SKU = "GAS-COCA-1L"  # el fin de semana se dispara y se agota
WEEKEND_RUSH_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.8, 2.6, 2.2]
WEEKEND_RUSH_COVER_DAYS = 2  # el tendero pide poco y se queda corto
STAGNANT_SKU = "ACE-PREM-3L"  # dejó de venderse hace más de un mes
STAGNANT_LAST_SALE_DAYS_AGO = 35
STAGNANT_INITIAL_UNITS = 18
GROWING_SKU = "MAL-PONY-330"  # cada semana se vende más
GROWING_FINAL_FACTOR = 4.0  # termina vendiendo 4 veces lo del primer día
GROWING_HABIT_DAYS = 28
GROWING_COVER_DAYS = 3


def _poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), COLOMBIA_TZ)


def _expected_demand(sku: str, scale: float, day: date, days_ago: int) -> float:
    mean = BASE_DEMAND[sku] * scale
    if sku == WEEKEND_RUSH_SKU:
        return mean * WEEKEND_RUSH_FACTOR[day.weekday()]
    if sku == STAGNANT_SKU:
        return mean if days_ago > STAGNANT_LAST_SALE_DAYS_AGO else 0.0
    if sku == GROWING_SKU:
        progress = (HISTORY_DAYS - days_ago) / HISTORY_DAYS
        mean *= 1 + (GROWING_FINAL_FACTOR - 1) * progress
    return mean * WEEKDAY_FACTOR[day.weekday()]


def _reorder_units(sku: str, recent_sales: list[int], stock: int, units_per_case: int) -> int:
    """Lo que el tendero habría pedido: lo de unos días según lo que vendió la última semana."""
    if sku == STAGNANT_SKU:
        return 0
    # Con el que crece, el tendero pide por costumbre (lo del último mes) y se queda corto.
    window = GROWING_HABIT_DAYS if sku == GROWING_SKU else 7
    daily = sum(recent_sales[-window:]) / max(len(recent_sales[-window:]), 1)
    cover = {WEEKEND_RUSH_SKU: WEEKEND_RUSH_COVER_DAYS, GROWING_SKU: GROWING_COVER_DAYS}.get(sku, COVER_DAYS)
    missing = daily * cover - stock
    return math.ceil(missing / units_per_case) * units_per_case if missing > 0 else 0


def build_history(store: Store, scale: float, products: list[Product], today: date, rng: random.Random) -> list:
    """Simula día a día ventas, entregas y conteos de una tienda. Las ventas nunca superan el stock."""
    rows: list = []
    delivery_weekdays = set(parse_visit_weekdays(store.visit_weekdays))
    start = today - timedelta(days=HISTORY_DAYS)
    stock: dict[int, int] = {}
    history: dict[int, list[int]] = {p.id: [] for p in products}

    for product in products:
        if product.sku == STAGNANT_SKU:
            initial = STAGNANT_INITIAL_UNITS
        else:
            initial = math.ceil(BASE_DEMAND[product.sku] * scale * COVER_DAYS)
        stock[product.id] = initial
        rows.append(
            StockCount(store_id=store.id, product_id=product.id, quantity=initial,
                       counted_at=_at(start, 6, 30), source="manual")
        )

    for days_ago in range(HISTORY_DAYS, 0, -1):
        day = today - timedelta(days=days_ago)

        if days_ago == PHOTO_COUNT_DAYS_AGO:
            for product in products:
                rows.append(
                    StockCount(store_id=store.id, product_id=product.id, quantity=stock[product.id],
                               counted_at=_at(day, 6, 30), source="photo")
                )

        if day.weekday() in delivery_weekdays and day > start:
            order = SuggestedOrder(store_id=store.id, status="received", delivery_date=day,
                                   created_at=_at(day - timedelta(days=store.lead_time_days), 15))
            received_at = _at(day, 7)
            for product in products:
                units = _reorder_units(product.sku, history[product.id], stock[product.id],
                                       product.units_per_case)
                if units == 0:
                    continue
                order.items.append(
                    SuggestedOrderItem(product_id=product.id, cases=units // product.units_per_case,
                                       units=units, reason="Reposición de la semana")
                )
                rows.append(Receipt(store_id=store.id, product_id=product.id, quantity=units,
                                    received_at=received_at))
                stock[product.id] += units
            if order.items:
                rows.append(order)

        for product in products:
            wanted = _poisson(rng, _expected_demand(product.sku, scale, day, days_ago))
            sold = min(wanted, stock[product.id])
            history[product.id].append(sold)
            if sold == 0:
                continue
            stock[product.id] -= sold
            rows.append(
                Sale(store_id=store.id, product_id=product.id, quantity=sold, unit_price=product.unit_price,
                     sold_at=_at(day, rng.randint(8, 20), rng.randint(0, 59)),
                     source=rng.choice(["voice", "text"]))
            )
    return rows


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def seed(session: AsyncSession, demo_history: bool = False) -> None:
    settings = get_settings()
    existing = await session.scalar(select(Distributor).where(Distributor.name == DISTRIBUTOR_NAME))
    if existing is not None:
        store_ids = list(
            (await session.scalars(select(Store.id).where(Store.distributor_id == existing.id))).all()
        )
        if store_ids:
            await session.execute(delete(Sale).where(Sale.store_id.in_(store_ids)))
            await session.execute(delete(StockCount).where(StockCount.store_id.in_(store_ids)))
        # Las tiendas primero: su borrado en cascada se lleva recibos, pedidos y borradores,
        # que apuntan a productos con RESTRICT.
        await session.execute(delete(Store).where(Store.distributor_id == existing.id))
        await session.execute(delete(Product).where(Product.distributor_id == existing.id))
        await session.execute(delete(DistributorUser).where(DistributorUser.distributor_id == existing.id))
        await session.delete(existing)
        await session.flush()

    distributor = Distributor(name=DISTRIBUTOR_NAME)
    session.add(distributor)
    await session.flush()

    session.add(
        DistributorUser(
            distributor_id=distributor.id,
            email=DEMO_EMAIL,
            password_hash=hash_password(settings.demo_password),
        )
    )

    products = []
    for sku, name, aliases, presentation, units_per_case, unit_price in PRODUCTS:
        products.append(
            Product(
                distributor_id=distributor.id,
                sku=sku,
                name=name,
                aliases=aliases,
                presentation=presentation,
                units_per_case=units_per_case,
                unit_price=Decimal(unit_price),
                active=True,
                # Sin cantidad registrada (sin límite) hasta que el distribuidor la ponga en el panel.
                available_units=None,
            )
        )

    session.add_all(products)

    # Sin celular: el distribuidor lo registra en el panel (Tiendas) para que el tendero pueda entrar.
    stores = []
    for name, owner_name, route_weekdays, visit_weekdays, lead_time_days in STORES:
        stores.append(
            Store(
                distributor_id=distributor.id,
                name=name,
                owner_name=owner_name,
                route_weekdays=route_weekdays,
                visit_weekdays=visit_weekdays,
                lead_time_days=lead_time_days,
            )
        )
    session.add_all(stores)

    if demo_history:
        await session.flush()
        today = datetime.now(timezone.utc).astimezone(COLOMBIA_TZ).date()
        # Semilla fija: la demo sale igual cada vez que se corre.
        rng = random.Random(1581)
        for store, scale in zip(stores, STORE_SCALE):
            session.add_all(build_history(store, scale, products, today, rng))

    await session.commit()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Carga los datos base de TenderoBot.")
    parser.add_argument("--demo", action="store_true", help="crea además 60 días de historia inventada")
    args = parser.parse_args()
    async with SessionLocal() as session:
        await seed(session, demo_history=args.demo)
    await engine.dispose()
    history = f"{HISTORY_DAYS} días de historia de prueba" if args.demo else "sin ventas"
    print(f"Listo: {DEMO_EMAIL} / {len(STORES)} tiendas sin vincular / {len(PRODUCTS)} productos / {history}")


if __name__ == "__main__":
    asyncio.run(main())
