SALES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": ["string", "null"]},
                    "product_text": {"type": "string"},
                    "quantity": {"type": "integer"},
                },
                "required": ["sku", "product_text", "quantity"],
            },
        }
    },
    "required": ["lines"],
}

STOCK_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": ["string", "null"]},
                    "quantity": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": ["sku", "quantity", "confidence"],
            },
        }
    },
    "required": ["lines"],
}


def format_catalog(products: list[dict]) -> str:
    if not products:
        return "(sin productos)"
    rows = []
    for product in products:
        sku = str(product.get("sku") or "").strip()
        name = str(product.get("name") or "").strip()
        aliases = str(product.get("aliases") or "").strip() or "-"
        rows.append(f"- sku={sku} | nombre={name} | alias={aliases}")
    return "\n".join(rows)


def sales_prompt(transcript: str, products: list[dict]) -> str:
    catalog = format_catalog(products)
    return (
        "Extrae ventas dichas por un tendero en Colombia.\n"
        "Devuelve solo JSON con esta forma: "
        '{"lines":[{"sku":string|null,"product_text":string,"quantity":integer}]}.\n'
        "Reglas:\n"
        "- quantity es la cantidad de unidades vendidas, siempre mayor que 0.\n"
        "- sku solo si coincide con un sku del catálogo. Si no estás seguro, sku=null.\n"
        "- product_text es el texto tal como lo dijo el tendero.\n"
        "- No inventes productos ni cantidades. No calcules cajas, totales ni precios.\n"
        "- Si no hay ventas claras, lines=[].\n"
        "- El tendero puede escribir con o sin la palabra vendí: "
        "'2 coca litro y un pan' también son ventas.\n"
        "- Ignora saludos, preguntas y pedidos de reposición.\n\n"
        f"Catálogo:\n{catalog}\n\n"
        f"Mensaje:\n{transcript.strip()}"
    )


def stock_prompt(products: list[dict], note: str = "") -> str:
    catalog = format_catalog(products)
    extra = f"\nNota del tendero:\n{note.strip()}\n" if note.strip() else ""
    return (
        "Cuenta unidades visibles en la foto del estante.\n"
        "Devuelve solo JSON con esta forma: "
        '{"lines":[{"sku":string|null,"quantity":integer,"confidence":number}]}.\n'
        "Reglas:\n"
        "- quantity es unidades visibles, 0 o más. No cuentes cajas cerradas como unidades sueltas.\n"
        "- sku solo si el producto coincide con el catálogo. Si no estás seguro, sku=null.\n"
        "- confidence va de 0 a 1. Si la foto está borrosa o tapada, baja confidence.\n"
        "- No inventes productos que no se vean. No calcules ventas, cobertura ni pedidos.\n"
        "- Si no puedes contar nada, lines=[].\n"
        f"{extra}\n"
        f"Catálogo:\n{catalog}"
    )


ORDER_CHANGES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": ["string", "null"]},
                    "product_text": {"type": "string"},
                    "action": {"type": "string", "enum": ["add", "remove", "set"]},
                    "quantity": {"type": "integer"},
                    "unit": {"type": "string", "enum": ["case", "unit"]},
                },
                "required": ["sku", "product_text", "action", "quantity", "unit"],
            },
        }
    },
    "required": ["changes"],
}


def order_changes_prompt(message: str, products: list[dict], current_order: str) -> str:
    catalog = format_catalog(products)
    return (
        "Un tendero en Colombia está revisando el pedido que le va a enviar a su distribuidor.\n"
        "Extrae los cambios que pide al pedido. Devuelve solo JSON con esta forma: "
        '{"changes":[{"sku":string|null,"product_text":string,'
        '"action":"add"|"remove"|"set","quantity":integer,"unit":"case"|"unit"}]}.\n'
        "Reglas:\n"
        "- add: agregar o poner más (\"ponle 2 cajas más de coca\", \"agrégame huevos\").\n"
        "- remove: quitar el producto del pedido (\"quita el arroz\"). quantity=0.\n"
        "- set: dejar una cantidad exacta (\"que sean 3 cajas de coca\").\n"
        "- unit=case si habla de cajas o no dice unidad; unit=unit si habla de unidades sueltas "
        "(\"6 cocas\", \"10 bolsas de leche\"). quantity es el número que dijo, sin convertir.\n"
        "- Si no dice cantidad al agregar, quantity=1.\n"
        "- sku solo si coincide con un sku del catálogo. Si no estás seguro, sku=null.\n"
        "- product_text es el producto tal como lo dijo el tendero.\n"
        "- No calcules cajas, totales ni precios.\n"
        "- Solo es un cambio si el tendero pide cambiar el pedido con palabras como agrega, ponle, "
        "pídeme, tráeme, quita, saca, no me traigas, que sean, súbele, bájale o cambia.\n"
        "- Si cuenta ventas (vendí, se fueron, salieron, se vendieron) o solo lista productos sin pedir "
        "un cambio, NO es un cambio: changes=[]. Vender un producto nunca significa quitarlo del pedido.\n"
        "- Saludos y preguntas: changes=[].\n\n"
        f"Pedido actual:\n{current_order}\n\n"
        f"Catálogo:\n{catalog}\n\n"
        f"Mensaje:\n{message.strip()}"
    )
