from functools import lru_cache

from pydantic import field_validator
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
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    bot_mode: str = "polling"
    session_secret: str = "dev-session-secret-change-me"
    demo_password: str = "demo1234"

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
