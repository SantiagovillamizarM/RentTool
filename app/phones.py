"""Números de celular de los tenderos: un solo formato para el panel y para Telegram."""

import re

COLOMBIA_PREFIX = "57"


def normalize_phone(raw: str | None) -> str | None:
    """'+57 300 123 4567', '3001234567' o '573001234567' -> '573001234567'. Inválido -> None.

    Telegram manda el número con indicativo y a veces sin '+'; en el panel se escribe con o sin él.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits.startswith("3"):
        digits = COLOMBIA_PREFIX + digits
    if not 11 <= len(digits) <= 15:
        return None
    return digits


def format_phone(phone: str | None) -> str:
    """'573001234567' -> '+57 300 123 4567' (para mostrar en el panel)."""
    if not phone:
        return ""
    if phone.startswith(COLOMBIA_PREFIX) and len(phone) == 12:
        local = phone[2:]
        return f"+57 {local[:3]} {local[3:6]} {local[6:]}"
    return f"+{phone}"
