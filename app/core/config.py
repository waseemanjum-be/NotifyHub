# app/core/config.py

from __future__ import annotations

from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the service.

    - Reads from environment variables
    - Also reads from `.env` when present (useful for local/dev)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    APP_NAME: str = "FastAPI Server"
    ENV: str = "local"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # CORS (comma-separated in env: "https://a.com,https://b.com")
    CORS_ORIGINS: List[str] = Field(default_factory=list)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            return [item.strip() for item in s.split(",") if item.strip()]
        return []

    # MongoDB (Motor / async)
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "app_db"

    # Scale-friendly knobs (optional)
    MONGODB_APP_NAME: str = "fastapi-server"
    MONGODB_CONNECT_TIMEOUT_MS: int = 10000
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = 10000

    # -------------------------
    # Provider configuration (config-driven)
    # -------------------------
    EMAIL_PROVIDER_BASE_URL: str = ""
    EMAIL_PROVIDER_API_KEY: str = ""

    SMS_PROVIDER_BASE_URL: str = ""
    SMS_PROVIDER_API_KEY: str = ""

    PUSH_PROVIDER_BASE_URL: str = ""
    PUSH_PROVIDER_API_KEY: str = ""

    PROVIDER_TIMEOUT_MS: int = 5000

    # Comma-separated in env (e.g. "408,429,500,502,503,504")
    PROVIDER_RETRYABLE_STATUS_CODES: List[int] = Field(
        default_factory=lambda: [408, 429, 500, 502, 503, 504]
    )

    @field_validator("PROVIDER_RETRYABLE_STATUS_CODES", mode="before")
    @classmethod
    def _parse_retryable_codes(cls, v):
        default_codes = [408, 429, 500, 502, 503, 504]
        if v is None:
            return default_codes
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default_codes
            return [int(item.strip()) for item in s.split(",") if item.strip()]
        return default_codes

    # -------------------------
    # Cache configuration (config-driven)
    # -------------------------
    CACHE_BACKEND: str = "none"  # none | lru | memcache
    CACHE_TTL_SECONDS: int = 300

    MEMCACHE_HOST: str = "localhost"
    MEMCACHE_PORT: int = 11211
    MEMCACHE_TIMEOUT_MS: int = 200


settings = Settings()
