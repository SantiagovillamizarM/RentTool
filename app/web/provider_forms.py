"""Validación de los datos de un proveedor: solicitud de la página pública y edición del administrador. Funciones puras."""

from collections.abc import Mapping

from app.providers import CATEGORIES, ProviderError, clean_nit
from app.web.catalog import FormError, parse_required
from app.web.store_forms import _collect, _optional, parse_document, parse_email, parse_phone


def parse_nit(raw: str) -> str:
    try:
        return clean_nit(raw)
    except ProviderError as exc:
        raise FormError(str(exc)) from exc


def parse_registration(raw: str) -> str:
    """Matrícula mercantil de la Cámara de Comercio: solo números."""
    cleaned = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not 4 <= len(cleaned) <= 20:
        raise FormError("Escribe el número de matrícula mercantil de la Cámara de Comercio.")
    return cleaned


def parse_count(raw: str, label: str, maximum: int) -> int:
    cleaned = (raw or "").replace(".", "").strip()
    if not cleaned.isdigit() or int(cleaned) > maximum:
        raise FormError(f"Escribe {label} como un número entero.")
    return int(cleaned)


def parse_categories(values: list[str]) -> str:
    chosen = [key for key in CATEGORIES if key in values]
    if not chosen:
        raise FormError("Marca al menos una categoría de lo que vendes.")
    return ",".join(chosen)


def parse_website(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned.startswith(("http://", "https://")) or " " in cleaned or len(cleaned) > 255:
        raise FormError("La página web debe empezar por https://")
    return cleaned


def parse_provider_request(form: Mapping[str, str], categories: list[str]) -> tuple[dict, list[str]]:
    """Solicitud de la página pública: datos reales de la empresa. Todo obligatorio menos la página web."""
    errors: list[str] = []

    def get(key: str) -> str:
        return form.get(key, "")

    data = {
        "legal_name": _collect(errors, parse_required, get("legal_name"), "la razón social", 200),
        "trade_name": _collect(errors, parse_required, get("trade_name"), "el nombre comercial", 160),
        "nit": _collect(errors, parse_nit, get("nit")),
        "chamber_registration": _collect(errors, parse_registration, get("chamber_registration")),
        "legal_rep_name": _collect(errors, parse_required, get("legal_rep_name"), "el nombre del representante legal", 160),
        "legal_rep_document": _collect(errors, parse_document, get("legal_rep_document")),
        "contact_name": _collect(errors, parse_required, get("contact_name"), "el nombre de la persona de contacto", 160),
        "contact_role": _collect(errors, parse_required, get("contact_role"), "el cargo de la persona de contacto", 80),
        "contact_phone": _collect(errors, parse_phone, get("contact_phone")),
        "city": _collect(errors, parse_required, get("city"), "la ciudad de la sede", 80),
        "address": _collect(errors, parse_required, get("address"), "la dirección de la sede", 200),
        "coverage": _collect(errors, parse_required, get("coverage"), "las ciudades o municipios donde repartes", 500),
        "categories": _collect(errors, parse_categories, categories),
        "vehicles": _collect(errors, parse_count, get("vehicles"), "el número de vehículos de reparto", 10000),
        "stores_estimate": _collect(errors, parse_count, get("stores_estimate"), "el número de tiendas que atiendes", 1000000),
        "website": _optional(errors, parse_website, get("website")),
    }
    if get("documents") != "on":
        errors.append("Confirma que tienes el RUT y el certificado de Cámara de Comercio vigentes.")
    if get("consent") != "on":
        errors.append("Para enviar la solicitud debes aceptar el tratamiento de los datos.")
    return data, errors


def parse_provider_edit(form: Mapping[str, str], categories: list[str]) -> tuple[dict, list[str]]:
    """Datos del proveedor que edita el administrador. Nombre obligatorio; lo demás puede quedar vacío."""
    errors: list[str] = []

    def get(key: str) -> str:
        return form.get(key, "")

    data = {
        "name": _collect(errors, parse_required, get("name"), "el nombre comercial", 160),
        "legal_name": _optional(errors, parse_required, get("legal_name"), "la razón social", 200),
        "nit": _optional(errors, parse_nit, get("nit")),
        "contact_name": _optional(errors, parse_required, get("contact_name"), "el contacto", 160),
        "contact_phone": _optional(errors, parse_phone, get("contact_phone")),
        "contact_email": _optional(errors, parse_email, get("contact_email")),
        "city": _optional(errors, parse_required, get("city"), "la ciudad", 80),
        "address": _optional(errors, parse_required, get("address"), "la dirección", 200),
        "coverage": _optional(errors, parse_required, get("coverage"), "la cobertura", 500),
        "categories": ",".join(key for key in CATEGORIES if key in categories) or None,
    }
    return data, errors
