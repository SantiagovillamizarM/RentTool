"""Horario en que una tienda puede recibir la mercancía: una hora de inicio y una de final. Funciones puras.

Se guarda como "HH:MM-HH:MM" ("07:30-17:00"), siempre dentro del horario de reparto: los camiones
no pasan de noche ni a la hora del almuerzo. Se puede terminar a las 12 m. o empezar a la 1 p. m.;
si el horario pasa por encima del almuerzo (7 a. m. a 5 p. m.) significa "todo ese tiempo, menos el almuerzo".
"""

# Horario de reparto de los camiones (hora Colombia). Cambiar aquí si el distribuidor reparte en otro.
DELIVERY_OPEN = 6 * 60
DELIVERY_CLOSE = 20 * 60
LUNCH_START = 12 * 60
LUNCH_END = 13 * 60

ASK = "Escribe desde qué hora y hasta qué hora puedes recibir la mercancía."


class HoursError(ValueError):
    pass


def time_label(minutes: int) -> str:
    """420 -> '7:00 a. m.'; 720 -> '12:00 m.'; 900 -> '3:00 p. m.'"""
    hour, minute = divmod(minutes, 60)
    if hour == 12 and minute == 0:
        return "12:00 m."
    suffix = "a. m." if hour < 12 else "p. m."
    return f"{(hour % 12) or 12}:{minute:02d} {suffix}"


def _short(minutes: int) -> str:
    """Sin ':00': '6 a. m.', '12 m.', '8 p. m.'"""
    return time_label(minutes).replace(":00", "")


def window_label() -> str:
    return f"{_short(DELIVERY_OPEN)} a {_short(DELIVERY_CLOSE)}"


def lunch_label() -> str:
    return f"{_short(LUNCH_START)} a {_short(LUNCH_END)}"


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_time(raw: str) -> int:
    """'07:30' (lo que manda <input type="time">) o '7:30' -> minutos desde la medianoche."""
    hour, sep, minute = (raw or "").strip().partition(":")
    if not sep or not hour.isdigit() or not minute[:2].isdigit():
        raise HoursError(ASK)
    hour_value, minute_value = int(hour), int(minute[:2])
    if hour_value > 23 or minute_value > 59:
        raise HoursError(ASK)
    return hour_value * 60 + minute_value


def clean_hours(start_raw: str, end_raw: str) -> str:
    """Valida el horario escrito y lo deja como 'HH:MM-HH:MM' para guardarlo."""
    start, end = parse_time(start_raw), parse_time(end_raw)
    if end <= start:
        raise HoursError("La hora final debe ser después de la inicial.")
    if start < DELIVERY_OPEN or end > DELIVERY_CLOSE:
        # window_label() ya termina en "m." (de "p. m."): sin punto extra.
        raise HoursError(f"Los camiones reparten de {window_label()} Escribe horas dentro de ese horario.")
    if LUNCH_START <= start < LUNCH_END or LUNCH_START < end <= LUNCH_END:
        raise HoursError(f"De {lunch_label()} es la hora del almuerzo: no hay entregas. Escribe otra hora.")
    return f"{hhmm(start)}-{hhmm(end)}"


def parse_range(value: str | None) -> tuple[int, int] | None:
    """'07:30-17:00' -> (450, 1020). Otro texto -> None."""
    start_raw, sep, end_raw = (value or "").partition("-")
    if not sep:
        return None
    try:
        return parse_time(start_raw), parse_time(end_raw)
    except HoursError:
        return None


def range_parts(value: str | None) -> tuple[str, str]:
    """Para llenar los campos Desde / Hasta: ('07:30', '17:00') o ('', '')."""
    parsed = parse_range(value)
    return (hhmm(parsed[0]), hhmm(parsed[1])) if parsed else ("", "")


def hours_label(value: str | None) -> str:
    """'07:00-17:00' -> 'De 7:00 a. m. a 5:00 p. m., menos el almuerzo (12 m. a 1 p. m.)'"""
    parsed = parse_range(value)
    if parsed is None:
        return value or ""  # dato viejo escrito a mano: tal cual
    start, end = parsed
    text = f"De {time_label(start)} a {time_label(end)}"
    if start < LUNCH_START and end > LUNCH_END:
        text += f", menos el almuerzo ({lunch_label()})"
    return text


def _percent(minutes: int) -> float:
    return round(100 * (minutes - DELIVERY_OPEN) / (DELIVERY_CLOSE - DELIVERY_OPEN), 2)


def segment(value: str | None) -> tuple[float, float] | None:
    """Para la minibarra del panel: (inicio %, ancho %) dentro del horario de reparto."""
    parsed = parse_range(value)
    if parsed is None:
        return None
    start, end = (min(max(t, DELIVERY_OPEN), DELIVERY_CLOSE) for t in parsed)
    return _percent(start), round(_percent(end) - _percent(start), 2)


def lunch_segment() -> tuple[float, float]:
    """La hora del almuerzo en la minibarra (se dibuja como un corte)."""
    return _percent(LUNCH_START), round(_percent(LUNCH_END) - _percent(LUNCH_START), 2)
