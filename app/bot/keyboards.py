from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

ACCEPT_CONSENT = "consent:accept"
REJECT_CONSENT = "consent:reject"
CONFIRM_SALES = "sales:confirm"
CANCEL_SALES = "sales:cancel"
CONFIRM_STOCK = "stock:confirm"
CANCEL_STOCK = "stock:cancel"
SEND_ORDER = "order:send"
CANCEL_ORDER = "order:cancel"
CONFIRM_LOGOUT = "logout:confirm"
KEEP_SESSION = "logout:keep"


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    """Botón de Telegram que manda el número verificado de la cuenta (no se puede escribir otro)."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Compartir mi número", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Acepto", callback_data=ACCEPT_CONSENT),
                InlineKeyboardButton("❌ No acepto", callback_data=REJECT_CONSENT),
            ]
        ]
    )


def confirm_sales_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmar", callback_data=CONFIRM_SALES),
                InlineKeyboardButton("❌ Cancelar", callback_data=CANCEL_SALES),
            ]
        ]
    )


def confirm_stock_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmar", callback_data=CONFIRM_STOCK),
                InlineKeyboardButton("❌ Cancelar", callback_data=CANCEL_STOCK),
            ]
        ]
    )


def logout_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚪 Cerrar sesión", callback_data=CONFIRM_LOGOUT),
                InlineKeyboardButton("👍 Sigo aquí", callback_data=KEEP_SESSION),
            ]
        ]
    )


def order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Enviar al distribuidor", callback_data=SEND_ORDER),
                InlineKeyboardButton("❌ Cancelar", callback_data=CANCEL_ORDER),
            ]
        ]
    )


# Días de entrega: la selección viaja en el callback ("days:<días marcados>:<acción>"),
# así no hay estado a medio guardar. Acción = un día (0-6) para marcar/desmarcar u "ok".
DAYS_PREFIX = "days"
DAYS_PATTERN = r"^days:[0-6]*:([0-6]|ok)$"
WEEKDAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def days_keyboard(route_days: list[int], selected: list[int]) -> InlineKeyboardMarkup:
    marked = "".join(str(d) for d in sorted(selected))
    rows = [
        [
            InlineKeyboardButton(
                f"{'✅' if day in selected else '⬜'} {WEEKDAY_NAMES[day]}",
                callback_data=f"{DAYS_PREFIX}:{marked}:{day}",
            )
        ]
        for day in route_days
    ]
    rows.append([InlineKeyboardButton("Listo", callback_data=f"{DAYS_PREFIX}:{marked}:ok")])
    return InlineKeyboardMarkup(rows)


def parse_days_callback(data: str) -> tuple[list[int], str]:
    """'days:04:2' -> ([0, 4], '2')"""
    _prefix, marked, action = data.split(":")
    return sorted({int(d) for d in marked}), action
