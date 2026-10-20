from app.inventory.engine import COLOMBIA_TZ

AI_FAIL = "No te entendí, intenta de nuevo o escríbelo"
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
EMPTY_SALES = (
    "No encontré productos vendidos en tu mensaje.\n"
    "Escríbeme lo que vendiste, por ejemplo: 2 coca litro y un pan"
)
EMPTY_STOCK = "No pude contar productos en la foto. Toma otra con más luz y más de cerca."
NO_STOCK = "Aún no hay conteo. Mándame una foto del estante."
NO_ORDER = "No hay pedido para enviar. Revisa /stock o vuelve a intentar."
NO_SENT_ORDER = "No hay un pedido enviado para marcar como recibido."
ORDER_CANCELLED = "Listo, no envié el pedido. Cuando quieras, escribe /pedido."
ORDER_RECEIVED = "Listo, registré la mercancía recibida."
HELP = (
    "Esto es lo que puedo hacer:\n\n"
    "🛒 Registrar ventas\n"
    "• Escríbeme o mándame un audio con lo que vendiste: 2 coca litro y un pan\n"
    "• No tienes que decir \"vendí\". Antes de guardar te pregunto.\n\n"
    "📷 Contar lo que tienes\n"
    "• Mándame una foto del estante y te digo cuántas unidades veo\n"
    "• Si un número está mal, escribe el producto y las unidades reales.\n"
    "  Ejemplo: si hay 8 Coca-Cola litro, escribe: coca litro 8\n\n"
    "Comandos\n"
    "/stock – lo que tienes en la tienda\n"
    "/alertas – lo que se te va a acabar y lo que no se vende\n"
    "/pedido – qué pedirle al distribuidor (puedes cambiarlo antes de enviarlo)\n"
    "/recibido – avisa que llegó el pedido\n"
    "/catalogo – lo que tiene tu proveedor y sus precios\n"
    "/ayuda – ver este mensaje"
)
UNKNOWN = (
    "No te entendí. Prueba así:\n"
    "2 coca litro y un pan\n"
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
    "Así me usas:\n\n"
    "🛒 Cada vez que vendas, escríbeme o mándame un audio con lo que vendiste. "
    "No tienes que decir \"vendí\", basta con: 2 coca litro y un pan\n\n"
    "📷 Para contar lo que tienes, mándame una foto del estante.\n\n"
    "📦 Escribe /pedido y te digo qué pedirle al distribuidor.\n\n"
    "Antes de guardar algo siempre te pregunto. Escribe /ayuda para ver más."
)
ASK_CODE = INTRO + "\n\n" + ASK_STORE
START_LINKED = INTRO + "\n\n" + QUICK_START


def start_needs_days(store_name: str) -> str:
    return INTRO + f"\n\nYa tienes vinculada la tienda {store_name}. Solo falta un paso."
WELCOME = "Listo, {owner}. Ya quedó vinculada tu tienda."
NEED_DAYS = "Antes de seguir, escoge los días en que quieres recibir la mercancía."
DAYS_EMPTY = "Escoge al menos un día."
WEEKDAYS_LOWER = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def join_days(days: list[int]) -> str:
    """[0, 2, 4] -> 'lunes, miércoles y viernes'"""
    names = [WEEKDAYS_LOWER[d] for d in sorted(days)]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " y " + names[-1]


def ask_days(route_days: list[int]) -> str:
    return (
        f"📅 Tu proveedor pasa por tu barrio los {join_days(route_days)}. "
        "¿Qué días quieres recibir la mercancía?\n\n"
        "Marca uno o varios y pulsa Listo."
    )


def days_saved(days: list[int]) -> str:
    return f"Listo. Te llevamos la mercancía los {join_days(days)}.\n\n" + QUICK_START


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
    rows = ["📷 Esto es lo que conté en la foto (es un cálculo aproximado):"]
    for line in lines:
        sku = line.get("sku")
        qty = int(line.get("quantity") or 0)
        units = "unidad" if qty == 1 else "unidades"
        if sku and sku in names:
            rows.append(f"• {names[sku]}: {qty} {units}")
        else:
            rows.append(f"• Producto que no reconocí: {qty} {units} (no se guarda)")
    rows.append("")
    rows.append("¿Algún número está mal? Escríbeme el producto y cuántas unidades hay de verdad.")
    rows.append("Ejemplo: si hay 8 Coca-Cola litro, escribe: coca litro 8")
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


def day_label(value) -> str:
    """date(2026, 10, 8) -> 'jueves 8/10'"""
    return f"{WEEKDAYS_LOWER[value.weekday()]} {value.day}/{value.month}"


def hour_label(hour: int) -> str:
    """18 -> '6 p. m.'"""
    suffix = "a. m." if hour < 12 else "p. m."
    return f"{(hour % 12) or 12} {suffix}"


def deadline_label(deadline) -> str:
    """Hora límite en hora Colombia: 'miércoles 7/10 a las 6 p. m.'"""
    local = deadline.astimezone(COLOMBIA_TZ)
    return f"{day_label(local.date())} a las {hour_label(local.hour)}"


ORDER_EDIT_HINT = (
    "¿Quieres cambiar algo? Escríbeme o mándame un audio, por ejemplo:\n"
    "\"quita el arroz\", \"ponle 2 cajas más de coca\" o \"agrega una cubeta de huevos\"."
)


def order_draft(
    lines: list,
    total,
    delivery,
    deadline,
    not_found: list[str] | None = None,
    availability_notes: list[str] | None = None,
) -> str:
    rows = []
    if lines:
        rows.append(f"📦 Pedido para el {day_label(delivery)}:")
        for line in lines:
            word = "caja" if line.cases == 1 else "cajas"
            rows.append(f"• {line.cases} {word} {line.name} — {line.reason}")
        rows.append(f"Total: {cop(total)} COP")
    else:
        rows.append(f"📦 Por ahora no necesitas pedir nada para el {day_label(delivery)}.")
    if not_found:
        rows.append("")
        rows.append("No encontré en el catálogo: " + ", ".join(not_found) + ".")
    if availability_notes:
        rows.append("")
        rows.append("Ajusté según lo que tiene tu proveedor: " + ", ".join(availability_notes) + ".")
    rows.append("")
    rows.append(ORDER_EDIT_HINT)
    rows.append("")
    rows.append(f"Tienes hasta el {deadline_label(deadline)} para enviarlo.")
    return "\n".join(rows)


def order_sent(delivery, missed_deadline: bool, availability_notes: list[str] | None = None) -> str:
    if missed_deadline:
        text = (
            "Ya había pasado la hora límite de esa entrega. "
            f"Envié el pedido para la siguiente: te llega el {day_label(delivery)}."
        )
    else:
        text = f"✅ Pedido enviado al distribuidor. Te llega el {day_label(delivery)}."
    if availability_notes:
        text += "\n\nAjusté según lo que tiene tu proveedor: " + ", ".join(availability_notes) + "."
    return text


def catalog_list(products: list) -> str:
    """Lista de /catalogo: precio por unidad y si está agotado donde el proveedor."""
    if not products:
        return "Tu proveedor aún no tiene productos."
    rows = ["🛒 Lo que tiene tu proveedor (precio por unidad):"]
    for product in products:
        # Agotado = no alcanza para una caja completa (igual que en el panel y en el pedido).
        sold_out = (
            " — agotado"
            if product.available_units is not None and product.available_units < max(1, product.units_per_case)
            else ""
        )
        rows.append(f"• {product.name}: {cop(product.unit_price)}{sold_out}")
    return "\n".join(rows)


def price_changed(name: str, old_price, new_price) -> str:
    return f"💲 Cambió el precio de {name}: antes {cop(old_price)}, ahora {cop(new_price)} la unidad."


def new_product(name: str, price, units_per_case: int) -> str:
    return (
        f"🆕 Tu proveedor tiene un producto nuevo: {name}, a {cop(price)} la unidad "
        f"(caja de {units_per_case}). Ya lo puedes pedir con /pedido."
    )


def order_reminder(delivery, deadline) -> str:
    return (
        f"⏰ El {day_label(delivery)} te llega mercancía.\n"
        f"Tienes hasta el {deadline_label(deadline)} para enviar tu pedido.\n\n"
        "Escribe /pedido y te digo qué pedir."
    )


def catalog_notice(notices: list[str]) -> str:
    """Un solo mensaje con todas las novedades del proveedor (no uno por producto)."""
    if len(notices) == 1:
        return notices[0]
    return "📣 Novedades de tu proveedor:\n\n" + "\n\n".join(notices)
