import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from telegram import Update
from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.bot.reminders import reminder_loop
from app.config import get_settings
from app.web.admin import router as admin_router
from app.web.login import router as login_router
from app.web.public import router as public_router
from app.web.routes import router as panel_router

settings = get_settings()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx registra las URLs de Telegram, que incluyen el token del bot: no loguearlas.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/telegram/webhook"


def build_application() -> Application:
    builder = Application.builder().token(settings.telegram_bot_token)
    if settings.bot_mode == "webhook":
        # En webhook los updates llegan por FastAPI: no se necesita updater.
        builder = builder.updater(None)
    application = builder.build()
    register_handlers(application)
    return application


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN vacío: el bot no se inicia, solo la API web")
        app.state.telegram_app = None
        yield
        return

    application = build_application()
    app.state.telegram_app = application
    await application.initialize()
    await application.start()

    if settings.bot_mode == "webhook":
        if not settings.public_base_url or not settings.telegram_webhook_secret:
            raise RuntimeError("En modo webhook se requieren PUBLIC_BASE_URL y TELEGRAM_WEBHOOK_SECRET")
        webhook_url = settings.public_base_url.rstrip("/") + WEBHOOK_PATH
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info("Bot en modo webhook")
    else:
        # Polling solo para desarrollo local: se borra cualquier webhook previo.
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot en modo polling")

    reminders = asyncio.create_task(reminder_loop(application.bot))
    try:
        yield
    finally:
        reminders.cancel()
        if settings.bot_mode == "polling" and application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()


app = FastAPI(title="TenderoBot", version="0.1.0", lifespan=lifespan)

# Panel web: sesión en cookie firmada (HttpOnly por defecto en Starlette), 8 horas.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="tenderobot_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=settings.public_base_url.startswith("https"),
)
app.include_router(public_router)
app.include_router(login_router)
app.include_router(panel_router)
app.include_router(admin_router)
app.mount(
    "/panel/static",
    StaticFiles(directory=str(Path(__file__).parent / "web" / "static")),
    name="panel_static",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "bot_mode": settings.bot_mode}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> dict[str, bool]:
    application: Application | None = request.app.state.telegram_app
    if application is None or settings.bot_mode != "webhook":
        raise HTTPException(status_code=404)
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received, settings.telegram_webhook_secret):
        raise HTTPException(status_code=403)
    update = Update.de_json(await request.json(), application.bot)
    # Se encola y se responde de una vez a Telegram; la Application lo procesa en segundo plano.
    await application.update_queue.put(update)
    return {"ok": True}
