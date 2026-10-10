from rapidfuzz import fuzz, process

from app.schemas import SaleLine, StockLine

MATCH_THRESHOLD = 80


def catalog_by_sku(products: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if sku:
            mapping[sku.casefold()] = product
    return mapping


def product_terms(product: dict) -> list[str]:
    terms: list[str] = []
    sku = str(product.get("sku") or "").strip()
    name = str(product.get("name") or "").strip()
    if sku:
        terms.append(sku)
    if name:
        terms.append(name)
    for alias in str(product.get("aliases") or "").split(","):
        cleaned = alias.strip()
        if cleaned:
            terms.append(cleaned)
    return terms


def best_product_match(query: str, products: list[dict]) -> dict | None:
    query = (query or "").strip()
    if not query or not products:
        return None

    best: dict | None = None
    best_score = -1.0
    tied = False
    for product in products:
        terms = product_terms(product)
        if not terms:
            continue
        result = process.extractOne(
            query,
            terms,
            scorer=fuzz.WRatio,
            score_cutoff=MATCH_THRESHOLD,
        )
        if result is None:
            continue
        _match, score, _index = result
        product_sku = str(product.get("sku") or "")
        best_sku = str(best.get("sku") or "") if best is not None else ""
        if score > best_score:
            best = product
            best_score = score
            tied = False
        elif score == best_score and product_sku != best_sku:
            tied = True
    if tied:
        return None
    return best


def resolve_sku(sku: str | None, query_text: str, products: list[dict]) -> str | None:
    by_sku = catalog_by_sku(products)
    cleaned_sku = (sku or "").strip()
    if cleaned_sku:
        found = by_sku.get(cleaned_sku.casefold())
        if found is not None:
            return str(found["sku"])

    query = (query_text or "").strip() or cleaned_sku
    match = best_product_match(query, products)
    if match is None:
        return None
    return str(match["sku"])


def match_sale_lines(lines: list[SaleLine], products: list[dict]) -> list[SaleLine]:
    matched: list[SaleLine] = []
    for line in lines:
        sku = resolve_sku(line.sku, line.product_text, products)
        matched.append(line.model_copy(update={"sku": sku}))
    return matched


def match_stock_lines(lines: list[StockLine], products: list[dict]) -> list[StockLine]:
    matched: list[StockLine] = []
    for line in lines:
        sku = resolve_sku(line.sku, "", products)
        matched.append(line.model_copy(update={"sku": sku}))
    return matched
