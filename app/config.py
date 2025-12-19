from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram
    bot_token: str = Field(..., alias="BOT_TOKEN")

    # Claude API
    claude_api_key: str = Field(..., alias="CLAUDE_API_KEY")
    claude_model: str = Field(default="claude-sonnet-4-20250514", alias="CLAUDE_MODEL")
    claude_model_fast: str = Field(default="claude-haiku-4-20250514", alias="CLAUDE_MODEL_FAST")
    claude_max_tokens: int = Field(default=500, alias="CLAUDE_MAX_TOKENS")

    # Database (SQLite for dev, PostgreSQL for prod)
    database_url: str = Field(
        default="sqlite+aiosqlite:///ryadom.db",
        alias="DATABASE_URL"
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Rate Limiting
    free_messages_per_day: int = Field(default=10, alias="FREE_MESSAGES_PER_DAY")
    basic_messages_per_day: int = Field(default=100, alias="BASIC_MESSAGES_PER_DAY")
    premium_messages_per_day: int = Field(default=1000, alias="PREMIUM_MESSAGES_PER_DAY")

    # Subscription Prices (in rubles)
    basic_price: int = Field(default=299, alias="BASIC_PRICE")
    premium_price: int = Field(default=699, alias="PREMIUM_PRICE")

    # YooKassa
    yookassa_shop_id: Optional[str] = Field(default=None, alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: Optional[str] = Field(default=None, alias="YOOKASSA_SECRET_KEY")

    # Sentry (optional)
    sentry_dsn: Optional[str] = Field(default=None, alias="SENTRY_DSN")

    # Admin telegram IDs (comma-separated)
    admin_ids: str = Field(default="", alias="ADMIN_IDS")

    # Environment
    debug: bool = Field(default=False, alias="DEBUG")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Health check
    health_port: int = Field(default=8080, alias="HEALTH_PORT")

    @property
    def admin_telegram_ids(self) -> list[int]:
        """Parse admin IDs from comma-separated string."""
        if not self.admin_ids:
            return []
        return [int(id.strip()) for id in self.admin_ids.split(",") if id.strip()]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience export
settings = get_settings()
