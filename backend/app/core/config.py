from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LostLink AI"
    environment: str = "development"
    demo_mode: bool = True
    database_url: str = "sqlite+aiosqlite:///./lostlink.db"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    line_channel_secret: str = ""
    line_channel_access_token: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    e5_model: str = "intfloat/multilingual-e5-base"
    siglip_model: str = "google/siglip2-base-patch16-256"

    match_notify_threshold: float = 0.75
    match_review_threshold: float = 0.60
    admin_api_key: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
