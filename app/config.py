from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    public_base_url: str = ""
    database_url: str = "postgresql+asyncpg://tendero:tendero@localhost:5432/tenderobot"
    grok_api_key: str = ""
    grok_base_url: str = "https://api.reto.pltk.mx/v1"
    grok_model: str = "grok-4.20-0309-non-reasoning"
    bot_mode: str = "polling"
    session_secret: str = "dev-session-secret-change-me"
    # Cuenta del panel que crea el seed. Sin valores por defecto: nada de contraseñas publicadas.
    panel_email: str = ""
    panel_password: str = Field("", validation_alias=AliasChoices("PANEL_PASSWORD", "DEMO_PASSWORD"))
    # Correo de respaldo para tenderos que no abren el bot. Vacío = no se mandan correos.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @field_validator("bot_mode")
    @classmethod
    def bot_mode_must_be_known(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"webhook", "polling"}:
            raise ValueError("BOT_MODE debe ser webhook o polling")
        return normalized

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://") and "+asyncpg" not in value.split("://", 1)[0]:
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
