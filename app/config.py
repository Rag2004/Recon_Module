"""recon_service settings."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_ENV_CANDIDATES = (_SERVICE_ROOT / ".env", _SERVICE_ROOT.parent / ".env")
_ENV_FILE = next((p for p in _ENV_CANDIDATES if p.exists()), _SERVICE_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LOG_LEVEL: str = "INFO"
    RECON_SERVICE_PORT: int = 8013
    RECON_INTERVAL_SEC: float = 1.0
    # dhan = GET /v2/positions. kotak = Neo positions(). mock = paper book.
    RECON_BROKER_MODE: str = "dhan"
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "quant_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    POSITION_STATE_DIR: str = ""
    DHAN_ACCESS_TOKEN: str = ""
    DHAN_CLIENT_ID: str = ""
    DHAN_API_BASE: str = "https://api.dhan.co"


settings = Settings()
