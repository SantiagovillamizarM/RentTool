import math
from datetime import date, timedelta


def cop(value) -> str:
    amount = int(round(float(value)))
    grouped = f"{amount:,}".replace(",", ".")
    return f"$ {grouped}"


def coverage(days) -> str:
    if math.isinf(float(days)):
        return "Sin ventas"
    whole = math.floor(float(days))
    if whole == 1:
        return "1 día"
    return f"{whole} días"


def qty(value) -> str:
    """Cantidad con un decimal y coma decimal: 2.5 -> "2,5", 6.0 -> "6"."""
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}".replace(".", ",")


def daily_series(sales_by_date: dict[date, int], end: date, days: int = 28) -> list[tuple[date, int]]:
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=offset), int(sales_by_date.get(start + timedelta(days=offset), 0))) for offset in range(days)]
