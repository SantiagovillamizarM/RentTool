import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.ai.grok_client import (
    ExtractError,
    extract_order_changes,
    extract_sales,
    extract_stock,
    transcribe_audio,
)
from app.ai.matching import best_product_match, match_order_changes, match_sale_lines, match_stock_lines
from app.bot import keyboards, texts
from app.db import SessionLocal
from app.inventory.engine import delivery_schedule, load_store_analysis, parse_visit_weekdays
from app.inventory.orders import apply_order_changes, cap_to_available, order_total, to_order_lines
from app.models import (
    PendingAction,
    Product,
    Receipt,
    Sale,
    StockCount,
    Store,
    SuggestedOrder,
    SuggestedOrderItem,
)
from app.schemas import SaleLine, StockLine

RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VOICE_SECONDS = 120
PENDING_TTL = timedelta(minutes=15)
# El borrador del pedido dura más: el tendero puede ir ajustándolo durante el día.
ORDER_PENDING_TTL = timedelta(hours=6)

_chat_hits: dict[int, deque[float]] = defaultdict(deque)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except ApplicationHandlerStop:
            # Detener la cadena de handlers no es un error: hay que guardar lo hecho.
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    if chat is None:
        return None
    return chat.id


def _is_rate_limited(chat_id: int) -> bool:
    now = monotonic()
    hits = _chat_hits[chat_id]
    while hits and now - hits[0] >= RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_MAX:
        return True
    hits.append(now)
    return False


async def _reply(update: Update, text: str, reply_markup=None) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(text, reply_markup=reply_markup)


async def check_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    if not _is_rate_limited(chat_id):
        return
    query = update.callback_query
    if query is not None:
        await query.answer(texts.RATE_LIMITED, show_alert=True)
    else:
        await _reply(update, texts.RATE_LIMITED)
    raise ApplicationHandlerStop


async def require_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mientras el chat no tenga tienda, solo se aceptan /start y el código (texto)."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        if await get_store_by_chat(session, chat_id) is not None:
            return
    message = update.message
    if message is not None and message.text:
        is_command = message.text.startswith("/")
        if not is_command or message.text.split()[0].split("@")[0].lower() == "/start":
            return
    query = update.callback_query
    if query is not None:
        await query.answer()
    await _reply(update, texts.ASK_STORE)
    raise ApplicationHandlerStop


async def get_store_by_chat(session: AsyncSession, chat_id: int) -> Store | None:
    return await session.scalar(select(Store).where(Store.telegram_chat_id == chat_id))


async def require_ready_store(update: Update, session: AsyncSession) -> Store | None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return None
    store = await get_store_by_chat(session, chat_id)
    if store is None:
        await _reply(update, texts.NO_STORE)
        return None
    if store.consent_at is None:
        await _reply(update, texts.NEED_CONSENT, reply_markup=keyboards.consent_keyboard())
        return None
    if store.delivery_days_set_at is None:
        await _reply(update, texts.NEED_DAYS)
        await ask_days(update, store)
        return None
    return store


def route_days(store: Store) -> list[int]:
    """Días en que el proveedor pasa por la zona. Sin configurar: toda la semana."""
    return parse_visit_weekdays(store.route_weekdays) or list(range(7))


async def ask_days(update: Update, store: Store) -> None:
    days = route_days(store)
    await _reply(update, texts.ask_days(days), reply_markup=keyboards.days_keyboard(days, []))


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _reply(update, texts.ASK_CODE)
            return
        # Siempre se saluda primero y luego se retoma el paso que falte.
        if store.consent_at is None:
            await _reply(update, texts.INTRO)
            await _reply(update, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
            return
        if store.delivery_days_set_at is None:
            await _reply(update, texts.start_needs_days(store.name))
            await ask_days(update, store)
            return
        await _reply(update, texts.START_LINKED)


async def handle_link_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    message = update.effective_message
    if chat_id is None or message is None:
        return
    code = (message.text or "").strip()
    if not code:
        return
    async with session_scope() as session:
        existing = await get_store_by_chat(session, chat_id)
        if existing is not None:
            if existing.consent_at is None:
                await _reply(update, texts.NEED_CONSENT, reply_markup=keyboards.consent_keyboard())
                raise ApplicationHandlerStop
            return
        store = await session.scalar(
            select(Store).where(func.lower(Store.link_code) == code.casefold())
        )
        if store is None:
            await _reply(update, texts.UNKNOWN_CODE)
            raise ApplicationHandlerStop
        if store.telegram_chat_id is not None:
            await _reply(update, texts.ALREADY_LINKED)
            raise ApplicationHandlerStop
        store.telegram_chat_id = chat_id
        await _reply(update, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
        raise ApplicationHandlerStop


async def handle_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _reply(update, texts.NEED_LINK)
            return
        if store.consent_at is None:
            store.consent_at = datetime.now(UTC)
        await _reply(update, texts.WELCOME.format(owner=store.owner_name))
        if store.delivery_days_set_at is None:
            await ask_days(update, store)
    await _remove_buttons(update)


async def handle_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Marca/desmarca días de entrega y, con "Listo", los guarda (solo la primera vez)."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None or not query.data:
        return
    selected, action = keyboards.parse_days_callback(query.data)
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None or store.consent_at is None:
            await query.answer()
            await _reply(update, texts.NEED_LINK)
            return
        if store.delivery_days_set_at is not None:
            # Botón viejo: los días ya quedaron escogidos.
            await query.answer()
            await _remove_buttons(update)
            return
        allowed = route_days(store)
        selected = [d for d in selected if d in allowed]
        if action != "ok":
            day = int(action)
            selected = sorted(set(selected) ^ {day}) if day in allowed else selected
            await query.answer()
            try:
                await query.edit_message_reply_markup(reply_markup=keyboards.days_keyboard(allowed, selected))
            except Exception:
                logger.info("No se pudieron actualizar los botones de días")
            return
        if not selected:
            await query.answer(texts.DAYS_EMPTY, show_alert=True)
            return
        await query.answer()
        store.visit_weekdays = ",".join(str(d) for d in selected)
        store.delivery_days_set_at = datetime.now(UTC)
    await _remove_buttons(update)
    await _reply(update, texts.days_saved(selected))


async def handle_reject_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """No acepta: se desvincula el chat y vuelve al paso del código."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _reply(update, texts.NEED_LINK)
            return
        if store.consent_at is not None:
            # Botón viejo de un aviso ya aceptado: no se desvincula por accidente.
            await _reply(update, texts.CONSENT_ALREADY)
            return
        store.telegram_chat_id = None
    await _remove_buttons(update)
    await _reply(update, texts.CONSENT_REJECTED)


async def _remove_buttons(update: Update) -> None:
    """Quita los botones del aviso para que no se pulsen dos veces."""
    query = update.callback_query
    if query is None:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.info("No se pudieron quitar los botones del aviso")


KIND_SALES = "sales"
KIND_STOCK = "stock"


async def load_active_products(session: AsyncSession, distributor_id: int) -> list[Product]:
    result = await session.scalars(
        select(Product).where(Product.distributor_id == distributor_id, Product.active.is_(True))
    )
    return list(result.all())


def products_as_catalog(products: list[Product]) -> list[dict]:
    return [
        {
            "sku": product.sku,
            "name": product.name,
            "aliases": product.aliases,
            "presentation": product.presentation,
        }
        for product in products
    ]


def names_by_sku(products: list[Product]) -> dict[str, str]:
    return {product.sku: product.name for product in products}


def products_by_sku(products: list[Product]) -> dict[str, Product]:
    return {product.sku: product for product in products}


def parse_stock_correction(text: str) -> tuple[str, int] | None:
    parts = text.strip().split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    name = " ".join(parts[:-1]).strip()
    if not name:
        return None
    return name, int(parts[-1])


def apply_stock_correction(lines: list[dict], sku: str, quantity: int) -> list[dict]:
    updated = False
    result: list[dict] = []
    for line in lines:
        if line.get("sku") == sku:
            result.append({**line, "sku": sku, "quantity": quantity})
            updated = True
        else:
            result.append(line)
    if not updated:
        result.append({"sku": sku, "quantity": quantity, "confidence": 1.0})
    return result


async def get_pending(session: AsyncSession, store_id: int, kind: str) -> PendingAction | None:
    now = datetime.now(UTC)
    return await session.scalar(
        select(PendingAction)
        .where(
            PendingAction.store_id == store_id,
            PendingAction.kind == kind,
            PendingAction.expires_at > now,
        )
        .order_by(PendingAction.id.desc())
    )


async def replace_pending(
    session: AsyncSession, store_id: int, kind: str, payload: dict, ttl: timedelta = PENDING_TTL
) -> None:
    """Un borrador por tipo: el nuevo reemplaza al anterior del mismo tipo.

    Así registrar una venta no borra el pedido que el tendero está armando.
    """
    rows = (
        await session.scalars(
            select(PendingAction).where(PendingAction.store_id == store_id, PendingAction.kind == kind)
        )
    ).all()
    for row in rows:
        await session.delete(row)
    session.add(
        PendingAction(
            store_id=store_id,
            kind=kind,
            payload=payload,
            expires_at=datetime.now(UTC) + ttl,
        )
    )


async def _download_file(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def _answer_callback(update: Update) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()


async def save_and_show_sales(
    update: Update,
    store_id: int,
    lines: list[SaleLine],
    source: str,
    names: dict[str, str],
) -> None:
    payload_lines = [line.model_dump() for line in lines]
    summary = texts.sales_summary(payload_lines, names)
    if not payload_lines:
        await _reply(update, summary)
        return
    async with session_scope() as session:
        await replace_pending(
            session,
            store_id,
            KIND_SALES,
            {"source": source, "lines": payload_lines},
        )
    await _reply(update, summary, reply_markup=keyboards.confirm_sales_keyboard())


async def save_and_show_stock(
    update: Update,
    store_id: int,
    lines: list[StockLine],
    names: dict[str, str],
) -> None:
    payload_lines = [line.model_dump() for line in lines]
    summary = texts.stock_summary(payload_lines, names)
    if not payload_lines:
        await _reply(update, summary)
        return
    async with session_scope() as session:
        await replace_pending(
            session,
            store_id,
            KIND_STOCK,
            {"source": "photo", "lines": payload_lines},
        )
    await _reply(update, summary, reply_markup=keyboards.confirm_stock_keyboard())


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.voice is None:
        return
    voice = message.voice
    if voice.duration > MAX_VOICE_SECONDS:
        await _reply(update, texts.VOICE_TOO_LONG)
        return
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
    audio_bytes = await _download_file(context, voice.file_id)
    try:
        transcript = await transcribe_audio(audio_bytes, voice.mime_type or "audio/ogg")
    except Exception:
        logger.warning("No se pudo transcribir el audio")
        transcript = ""
    if not transcript:
        await _reply(update, texts.AI_FAIL)
        return
    await route_text(update, transcript, "voice")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return
    photo = message.photo[-1]
    if photo.file_size is not None and photo.file_size > MAX_PHOTO_BYTES:
        await _reply(update, texts.PHOTO_TOO_BIG)
        return
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        store_id = store.id
        products = await load_active_products(session, store.distributor_id)
        catalog = products_as_catalog(products)
        names = names_by_sku(products)
    image_bytes = await _download_file(context, photo.file_id)
    if len(image_bytes) > MAX_PHOTO_BYTES:
        await _reply(update, texts.PHOTO_TOO_BIG)
        return
    note = (message.caption or "").strip()
    try:
        lines = await extract_stock(catalog, image_bytes, "image/jpeg", note=note)
    except ExtractError as exc:
        await _reply(update, exc.user_message)
        return
    lines = match_stock_lines(lines, catalog)
    await save_and_show_stock(update, store_id, lines, names)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or "").strip()
    if not text:
        return
    await route_text(update, text, "text")
    raise ApplicationHandlerStop


async def route_text(update: Update, text: str, source: str) -> None:
    """Texto del tendero (escrito o de un audio): corrige el conteo, cambia el pedido o registra ventas."""
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        store_id = store.id
        products = await load_active_products(session, store.distributor_id)
        catalog = products_as_catalog(products)
        names = names_by_sku(products)
        pending_stock = await get_pending(session, store.id, KIND_STOCK)
        correction = parse_stock_correction(text)
        if pending_stock is not None and correction is not None:
            name, quantity = correction
            match = best_product_match(name, catalog)
            if match is None:
                await _reply(update, texts.AI_FAIL)
                return
            sku = str(match["sku"])
            lines = apply_stock_correction(list(pending_stock.payload.get("lines") or []), sku, quantity)
            pending_stock.payload = {**pending_stock.payload, "lines": lines}
            pending_stock.expires_at = datetime.now(UTC) + PENDING_TTL
            await _reply(
                update,
                texts.stock_summary(lines, names),
                reply_markup=keyboards.confirm_stock_keyboard(),
            )
            return
        pending_order = await get_pending(session, store.id, KIND_ORDER)
        order_payload = dict(pending_order.payload) if pending_order is not None else None

    if order_payload is not None and await apply_order_message(update, store_id, text, products, order_payload):
        return

    # Todo texto va a Grok: el tendero no tiene que decir "vendí" para registrar una venta.
    try:
        lines = await extract_sales(catalog, transcript=text)
    except ExtractError as exc:
        await _reply(update, exc.user_message)
        return
    lines = match_sale_lines(lines, catalog)
    await save_and_show_sales(update, store_id, lines, source, names)


async def apply_order_message(
    update: Update, store_id: int, text: str, products: list[Product], payload: dict
) -> bool:
    """Si el mensaje pide cambios al pedido abierto, los aplica y muestra el pedido nuevo.

    Devuelve False si el mensaje no era sobre el pedido (entonces se trata como ventas).
    """
    catalog = products_as_catalog(products)
    by_id = {product.id: product for product in products}
    current = to_order_lines(list(payload.get("lines") or []), by_id)
    current_text = "\n".join(f"- sku={line.sku} | {line.name} | {line.cases} cajas" for line in current) or "(vacío)"
    try:
        changes = await extract_order_changes(catalog, text, current_text)
    except ExtractError:
        return False
    if not changes:
        return False
    changes = match_order_changes(changes, catalog)
    result = apply_order_changes(list(payload.get("lines") or []), changes, products_by_sku(products))
    capped = cap_to_available(result.lines, by_id)
    async with session_scope() as session:
        pending = await get_pending(session, store_id, KIND_ORDER)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return True
        pending.payload = {**pending.payload, "lines": capped.lines}
        pending.expires_at = datetime.now(UTC) + ORDER_PENDING_TTL
    lines = to_order_lines(capped.lines, by_id)
    await _reply(
        update,
        texts.order_draft(
            lines,
            order_total(lines),
            date.fromisoformat(payload["delivery_date"]),
            datetime.fromisoformat(payload["order_deadline"]),
            result.not_found,
            capped.not_found,
        ),
        reply_markup=keyboards.order_keyboard(),
    )
    return True


async def handle_confirm_sales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_SALES)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        products = products_by_sku(await load_active_products(session, store.distributor_id))
        source = pending.payload.get("source") or "text"
        if source not in {"voice", "text", "manual"}:
            source = "text"
        now = datetime.now(UTC)
        saved = 0
        for line in pending.payload.get("lines") or []:
            product = products.get(line.get("sku") or "")
            quantity = int(line.get("quantity") or 0)
            if product is None or quantity <= 0:
                continue
            session.add(
                Sale(
                    store_id=store.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=product.unit_price,
                    sold_at=now,
                    source=source,
                )
            )
            saved += 1
        await session.delete(pending)
    await _reply(update, texts.SAVED_SALES if saved else texts.EMPTY_SALES)


async def handle_cancel_sales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_SALES)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        await session.delete(pending)
    await _reply(update, texts.CANCELLED)


async def handle_confirm_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_STOCK)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        products = products_by_sku(await load_active_products(session, store.distributor_id))
        source = pending.payload.get("source") or "photo"
        if source not in {"photo", "manual"}:
            source = "photo"
        now = datetime.now(UTC)
        saved = 0
        for line in pending.payload.get("lines") or []:
            product = products.get(line.get("sku") or "")
            quantity = int(line.get("quantity") or 0)
            if product is None or quantity < 0:
                continue
            session.add(
                StockCount(
                    store_id=store.id,
                    product_id=product.id,
                    quantity=quantity,
                    counted_at=now,
                    source=source,
                )
            )
            saved += 1
        await session.delete(pending)
    await _reply(update, texts.SAVED_STOCK if saved else texts.EMPTY_STOCK)


async def handle_cancel_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_STOCK)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        await session.delete(pending)
    await _reply(update, texts.CANCELLED)


KIND_ORDER = "order"


async def handle_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        analysis = await load_store_analysis(session, store.id)
    if analysis is None or not any(p.has_count for p in analysis.products):
        await _reply(update, texts.NO_STOCK)
        return
    products = sorted(analysis.products, key=lambda p: (not p.has_count, p.name))
    await _reply(update, texts.stock_list(products))


async def handle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        analysis = await load_store_analysis(session, store.id)
    if analysis is None:
        await _reply(update, texts.NO_STORE)
        return
    await _reply(update, texts.alerts_list(analysis.at_risk, analysis.stagnant))


async def handle_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pedido sugerido para la próxima entrega. Queda como borrador que el tendero puede cambiar."""
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        analysis = await load_store_analysis(session, store.id)
        if analysis is None:
            await _reply(update, texts.NO_STORE)
            return
        by_id = {p.id: p for p in await load_active_products(session, store.distributor_id)}
        capped = cap_to_available(
            [
                {"product_id": line.product_id, "cases": line.cases, "reason": line.reason}
                for line in analysis.order_lines
            ],
            by_id,
        )
        payload_lines = capped.lines
        await replace_pending(
            session,
            store.id,
            KIND_ORDER,
            {
                "lines": payload_lines,
                "delivery_date": analysis.next_delivery.isoformat(),
                "order_deadline": analysis.order_deadline.isoformat(),
            },
            ttl=ORDER_PENDING_TTL,
        )
    lines = to_order_lines(payload_lines, by_id)
    await _reply(
        update,
        texts.order_draft(
            lines,
            order_total(lines),
            analysis.next_delivery,
            analysis.order_deadline,
            availability_notes=capped.not_found,
        ),
        reply_markup=keyboards.order_keyboard(),
    )


async def handle_send_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_ORDER)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        draft_lines = list(pending.payload.get("lines") or [])
        product_ids = [int(line.get("product_id") or 0) for line in draft_lines]
        # FOR UPDATE: dos tiendas no pueden llevarse la misma última caja.
        locked = (
            await session.scalars(
                select(Product)
                .where(
                    Product.id.in_(product_ids),
                    Product.distributor_id == store.distributor_id,
                    Product.active.is_(True),
                )
                .with_for_update()
            )
        ).all()
        products = {p.id: p for p in locked}
        capped = cap_to_available(draft_lines, products)
        lines = to_order_lines(capped.lines, products)
        drafted_for = pending.payload.get("delivery_date")
        await session.delete(pending)
        if not lines:
            await _reply(update, texts.NO_ORDER)
            return
        # Si pasó la hora límite mientras lo revisaba, el pedido va para la entrega siguiente.
        schedule = delivery_schedule(store.visit_weekdays, store.lead_time_days, datetime.now(UTC))
        missed_deadline = drafted_for is not None and drafted_for != schedule.next_delivery.isoformat()
        order = SuggestedOrder(store_id=store.id, status="sent", delivery_date=schedule.next_delivery)
        for line in lines:
            order.items.append(
                SuggestedOrderItem(
                    product_id=line.product_id,
                    cases=line.cases,
                    units=line.units,
                    reason=line.reason[:240],
                )
            )
            product = products[line.product_id]
            if product.available_units is not None:
                product.available_units -= line.units
        session.add(order)
    await _reply(update, texts.order_sent(schedule.next_delivery, missed_deadline, capped.not_found))


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_pending(session, store.id, KIND_ORDER)
        if pending is None:
            await _reply(update, texts.NOTHING_PENDING)
            return
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.ORDER_CANCELLED)


async def handle_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        order = await session.scalar(
            select(SuggestedOrder)
            .where(SuggestedOrder.store_id == store.id, SuggestedOrder.status == "sent")
            .order_by(SuggestedOrder.created_at.desc(), SuggestedOrder.id.desc())
        )
        if order is None:
            await _reply(update, texts.NO_SENT_ORDER)
            return
        items = (
            await session.scalars(select(SuggestedOrderItem).where(SuggestedOrderItem.order_id == order.id))
        ).all()
        now = datetime.now(UTC)
        for item in items:
            if item.units > 0:
                session.add(
                    Receipt(store_id=store.id, product_id=item.product_id, quantity=item.units, received_at=now)
                )
        order.status = "received"
    await _reply(update, texts.ORDER_RECEIVED)


async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        products = sorted(await load_active_products(session, store.distributor_id), key=lambda p: p.name)
    await _reply(update, texts.catalog_list(products))


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, texts.HELP)


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, texts.UNKNOWN)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Solo el tipo de error: nunca el contenido del mensaje ni datos del tendero.
    logger.error("Error procesando update: %s", type(context.error).__name__, exc_info=context.error)
    if isinstance(update, Update):
        try:
            await _reply(update, texts.AI_FAIL)
        except Exception:
            logger.warning("No se pudo avisar el error al chat")


def register_handlers(application: Application) -> None:
    text_only = filters.TEXT & ~filters.COMMAND

    # Grupo -2: rate limit antes que todo (lanza ApplicationHandlerStop si se pasa).
    application.add_handler(TypeHandler(Update, check_rate_limit), group=-2)

    # Grupo -1: sin tienda vinculada solo pasan /start y el código.
    application.add_handler(TypeHandler(Update, require_link), group=-1)

    # Grupo 0: comandos, botones, voz, foto y código de vinculación.
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("stock", handle_stock))
    application.add_handler(CommandHandler("alertas", handle_alerts))
    application.add_handler(CommandHandler("pedido", handle_order))
    application.add_handler(CommandHandler("recibido", handle_received))
    application.add_handler(CommandHandler("catalogo", handle_catalog))
    application.add_handler(CommandHandler("ayuda", handle_help))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown))
    application.add_handler(CallbackQueryHandler(handle_consent, pattern=f"^{keyboards.ACCEPT_CONSENT}$"))
    application.add_handler(
        CallbackQueryHandler(handle_reject_consent, pattern=f"^{keyboards.REJECT_CONSENT}$")
    )
    application.add_handler(CallbackQueryHandler(handle_days, pattern=keyboards.DAYS_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_confirm_sales, pattern=f"^{keyboards.CONFIRM_SALES}$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_sales, pattern=f"^{keyboards.CANCEL_SALES}$"))
    application.add_handler(CallbackQueryHandler(handle_confirm_stock, pattern=f"^{keyboards.CONFIRM_STOCK}$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_stock, pattern=f"^{keyboards.CANCEL_STOCK}$"))
    application.add_handler(CallbackQueryHandler(handle_send_order, pattern=f"^{keyboards.SEND_ORDER}$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_order, pattern=f"^{keyboards.CANCEL_ORDER}$"))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(text_only, handle_link_code))

    # Grupo 1: texto de una tienda ya vinculada (ventas o corrección de conteo).
    application.add_handler(MessageHandler(text_only, handle_text), group=1)

    # Grupo 2: si nadie lo entendió, respuesta amable con ejemplos.
    application.add_handler(MessageHandler(text_only, handle_unknown), group=2)

    application.add_error_handler(handle_error)
