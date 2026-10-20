"""Validación del formulario de productos del panel. Funciones puras."""

from dataclasses import dataclass, field
from decimal import Decimal

from app.bot import texts


class FormError(ValueError):
    pass


def parse_price(raw: str) -> Decimal:
    """Precio en pesos: '4.500', '4500' o '$ 4.500' -> Decimal('4500'). Debe ser mayor que 0."""
    cleaned = (raw or "").replace("$", "").replace(".", "").replace(",", "").replace(" ", "").strip()
    if not cleaned.isdigit() or int(cleaned) <= 0:
        raise FormError("El precio debe ser un número mayor que 0, por ejemplo 4500.")
    return Decimal(int(cleaned))


def parse_available(raw: str) -> int | None:
    """Unidades disponibles. Vacío = no lleva la cuenta (sin límite)."""
    cleaned = (raw or "").replace(".", "").replace(" ", "").strip()
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise FormError("Las unidades disponibles deben ser un número entero (0 o más), o dejarse vacías.")
    return int(cleaned)


def parse_units_per_case(raw: str) -> int:
    cleaned = (raw or "").strip()
    if not cleaned.isdigit() or int(cleaned) < 1:
        raise FormError("Las unidades por caja deben ser un número entero, 1 o más.")
    return int(cleaned)


def parse_required(raw: str, label: str, max_length: int) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise FormError(f"Falta {label}.")
    if len(cleaned) > max_length:
        raise FormError(f"{label.capitalize()} es muy largo (máximo {max_length} caracteres).")
    return cleaned


@dataclass
class ProductChange:
    product_id: int
    unit_price: Decimal
    available_units: int | None
    active: bool
    notice: str | None  # aviso para los tenderos (precio nuevo o producto disponible), si aplica


@dataclass
class CatalogPlan:
    changes: list[ProductChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error_ids: set[int] = field(default_factory=set)


def plan_catalog_changes(products, form: dict[str, str]) -> CatalogPlan:
    """Valida todo el catálogo enviado de una vez y calcula solo lo que cambió.

    `form` trae price-<id>, available-<id> y active-<id> ("on" si está activo). Si hay
    algún error no se debe guardar nada: el distribuidor corrige y vuelve a enviar.
    Solo se avisa a los tenderos lo que les sirve: precio nuevo o producto que vuelve a estar.
    """
    plan = CatalogPlan()
    for product in products:
        pid = product.id
        try:
            price = parse_price(form.get(f"price-{pid}", ""))
            available = parse_available(form.get(f"available-{pid}", ""))
        except FormError as exc:
            plan.errors.append(f"{product.name}: {exc}")
            plan.error_ids.add(pid)
            continue
        active = form.get(f"active-{pid}") == "on"
        old_price = Decimal(product.unit_price)
        if (price, available, active) == (old_price, product.available_units, product.active):
            continue
        notice = None
        if active and not product.active:
            notice = texts.new_product(product.name, price, product.units_per_case)
        elif active and price != old_price:
            notice = texts.price_changed(product.name, old_price, price)
        plan.changes.append(ProductChange(pid, price, available, active, notice))
    return plan
