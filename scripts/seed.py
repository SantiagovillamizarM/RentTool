"""Datos base para arrancar: distribuidor, usuario del panel, catálogo y tiendas.

No inventa ventas, conteos ni pedidos: todo eso llega de verdad desde Telegram.
Volver a correrlo borra los datos de este distribuidor (también las ventas reales).
"""

import asyncio
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


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def seed(session: AsyncSession) -> None:
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

    for sku, name, aliases, presentation, units_per_case, unit_price in PRODUCTS:
        session.add(
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

    # Sin celular: el distribuidor lo registra en el panel (Tiendas) para que el tendero pueda entrar.
    for name, owner_name, route_weekdays, visit_weekdays, lead_time_days in STORES:
        session.add(
            Store(
                distributor_id=distributor.id,
                name=name,
                owner_name=owner_name,
                route_weekdays=route_weekdays,
                visit_weekdays=visit_weekdays,
                lead_time_days=lead_time_days,
            )
        )

    await session.commit()


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
    await engine.dispose()
    print(f"Listo: {DEMO_EMAIL} / {len(STORES)} tiendas sin vincular / {len(PRODUCTS)} productos / sin ventas")


if __name__ == "__main__":
    asyncio.run(main())
