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
        "- Ignora saludos y pedidos de reposición. Solo ventas ya hechas.\n\n"
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
