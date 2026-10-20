"""Agregaciones del panel sobre resultados del motor de inventario.

Funciones puras: reciben StoreAnalysis ya calculados y no tocan la base de datos.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from app.inventory.engine import ProductAnalysis, StoreAnalysis, local_date

# Evita que errores de punto flotante (14.0000000001) suban una unidad al redondear.
_FLOAT_PRECISION = 6


@dataclass(frozen=True)
class DemandRow:
    product_id: int
    sku: str
    name: str
    units_per_case: int
    units: int
    cases: int


@dataclass(frozen=True)
class RiskRow:
    product_id: int
    name: str
    stores_at_risk: int


def _ceil(value: float) -> int:
    return math.ceil(round(value, _FLOAT_PRECISION))


def aggregate_demand(analyses: list[StoreAnalysis]) -> list[DemandRow]:
    """Suma la demanda proyectada a 7 días de cada producto en todas las tiendas.

    Unidades y cajas se redondean hacia arriba. Orden: más unidades primero.
    """
    totals: dict[int, float] = {}
    info: dict[int, ProductAnalysis] = {}
    for analysis in analyses:
        for product in analysis.products:
            totals[product.product_id] = totals.get(product.product_id, 0.0) + product.projected_units_7d
            info.setdefault(product.product_id, product)

    rows = []
    for product_id, projected in totals.items():
        product = info[product_id]
        units = _ceil(projected)
        if units <= 0:
            continue  # sin ventas en ninguna tienda: no hay nada que proyectar
        per_case = max(1, product.units_per_case)
        rows.append(
            DemandRow(
                product_id=product_id,
                sku=product.sku,
                name=product.name,
                units_per_case=per_case,
                units=units,
                cases=math.ceil(units / per_case),
            )
        )
    rows.sort(key=lambda row: (-row.units, row.name))
    return rows


def count_stores_at_risk(analyses: list[StoreAnalysis]) -> int:
    return sum(1 for analysis in analyses if analysis.at_risk)


def top_risk_products(analyses: list[StoreAnalysis], limit: int = 5) -> list[RiskRow]:
    """Productos que están en riesgo de quiebre en más tiendas."""
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for analysis in analyses:
        for product in analysis.at_risk:
            counts[product.product_id] = counts.get(product.product_id, 0) + 1
            names[product.product_id] = product.name
    rows = [RiskRow(pid, names[pid], count) for pid, count in counts.items()]
    rows.sort(key=lambda row: (-row.stores_at_risk, row.name))
    return rows[:limit]


def product_status(product: ProductAnalysis) -> str:
    if product.at_risk:
        return "Riesgo"
    if product.stagnant:
        return "Estancado"
    return "OK"


WEEKDAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
WEEKDAY_FULL = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def weekday_sales(rows: list[tuple[int, datetime, int]], stores: list[tuple[int, str]]) -> dict:
    """Unidades vendidas por día de la semana (hora Colombia), por tienda.

    `rows` son (store_id, sold_at, quantity) de un producto. Cada tienda trae sus 7 valores
    (lunes a domingo) y su total; las tiendas sin ventas también aparecen, en cero.
    """
    values = {store_id: [0] * 7 for store_id, _name in stores}
    for store_id, sold_at, quantity in rows:
        if store_id in values:
            values[store_id][local_date(sold_at).weekday()] += quantity
    return {
        "labels": WEEKDAY_SHORT,
        "full_labels": WEEKDAY_FULL,
        "stores": [
            {"id": store_id, "name": name, "values": values[store_id], "total": sum(values[store_id])}
            for store_id, name in stores
        ],
    }
