import os
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = Path(os.environ.get("MARKETPLACE_LOG_DIR", BASE_DIR / "logs"))
BACKUPS_DIR = Path(os.environ.get("MARKETPLACE_BACKUP_DIR", BASE_DIR / "backups"))
DB_DIR = BASE_DIR / "database"

def prepare_runtime_dirs() -> None:
    for directory in (LOGS_DIR, BACKUPS_DIR, DB_DIR):
        directory.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("MARKETPLACE_ENV_FILE", str(BASE_DIR / ".env")) or None,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
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

    # Wildberries API v3 (поддержка обоих вариантов имени переменной в .env)
    WB_API_TOKEN: str = Field(
        ...,
        validation_alias=AliasChoices("WB_API_TOKEN", "WB_TOKEN"),
        description="Единый токен WB API (Статистика, Цены, Продвижение, Маркетплейс)",
    )

    # Gemini API
    GEMINI_API_KEY: str = Field(..., description="API ключ Google Gemini")
    GEMINI_MODEL: str = Field(
        default="gemini-3.8-flash", description="Базовая модель Gemini"
    )
    GEMINI_FAST_MODEL: str = Field(
        default="gemini-3.5-flash-lite",
        description="Эконом-модель для отзывов, вопросов и коротких задач",
    )
    # Прямой адрес API Google. Работает без посредников и без локального прокси.
    GEMINI_DIRECT_URL: str = Field(
        default="https://generativelanguage.googleapis.com",
        description="Прямой базовый адрес Gemini API (используется первым маршрутом)",
    )
    GEMINI_TIMEOUT: int = Field(default=90, description="Таймаут запроса к Gemini, сек")

    # Сетевая маршрутизация.
    # Каждый запрос перебирает все доступные маршруты: прямые адреса площадок,
    # прямые адреса Google и локальные прокси. Недоступный маршрут уходит в
    # карантин и не тормозит следующие вызовы.
    LOCAL_PROXY_URL: str = Field(
        default="http://127.0.0.1:3066",
        description="Локальный HTTP-прокси (резервный маршрут для Telegram и Gemini)",
    )
    LOCAL_PROXY_FALLBACK_URL: str = ""
    # Список дополнительных прокси через запятую: http://127.0.0.1:3067,...
    LOCAL_PROXY_LIST: str = Field(
        default="", description="Дополнительные локальные прокси через запятую"
    )
    # Прямые запросы без прокси разрешены (Gemini и площадки работают напрямую).
    ALLOW_DIRECT_CONNECTIONS: bool = Field(
        default=True, description="Разрешить прямые HTTPS-запросы без прокси"
    )
    # Сколько секунд держать в карантине упавший маршрут.
    ROUTE_COOLDOWN_SECONDS: int = Field(
        default=120, description="Карантин недоступного маршрута, сек"
    )
    OZON_REPLY_DRY_RUN: bool = False
    # Устаревшие финансовые методы Ozon отключены 08.09.2026, оставлены как
    # последняя попытка только при явном включении.
    OZON_LEGACY_FINANCE: bool = Field(
        default=False, description="Пробовать отключённый /v3/finance/transaction/list"
    )
    TIMEZONE: str = Field(default="Europe/Moscow", description="Часовой пояс планировщика")

    # Системные настройки, БД и защита от дублей процессов (Single-Instance Lock)
    DB_URL: str = Field(default=f"sqlite+aiosqlite:///{(DB_DIR / 'store.db').as_posix()}")
    HEARTBEAT_FILE: Path = Field(default=LOGS_DIR / "heartbeat.json")
    RESTART_SIGNAL_FILE: Path = Field(default=LOGS_DIR / "restart_worker.signal")
    SUPERVISOR_PID_FILE: Path = Field(default=LOGS_DIR / "supervisor.pid")
    WORKER_PID_FILE: Path = Field(default=LOGS_DIR / "worker.pid")
    SUPERVISOR_LOCK_PORT: int = Field(default=47831, description="TCP-порт single-instance блокировки supervisor.py")
    WORKER_LOCK_PORT: int = Field(default=47832, description="TCP-порт single-instance блокировки worker.py")

    # Эталонные финансовые константы бренда YaSoKo
    TAX_RATE: float = Field(default=0.06, description="Ставка УСН (6%)")
    ACQUIRING_RATE: float = Field(default=0.015, description="Эквайринг (1.5%)")
    OZON_LOW_PRICE_LIMIT: float = Field(default=300.0, description="Порог льготной комиссии Ozon (до 300 ₽)")
    OZON_HIGH_COMMISSION_RATE: float = Field(default=0.52, description="Комиссия Ozon свыше 300 ₽ (52%)")
    WB_MIN_PRICE_RATIO: float = Field(default=0.92, description="Коэффициент минимальной цены для акций WB")
    OZON_REVIEWS_MIN_DATE: str = Field(default="13.11.2026", description="Минимальная дата автоответов Ozon")

    @field_validator(
        "TG_BOT_TOKEN",
        "OZON_CLIENT_ID",
        "OZON_API_KEY",
        "OZON_PERF_CLIENT_ID",
        "OZON_PERF_SECRET",
        "WB_API_TOKEN",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FAST_MODEL",
        "GEMINI_DIRECT_URL",
        "LOCAL_PROXY_URL",
        "LOCAL_PROXY_LIST",
        mode="before",
    )
    @classmethod
    def _strip_quotes_and_spaces(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'").strip()
        return value

    @property
    def WB_TOKEN(self) -> str:
        return self.WB_API_TOKEN


settings = Settings()
