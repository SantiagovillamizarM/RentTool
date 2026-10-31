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
CONFIRM_RECEIPT = "receipt:confirm"
CANCEL_RECEIPT = "receipt:cancel"
CONFIRM_LOGOUT = "logout:confirm"
KEEP_SESSION = "logout:keep"


def draft_pattern(action: str) -> str:
    """Botones de un borrador: "<acción>:<id del borrador>". Sin id = botón de antes de este cambio."""
    return rf"^{action}(:\d+)?$"


def draft_id(data: str | None) -> int | None:
    """'sales:confirm:42' -> 42. Un botón sin id no corresponde a ningún borrador vigente."""
    parts = (data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _confirm_cancel(confirm_label: str, confirm: str, cancel: str, pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(confirm_label, callback_data=f"{confirm}:{pending_id}"),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"{cancel}:{pending_id}"),
            ]
        ]
    )


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


def confirm_sales_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Confirmar", CONFIRM_SALES, CANCEL_SALES, pending_id)


def confirm_stock_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Confirmar", CONFIRM_STOCK, CANCEL_STOCK, pending_id)


def confirm_receipt_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Llegó así", CONFIRM_RECEIPT, CANCEL_RECEIPT, pending_id)


def logout_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚪 Cerrar sesión", callback_data=CONFIRM_LOGOUT),
                InlineKeyboardButton("👍 Sigo aquí", callback_data=KEEP_SESSION),
            ]
        ]
    )


def order_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Enviar al distribuidor", SEND_ORDER, CANCEL_ORDER, pending_id)


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
