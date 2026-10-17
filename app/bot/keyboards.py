from telegram import InlineKeyboardButton, InlineKeyboardMarkup

ACCEPT_CONSENT = "consent:accept"
REJECT_CONSENT = "consent:reject"
CONFIRM_SALES = "sales:confirm"
CANCEL_SALES = "sales:cancel"
CONFIRM_STOCK = "stock:confirm"
CANCEL_STOCK = "stock:cancel"
SEND_ORDER = "order:send"


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


def send_order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Enviar al distribuidor", callback_data=SEND_ORDER)]]
    )
