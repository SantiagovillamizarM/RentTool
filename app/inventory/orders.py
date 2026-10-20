"""Pedido editable: aplica los cambios del tendero al pedido sugerido.

Funciones puras. Las líneas del pedido son dicts {"product_id", "cases", "reason"}, tal como
se guardan en el borrador (PendingAction). Grok solo extrae los cambios; aquí se calculan.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from app.inventory.engine import OrderLine
from app.models import Product
from app.schemas import OrderChange

ADDED_BY_YOU = "Lo agregaste tú"


@dataclass
class ChangeResult:
    lines: list[dict]
    not_found: list[str]


def change_to_cases(change: OrderChange, units_per_case: int) -> int:
    """Unidades sueltas se redondean hacia arriba a cajas completas."""
    if change.unit == "unit":
        return math.ceil(change.quantity / max(1, units_per_case))
    return change.quantity


def apply_order_changes(
    lines: list[dict], changes: list[OrderChange], products_by_sku: dict[str, Product]
) -> ChangeResult:
    result = [dict(line) for line in lines]
    not_found: list[str] = []
    for change in changes:
        product = products_by_sku.get(change.sku or "")
        if product is None:
            not_found.append(change.product_text or "producto")
            continue
        cases = change_to_cases(change, product.units_per_case)
        line = next((item for item in result if item["product_id"] == product.id), None)
        if line is None:
            line = {"product_id": product.id, "cases": 0, "reason": ADDED_BY_YOU}
            result.append(line)
        if change.action == "remove":
            line["cases"] = 0
        elif change.action == "add":
            line["cases"] += max(1, cases)
        else:
            line["cases"] = cases
    return ChangeResult([line for line in result if line["cases"] > 0], not_found)


def cap_to_available(lines: list[dict], products_by_id: dict[int, Product]) -> ChangeResult:
    """Ajusta el pedido a lo que el distribuidor tiene: nunca más cajas completas de las que hay.

    Devuelve las líneas ajustadas y, en `not_found`, una nota por cada producto recortado o agotado.
    Productos sin cantidad registrada (available_units = None) no tienen límite.
    """
    result: list[dict] = []
    notes: list[str] = []
    for line in lines:
        product = products_by_id.get(int(line.get("product_id") or 0))
        if product is None:
            continue
        if product.available_units is None:
            result.append(dict(line))
            continue
        max_cases = product.available_units // max(1, product.units_per_case)
        cases = int(line.get("cases") or 0)
        if max_cases <= 0:
            notes.append(f"{product.name} (agotado donde tu proveedor)")
            continue
        if cases > max_cases:
            word = "caja" if max_cases == 1 else "cajas"
            notes.append(f"{product.name} (solo hay {max_cases} {word})")
            cases = max_cases
        result.append({**line, "cases": cases})
    return ChangeResult(result, notes)


def to_order_lines(lines: list[dict], products_by_id: dict[int, Product]) -> list[OrderLine]:
    """Arma las líneas para mostrar: unidades y subtotal salen del catálogo actual."""
    order_lines = []
    for line in lines:
        product = products_by_id.get(int(line.get("product_id") or 0))
        cases = int(line.get("cases") or 0)
        if product is None or cases <= 0:
            continue
        units = cases * max(1, product.units_per_case)
        order_lines.append(
            OrderLine(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                cases=cases,
                units=units,
                subtotal=Decimal(units) * Decimal(product.unit_price),
                reason=str(line.get("reason") or ""),
            )
        )
    return order_lines


def order_total(order_lines: list[OrderLine]) -> Decimal:
    return sum((line.subtotal for line in order_lines), Decimal("0"))
