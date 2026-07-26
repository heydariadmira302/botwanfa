from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr = SecretStr("")
    super_admin_ids: tuple[int, ...] = ()
    database_url: str = "postgresql+asyncpg://botwanfa:botwanfa@localhost:5432/botwanfa"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
    sender_poll_seconds: float = Field(default=0.25, gt=0)

    @field_validator("super_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
