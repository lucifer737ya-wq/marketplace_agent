import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
BACKUPS_DIR = BASE_DIR / "backups"
DB_DIR = BASE_DIR / "database"

for directory in (LOGS_DIR, BACKUPS_DIR, DB_DIR):
    directory.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram
    TG_BOT_TOKEN: str = Field(..., description="Токен Telegram бота")
    TG_ADMIN_ID: int = Field(..., description="ID администратора в Telegram")

    # Ozon Seller API
    OZON_CLIENT_ID: str = Field(..., description="Client-Id Ozon Seller")
    OZON_API_KEY: str = Field(..., description="Api-Key Ozon Seller")

    # Ozon Performance API
    OZON_PERF_CLIENT_ID: str = Field(default="", description="Client-Id Ozon Performance")
    OZON_PERF_SECRET: str = Field(default="", description="Client-Secret Ozon Performance")

    # Wildberries API v3
    WB_API_TOKEN: str = Field(..., description="Единый токен WB API (Статистика, Цены, Продвижение)")

    # Gemini API для саморедактирования кода в supervisor.py
    GEMINI_API_KEY: str = Field(..., description="API ключ Google Gemini")
    GEMINI_MODEL: str = Field(default="gemini-2.5-pro", description="Модель для генерации патчей")

    # Системные настройки
    DB_URL: str = Field(default=f"sqlite+aiosqlite:///{DB_DIR / 'store.db'}")
    HEARTBEAT_FILE: Path = Field(default=LOGS_DIR / "heartbeat.json")
    TAX_RATE: float = Field(default=0.06, description="Ставка УСН (6%)")
    ACQUIRING_RATE: float = Field(default=0.015, description="Эквайринг (1.5%)")


settings = Settings()