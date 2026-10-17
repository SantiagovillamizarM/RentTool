import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import (
    Distributor,
    DistributorUser,
    Product,
    Sale,
    StockCount,
    Store,
)

SEED = 1581
DISTRIBUTOR_NAME = "Distribuidora El Barrio"
DEMO_EMAIL = "demo@demo.co"

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

STORES = [
    ("Tienda Doña Marta", "Marta Gómez", "MARTA01", "0,3", 1),
    ("Mini Market El Parque", "Carlos Ruiz", "PARQUE02", "1,4", 1),
    ("La Esquina de Luz", "Luz Helena Díaz", "LUZ03", "0,3", 2),
]

GROWING_SKU = "GAS-COCA-1L"
STAGNANT_SKU = "CAF-AGUI-125"
WEEKEND_SKU = "PAN-TAJA-UND"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def daily_quantity(sku: str, day: datetime, rng: random.Random) -> int:
    weekday = day.weekday()
    if sku == STAGNANT_SKU:
        return 0
    if sku == WEEKEND_SKU:
        if weekday in {4, 5, 6}:
            return rng.randint(8, 14)
        return rng.randint(0, 1)
    if sku == GROWING_SKU:
        base = 2 + int(day.day / 4)
        return max(1, base + rng.randint(-1, 2))
    if weekday in {4, 5, 6}:
        return rng.randint(1, 5)
    return rng.randint(0, 3)


async def seed(session: AsyncSession) -> None:
    settings = get_settings()
    existing = await session.scalar(select(Distributor).where(Distributor.name == DISTRIBUTOR_NAME))
    if existing is not None:
        store_ids = list(
            (
                await session.scalars(select(Store.id).where(Store.distributor_id == existing.id))
            ).all()
        )
        if store_ids:
            await session.execute(delete(Sale).where(Sale.store_id.in_(store_ids)))
            await session.execute(delete(StockCount).where(StockCount.store_id.in_(store_ids)))
        # Las tiendas primero: su borrado en cascada se lleva recibos, pedidos y borradores,
        # que apuntan a productos con RESTRICT.
        await session.execute(delete(Store).where(Store.distributor_id == existing.id))
        await session.execute(delete(Product).where(Product.distributor_id == existing.id))
        await session.execute(
            delete(DistributorUser).where(DistributorUser.distributor_id == existing.id)
        )
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

    products: dict[str, Product] = {}
    for sku, name, aliases, presentation, units_per_case, unit_price in PRODUCTS:
        product = Product(
            distributor_id=distributor.id,
            sku=sku,
            name=name,
            aliases=aliases,
            presentation=presentation,
            units_per_case=units_per_case,
            unit_price=Decimal(unit_price),
            active=True,
        )
        session.add(product)
        products[sku] = product
    await session.flush()

    stores: list[Store] = []
    for name, owner_name, link_code, visit_weekdays, lead_time_days in STORES:
        store = Store(
            distributor_id=distributor.id,
            name=name,
            owner_name=owner_name,
            link_code=link_code,
            visit_weekdays=visit_weekdays,
            lead_time_days=lead_time_days,
        )
        session.add(store)
        stores.append(store)
    await session.flush()

    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=59)
    rng = random.Random(SEED)

    count_rng = random.Random(SEED + 1)
    count_cutoff = today - timedelta(days=1)

    for store in stores:
        recent_units: dict[str, int] = {sku: 0 for sku in products}
        for offset in range(60):
            sold_at = start + timedelta(days=offset)
            for sku, product in products.items():
                quantity = daily_quantity(sku, sold_at, rng)
                if quantity <= 0:
                    continue
                if sold_at > count_cutoff - timedelta(days=28):
                    recent_units[sku] += quantity
                session.add(
                    Sale(
                        store_id=store.id,
                        product_id=product.id,
                        quantity=quantity,
                        unit_price=product.unit_price,
                        sold_at=sold_at,
                        source="manual",
                    )
                )
        # Conteo de ayer en la noche: así el stock refleja solo las ventas de hoy.
        counted_at = count_cutoff.replace(hour=21)
        for sku, product in products.items():
            daily = recent_units[sku] / 28
            if sku == STAGNANT_SKU:
                quantity = 18
            elif sku == WEEKEND_SKU:
                quantity = 2
            elif sku == GROWING_SKU:
                quantity = max(1, round(daily * 1.5))
            else:
                # Días de cobertura variados: unos casi agotados, otros sobrados.
                cover_days = count_rng.choice([1, 2, 3, 5, 8, 12, 20, 60])
                quantity = max(0, round(daily * cover_days))
            session.add(
                StockCount(
                    store_id=store.id,
                    product_id=product.id,
                    quantity=quantity,
                    counted_at=counted_at,
                    source="manual",
                )
            )

    await session.commit()


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
    await engine.dispose()
    print("Seed listo: demo@demo.co / 3 tiendas / 30 productos / 60 días")


if __name__ == "__main__":
    asyncio.run(main())
