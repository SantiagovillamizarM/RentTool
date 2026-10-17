GEMINI_FAIL = "No te entendí, intenta de nuevo o escríbelo"
UNRECOGNIZED = "no reconocido"
RATE_LIMITED = "Espera un momentico. Máximo 20 mensajes por minuto."
PHOTO_TOO_BIG = "La foto pesa mucho. Máximo 10 MB."
VOICE_TOO_LONG = "El audio es muy largo. Máximo 2 minutos."
NEED_CONSENT = "Para seguir, primero responde si aceptas el uso de tus datos."
ASK_STORE = "Antes de todo, ¿de qué tienda nos estás hablando? (Proporciona el código de tu tienda)"
NEED_LINK = ASK_STORE
UNKNOWN_CODE = "Código incorrecto o no registrado."
ALREADY_LINKED = "Esa tienda ya está vinculada a otro chat. Pídele ayuda a tu distribuidor."
STORE_BUSY = "Ese chat ya está vinculado a otra tienda."
NO_STORE = ASK_STORE
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
    "Esto es lo que puedo hacer:\n\n"
    "Registrar ventas\n"
    "• Escribe o manda un audio: vendí 2 coca litro y un pan\n\n"
    "Contar el estante\n"
    "• Manda una foto del estante\n"
    "• Para corregir el conteo escribe: coca litro 12\n\n"
    "Comandos\n"
    "/stock – lo que tienes en la tienda\n"
    "/alertas – lo que se te va a acabar y lo que no se vende\n"
    "/pedido – qué pedirle al distribuidor\n"
    "/recibido – avisa que llegó el pedido\n"
    "/ayuda – ver este mensaje"
)
UNKNOWN = (
    "No te entendí. Prueba así:\n"
    "vendí 2 coca litro y un pan\n"
    "o manda una foto del estante.\n"
    "Escribe /ayuda."
)

CONSENT_NOTICE = (
    "📄 Antes de empezar necesitamos tu permiso.\n\n"
    "En Colombia, la Ley 1581 de 2012 protege los datos personales: nadie puede usarlos "
    "sin tu autorización, y tú puedes pedir en cualquier momento conocerlos, corregirlos o borrarlos.\n\n"
    "TenderoBot guardará el chat, las ventas y el stock de tu tienda. "
    "Esta información solo se usará para preparar tus futuros pedidos de productos con tu distribuidor.\n\n"
    "¿Aceptas?"
)
CONSENT_REJECTED = (
    "Entendido, no guardamos nada. Sin tu permiso no podemos ayudarte con la tienda.\n\n"
    "Si cambias de opinión, envíame otra vez el código de tu tienda."
)
CONSENT_ALREADY = "Ya habías aceptado antes. Pon /ayuda para ver lo que puedo hacer."
INTRO = (
    "👋 Bienvenido a TenderoBot, el bot que te ayuda a llevar el inventario "
    "de tu tienda y a hacer el pedido al distribuidor sin perder tiempo."
)
QUICK_START = (
    "Lo principal:\n"
    "• Escribe o manda un audio con lo que vendiste: vendí 2 coca litro y un pan\n"
    "• Manda una foto del estante para contar lo que tienes\n"
    "• /pedido para saber qué pedir\n\n"
    "Pon /ayuda para ver más comandos."
)
ASK_CODE = INTRO + "\n\n" + ASK_STORE
START_LINKED = INTRO + "\n\n" + QUICK_START
WELCOME = "Listo, {owner}. Ya quedó vinculada tu tienda.\n\n" + QUICK_START


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
