GEMINI_FAIL = "No te entendí, intenta de nuevo o escríbelo"
UNRECOGNIZED = "no reconocido"
RATE_LIMITED = "Espera un momentico. Máximo 20 mensajes por minuto."
PHOTO_TOO_BIG = "La foto pesa mucho. Máximo 10 MB."
VOICE_TOO_LONG = "El audio es muy largo. Máximo 2 minutos."
NEED_CONSENT = "Primero acepta el aviso de datos para poder usar el bot."
NEED_LINK = "Envíame el código de tu tienda para vincularte."
UNKNOWN_CODE = "Ese código no existe. Pídelo al distribuidor."
ALREADY_LINKED = "Esta tienda ya está vinculada. Escribe /ayuda."
STORE_BUSY = "Ese chat ya está vinculado a otra tienda."
NO_STORE = "Aún no estás vinculado. Escribe /start y el código de tu tienda."
NOTHING_PENDING = "No hay nada por confirmar."
CANCELLED = "Listo, no guardé nada."
SAVED_SALES = "Quedó registrado."
SAVED_STOCK = "Quedó el conteo."
EMPTY_SALES = "No encontré ventas en el mensaje. Ejemplo: vendí 2 coca litro y un pan."
EMPTY_STOCK = "No pude contar productos en la foto. Toma otra o escribe: coca litro 12."
NO_STOCK = "Aún no hay conteo. Mándame una foto del estante."
NO_ORDER = "No hay pedido para enviar. Revisa /stock o vuelve a intentar."
NO_SENT_ORDER = "No hay un pedido enviado para marcar como recibido."
ORDER_SENT = "Pedido enviado al distribuidor."
ORDER_RECEIVED = "Listo, registré la mercancía recibida."
HELP = (
    "Puedes decirme:\n"
    "• vendí 2 coca litro y un pan\n"
    "• una foto del estante\n"
    "• coca litro 12 (para corregir)\n"
    "• /stock  /alertas  /pedido  /recibido  /ayuda"
)
UNKNOWN = (
    "No te entendí. Prueba así:\n"
    "vendí 2 coca litro y un pan\n"
    "o manda una foto del estante.\n"
    "Escribe /ayuda."
)

CONSENT_NOTICE = (
    "TenderoBot trata datos de tu tienda (chat, ventas y stock) según la Ley 1581 de 2012. "
    "Solo el distribuidor de tu tienda los usa para el pedido. "
    "Si aceptas, pulsa Acepto. Si no, no podemos operar."
)
WELCOME = "Listo, {owner}. Ya puedes registrar ventas, stock y pedidos."
ASK_CODE = "Hola. Envíame el código de tu tienda (te lo da el distribuidor)."


def cop(amount) -> str:
    value = int(round(float(amount)))
    return f"${value:,}".replace(",", ".")


def sales_summary(lines: list[dict], names: dict[str, str]) -> str:
    if not lines:
        return EMPTY_SALES
    rows = ["¿Confirmas estas ventas?"]
    for line in lines:
        sku = line.get("sku")
        qty = int(line.get("quantity") or 0)
        label = names.get(sku, "") if sku else ""
        text = (line.get("product_text") or "").strip()
        if sku and label:
            rows.append(f"• {qty} {label}")
        elif text:
            rows.append(f"• {qty} {text} ({UNRECOGNIZED})")
        else:
            rows.append(f"• {qty} ({UNRECOGNIZED})")
    rows.append("Se guarda solo si confirmas.")
    return "\n".join(rows)


def stock_summary(lines: list[dict], names: dict[str, str]) -> str:
    if not lines:
        return EMPTY_STOCK
    rows = ["Es una estimación, revísala."]
    for line in lines:
        sku = line.get("sku")
        qty = int(line.get("quantity") or 0)
        if sku and sku in names:
            rows.append(f"• {names[sku]}: {qty}")
        else:
            rows.append(f"• {UNRECOGNIZED}: {qty}")
    rows.append("Confirma o corrige así: coca litro 12")
    return "\n".join(rows)


def stock_list(products: list) -> str:
    if not products:
        return NO_STOCK
    rows = ["Stock actual:"]
    for item in products:
        mark = "" if item.has_count else " (sin conteo)"
        rows.append(f"• {item.name}: {item.stock}{mark}")
    return "\n".join(rows)


def alerts_list(at_risk: list, stagnant: list) -> str:
    rows = []
    if at_risk:
        rows.append("En riesgo:")
        for item in at_risk:
            rows.append(f"• {item.name} — {item.reason}")
    if stagnant:
        rows.append("Estancados:")
        for item in stagnant:
            rows.append(f"• {item.name} — {item.reason}")
    if not rows:
        return "Sin alertas por ahora."
    return "\n".join(rows)


def order_summary(lines: list, total) -> str:
    if not lines:
        return NO_ORDER
    rows = ["Pedido sugerido:"]
    for line in lines:
        word = "caja" if line.cases == 1 else "cajas"
        rows.append(f"• {line.cases} {word} {line.name} — {line.reason}")
    rows.append(f"Total: {cop(total)} COP")
    return "\n".join(rows)
