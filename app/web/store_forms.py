"""Validación de los datos de una tienda: solicitud de la página pública y formulario del panel. Funciones puras."""

import re
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation

from app.onboarding import DecisionError, parse_approval
from app.phones import normalize_phone
from app.web.catalog import FormError, parse_required

PAYMENT_METHODS = {"contado": "De contado", "credito": "A crédito"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_phone(raw: str) -> str:
    phone = normalize_phone(raw)
    if phone is None:
        raise FormError("El celular no es válido. Escríbelo así: 300 123 4567.")
    return phone


def parse_document(raw: str) -> str:
    """Cédula o NIT: '1.098.765.432' o '900123456-7' -> solo dígitos y el guion del NIT."""
    cleaned = re.sub(r"[^\d-]", "", raw or "").strip("-")
    digits = cleaned.replace("-", "")
    if not 5 <= len(digits) <= 15:
        raise FormError("La cédula o el NIT no es válido.")
    return cleaned


def parse_email(raw: str) -> str:
    cleaned = (raw or "").strip().lower()
    if not _EMAIL.match(cleaned) or len(cleaned) > 255:
        raise FormError("El correo no es válido, por ejemplo: tienda@gmail.com.")
    return cleaned


def parse_payment(raw: str) -> str:
    if raw not in PAYMENT_METHODS:
        raise FormError("Escoge cómo vas a pagar los pedidos.")
    return raw


def parse_coordinate(raw: str, limit: int) -> Decimal | None:
    """Ubicación opcional que manda el navegador. Vacía o inválida = sin ubicación."""
    try:
        value = Decimal((raw or "").strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) > limit:
        return None
    return value.quantize(Decimal("0.000001"))


def _collect(errors: list[str], parse: Callable, *args):
    try:
        return parse(*args)
    except (FormError, DecisionError) as exc:
        errors.append(str(exc))
        return None


def _optional(errors: list[str], parse: Callable, raw: str, *args):
    return _collect(errors, parse, raw, *args) if (raw or "").strip() else None


def parse_store_request(form: Mapping[str, str]) -> tuple[dict, list[str]]:
    """Solicitud de la página pública: todo es obligatorio menos la ubicación."""
    errors: list[str] = []

    def get(key: str) -> str:
        return form.get(key, "")

    data = {
        "store_name": _collect(errors, parse_required, get("store_name"), "el nombre de la tienda", 160),
        "owner_name": _collect(errors, parse_required, get("owner_name"), "tu nombre", 160),
        "document_id": _collect(errors, parse_document, get("document_id")),
        "phone": _collect(errors, parse_phone, get("phone")),
        "email": _collect(errors, parse_email, get("email")),
        "city": _collect(errors, parse_required, get("city"), "la ciudad", 80),
        "neighborhood": _collect(errors, parse_required, get("neighborhood"), "el barrio", 120),
        "address": _collect(errors, parse_required, get("address"), "la dirección", 200),
        "receiving_hours": _collect(errors, parse_required, get("receiving_hours"), "el horario para recibir", 80),
        "payment_method": _collect(errors, parse_payment, get("payment_method")),
        "latitude": parse_coordinate(get("latitude"), 90),
        "longitude": parse_coordinate(get("longitude"), 180),
    }
    if data["latitude"] is None or data["longitude"] is None:
        data["latitude"] = data["longitude"] = None
    if get("consent") != "on":
        errors.append("Para enviar la solicitud debes aceptar el tratamiento de tus datos.")
    return data, errors


def parse_store_form(form: Mapping[str, str], route_days: list[str], with_phone: bool) -> tuple[dict, list[str]]:
    """Tienda creada o editada en el panel: nombre, dueño y días de ruta obligatorios; lo demás opcional."""
    errors: list[str] = []

    def get(key: str) -> str:
        return form.get(key, "")

    approval = _collect(errors, parse_approval, route_days, get("lead_time_days"))
    data = {
        "name": _collect(errors, parse_required, get("name"), "el nombre de la tienda", 160),
        "owner_name": _collect(errors, parse_required, get("owner_name"), "el nombre del dueño", 160),
        "document_id": _optional(errors, parse_document, get("document_id")),
        "email": _optional(errors, parse_email, get("email")),
        "city": _optional(errors, parse_required, get("city"), "la ciudad", 80),
        "neighborhood": _optional(errors, parse_required, get("neighborhood"), "el barrio", 120),
        "address": _optional(errors, parse_required, get("address"), "la dirección", 200),
        "receiving_hours": _optional(errors, parse_required, get("receiving_hours"), "el horario", 80),
        "payment_method": _optional(errors, parse_payment, get("payment_method")),
        "route_days": approval.route_days if approval else [],
        "lead_time_days": approval.lead_time_days if approval else 1,
    }
    if with_phone:
        data["owner_phone"] = _optional(errors, parse_phone, get("phone"))
    return data, errors
