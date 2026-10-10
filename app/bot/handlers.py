from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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

from app.ai.gemini_client import GeminiExtractError, extract_sales, extract_stock
from app.ai.matching import best_product_match, match_sale_lines, match_stock_lines
from app.bot import keyboards, texts
from app.db import SessionLocal
from app.inventory.engine import load_store_analysis
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

_chat_hits: dict[int, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
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
    return store


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _reply(update, texts.ASK_CODE)
            return
        if store.consent_at is None:
            await _reply(update, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
            return
        await _reply(update, texts.ALREADY_LINKED)


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


def looks_like_sales(text: str) -> bool:
    return "vend" in text.casefold()


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


async def replace_pending(session: AsyncSession, store_id: int, kind: str, payload: dict) -> None:
    rows = (await session.scalars(select(PendingAction).where(PendingAction.store_id == store_id))).all()
    for row in rows:
        await session.delete(row)
    session.add(
        PendingAction(
            store_id=store_id,
            kind=kind,
            payload=payload,
            expires_at=datetime.now(UTC) + PENDING_TTL,
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
        store_id = store.id
        products = await load_active_products(session, store.distributor_id)
        catalog = products_as_catalog(products)
        names = names_by_sku(products)
    audio_bytes = await _download_file(context, voice.file_id)
    try:
        lines = await extract_sales(
            catalog,
            audio_bytes=audio_bytes,
            audio_mime_type=voice.mime_type or "audio/ogg",
        )
    except GeminiExtractError as exc:
        await _reply(update, exc.user_message)
        return
    lines = match_sale_lines(lines, catalog)
    await save_and_show_sales(update, store_id, lines, "voice", names)


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
    except GeminiExtractError as exc:
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
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            raise ApplicationHandlerStop
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
                await _reply(update, texts.GEMINI_FAIL)
                raise ApplicationHandlerStop
            sku = str(match["sku"])
            lines = apply_stock_correction(list(pending_stock.payload.get("lines") or []), sku, quantity)
            pending_stock.payload = {**pending_stock.payload, "lines": lines}
            pending_stock.expires_at = datetime.now(UTC) + PENDING_TTL
            await _reply(
                update,
                texts.stock_summary(lines, names),
                reply_markup=keyboards.confirm_stock_keyboard(),
            )
            raise ApplicationHandlerStop
        if not looks_like_sales(text):
            return
    try:
        lines = await extract_sales(catalog, transcript=text)
    except GeminiExtractError as exc:
        await _reply(update, exc.user_message)
        raise ApplicationHandlerStop
    lines = match_sale_lines(lines, catalog)
    await save_and_show_sales(update, store_id, lines, "text", names)
    raise ApplicationHandlerStop


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
