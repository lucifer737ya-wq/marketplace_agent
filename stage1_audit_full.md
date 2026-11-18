# Этап 1 — аудит и полные исправленные файлы

## Найденные проблемы и исправления

1. **worker.py — `main`, `scheduled_marketplaces_poll`, `sync_marketplace_silent`, `daily_18h_price_and_ads_digest`, `btn_sync_mp`:** отдельный вечерний digest дублировал функциональность аудита; при синхронизации Ozon обращение к отсутствующим `promo_max_price` и `promo_id` прерывало обработку. Задачи сведены к одному плановому аудиту, синхронизация выполняется пакетно; бизнес-правильность аудита и изменение расписания следует отдельно проверить на этапе 2.
2. **worker.py — `scheduled_night_self_heal`, `cmd_manual_self_heal`:** отсутствовал импорт `run_full_self_check_and_heal`, что приводило к `NameError`.
3. **worker.py — `main`, `configure_scheduler`, `scheduled_reviews_and_questions_job`:** не было единой блокировки процесса и защиты от перекрывающихся экземпляров задач; после ошибок запуска возможна потеря ресурсов. Добавлены локальный TCP lock, уникальные ID задач, `max_instances=1`, `coalesce`, закрытие сессии бота и движка в `finally`. Локальная блокировка не защищает от другого сервера с тем же токеном.
4. **worker.py — `ensure_working_telegram_route`; requirements.txt.txt:** прокси-маршрут требовал зависимость `aiohttp-socks`, которой не было в списке. Добавлены зависимости прокси и Playwright; прямой fallback Telegram отключён. Реальную доступность прокси и браузера в Windows необходимо проверить отдельно.
5. **supervisor.py — `WorkerSupervisor.apply_patch_safely`:** аргументы `request_gemini_patch` передавались в неправильном порядке. Исправлен вызов. Дополнительно исправлены сетевые URL, ограничен маршрут Telegram/Gemini локальным прокси, добавлены блокировка supervisor и обработка файла сигнала перезапуска.
6. **start_hidden.vbs; supervisor.py — `WorkerSupervisor.run_forever`, `start_worker`, `cleanup_orphan_worker`:** повторные запуски могли оставлять несколько процессов с одним токеном Telegram. Добавлены проверка WMI и локальные блокировки. При занятом порте старый процесс не убивается по устаревшему PID-файлу — требуется ручная проверка.
7. **database/models.py — `Product`, `init_db`; database/crud.py — `upsert_product_costs`, `record_first_mile_supply`, `update_product_market_state`, `bulk_upsert_costs`:** единый SKU используется на двух маркетплейсах, а старый первичный ключ включал только SKU. Добавлены составной ключ `(sku, marketplace)` и миграция старой схемы; данные, уже перезаписанные до миграции, восстановить невозможно.
8. **database/models.py — `PendingAction`; database/crud.py — `create_pending_action`, `get_pending_action`, `mark_action_executed`, `mark_action_rejected`:** разный регистр статуса, отсутствовавшие `payload` и `executed_at`, а также строковое представление ORM-объекта мешали callback-кнопкам. Добавлены совместимые свойства и миграция.
9. **database/models.py — `get_db_session`, `init_db`, `close_db`; database/crud.py — операции записи:** добавлены явное закрытие сессий и движка, rollback, `busy_timeout`, сериализация локальных записей и обработка дублей продаж. Это не гарантия отсутствия блокировок при записи из других процессов.
10. **products_catalog.py — `PRODUCTS_CATALOG`, `get_catalog_product`, `resolve_catalog_external_id`:** отсутствовало поле `name`, а в одной рекомендации содержались неизвестные артикулы. Добавлены имена и справочные индексы. Ozon SKU не подменяет Ozon `product_id`.
11. **config.py — `Settings`:** путь SQLite нормализован для Windows; добавлена совместимость `WB_TOKEN`/`WB_API_TOKEN`, конфигурация прокси и блокировок.

## Важные ограничения

- `CLAUDE.md` в репозитории отсутствует; файл `supervision.py` отсутствует, проверен корневой `supervisor.py`.
- **Автоответчик временно запускается только в `dry_run`:** в `services/ozon/browser_reviews.py` функция проверки даты объявлена, но не вызывается в циклах. Строгий фильтр и настоящую публикацию необходимо исправить и проверить на этапе 4; это пока не выполненное требование. Существующий модуль вопросов не изменён.
- Этапы 2–4 не проводились. Финансовые формулы, кампании, сетевые маршруты за пределами файлов этапа 1, все обработчики и реальная работа Playwright не проверены. Не считать систему готовой к промышленному запуску.
- Перед первым запуском новой версии сделайте резервную копию SQLite, включая корректное завершение процессов и использование SQLite backup, чтобы учесть WAL. Миграция изменяет схему автоматически.
- Проверены синтаксис, создание и повторная миграция тестовой SQLite, сохранение записей, параллельные upsert, пакетное обновление, PendingAction, уникальность 10 ID задач и локальные TCP-блокировки. Внешние API, Windows WMI, живой Telegram и реальный браузер не тестировались.

## Полные исходники

Ниже приведены все изменённые файлы целиком, без сокращений.

### `config.py`

```python
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
        extra="ignore",
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
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Базовая модель Gemini")

    # Сетевая маршрутизация (Telegram и Gemini — через локальный прокси; Ozon и WB — напрямую)
    LOCAL_PROXY_URL: str = Field(
        default="http://127.0.0.1:3066",
        description="Локальный HTTP-прокси для Telegram и Gemini API",
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
        "LOCAL_PROXY_URL",
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
```

### `products_catalog.py`

```python
"""
Полный каталог сертифицированной продукции бренда YaSoKo.
Все товары зарегистрированы в системе «Честный ЗНАК» и имеют Декларацию о соответствии (ТР ТС 009/2011).
"""

from typing import Any, Dict, Optional

LEGAL_COMPLIANCE_INFO = (
    "Продукция бренда YaSoKo официально сертифицирована, прошла лабораторные испытания, "
    "имеет действующую декларацию о соответствии ТР ТС 009/2011 «О безопасности парфюмерно-косметической продукции» "
    "и подлежит обязательной национальной маркировке в государственной системе «Честный ЗНАК»."
)

GEL_COMMON_DESCRIPTION = (
    "YaSoKo — профессиональный камуфляжный моделирующий гель для наращивания, укрепления и коррекции ногтей. "
    "Плотный натуральный оттенок перекрывает ногтевую пластину уже с первого слоя, создавая эффект аккуратного, ухоженного маникюра. "
    "Формула разработана так, чтобы материал был предсказуемым в работе. Средне-вязкая консистенция не растекается и не затекает под кутикулу, "
    "что позволяет наносить гель сразу на несколько ногтей и спокойно формировать архитектуру без риска потери формы. "
    "После полимеризации в UV или LED-лампе в течение 60–90 секунд гель полностью сохраняет заданную форму — усадка отсутствует, "
    "проплешин не образуется. Плотная структура сокращает время опила до минимума. Высокая адгезия обеспечивает прочное сцепление "
    "с натуральным ногтем, а pH-нейтральный состав делает материал подходящим даже для тонких и ломких ногтей. "
    "Стойкость покрытия до четырех недель без сколов, трещин и отслоек. "
    "Формула HEMA FREE и 11-FREE не содержит токсичных мономеров, формальдегидов, толуола и камфоры. "
    "Материал не вызывает жжения в лампе и не имеет резкого химического запаха. "
    "Объем: 15 г. Время полимеризации: 60–90 секунд в UV/LED-лампе."
)

ALLOWED_UPSELL_LIST = (
    "В ассортименте YaSoKo есть ТОЛЬКО следующие категории для рекомендаций:\n"
    "1. Базы (15 мл): каучуковая эластичная (арт. 21000), многофункциональная жесткая 5 в 1 (арт. 21001), адгезивная 8 в 1 (арт. 21002).\n"
    "2. Топы (15 мл): бриллиантовый с УФ-защитой без липкого слоя (арт. 22000), кристально-прозрачный самовыравнивающийся (арт. 22001), молочный полупрозрачный Milky Top (арт. 22002).\n"
    "3. Камуфлирующие гели YaSoKo (15 г) других оттенков палитры.\n"
    "СТРОГО ЗАПРЕЩЕНО рекомендовать несуществующие товары нашего бренда: праймеры, обезжириватели, жидкости для снятия, безворсовые салфетки, лампы и пилки!"
)

# Паспорт товаров бренда (каждая запись содержит и "title", и "name" для полной совместимости всех модулей)
PRODUCTS_CATALOG: Dict[str, Dict[str, Any]] = {
    # --- ОСНОВНАЯ ЛИНЕЙКА ГЕЛЕЙ 1001–1511 (с названиями цветов) ---
    "1001": {
        "art": "1001", "shade": "#01", "color_name": "Ванильный Крем",
        "name": "Гель YaSoKo 15 г, #01 Ванильный Крем",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #01 Ванильный Крем",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE, pH-нейтральный",
        "ozon_sku": "3405669329", "wb_sku": "765197240", "barcode": "4638944332008", "gtin": "04638944332008",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000) под подложку или топ без липкого слоя (арт. 22000)",
    },
    "1002": {
        "art": "1002", "shade": "#02", "color_name": "Зефирная Мечта",
        "name": "Гель YaSoKo 15 г, #02 Зефирная Мечта",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #02 Зефирная Мечта",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668968", "wb_sku": "765197247",
        "barcode": "4638944332015", "gtin": "04638944332015",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo без липкого слоя (арт. 22000)",
    },
    "1003": {
        "art": "1003", "shade": "#03", "color_name": "Клубничный Джем",
        "name": "Гель YaSoKo 15 г, #03 Клубничный Джем",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #03 Клубничный Джем",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668876", "wb_sku": "765197248",
        "barcode": "4638944332022", "gtin": "04638944332022",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1006": {
        "art": "1006", "shade": "#06", "color_name": "Малиновый Сорбет",
        "name": "Гель YaSoKo 15 г, #06 Малиновый Сорбет",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #06 Малиновый Сорбет",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668993", "wb_sku": "765197241",
        "barcode": "4638944332039", "gtin": "04638944332039",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1010": {
        "art": "1010", "shade": "#10", "color_name": "Малиновый Щербет",
        "name": "Гель YaSoKo 15 г, #10 Малиновый Щербет",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #10 Малиновый Щербет",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669196", "wb_sku": "765197249",
        "barcode": "4638944332046", "gtin": "04638944332046",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "каучуковая эластичная база YaSoKo (арт. 21000)",
    },
    "1023": {
        "art": "1023", "shade": "#23", "color_name": "Черничный Мусс",
        "name": "Гель YaSoKo 15 г, #23 Черничный Мусс",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #23 Черничный Мусс",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669300", "wb_sku": "765197245",
        "barcode": "4638944332053", "gtin": "04638944332053",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1028": {
        "art": "1028", "shade": "#28", "color_name": "Миндальное Молоко",
        "name": "Гель YaSoKo 15 г, #28 Миндальное Молоко",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #28 Миндальное Молоко",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669632", "wb_sku": "765197242",
        "barcode": "4638944332060", "gtin": "04638944332060",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "молочный топ Milky Top (арт. 22002) или прозрачный топ (арт. 22001)",
    },
    "1031": {
        "art": "1031", "shade": "#31", "color_name": "Арахисовое Пралине",
        "name": "Гель YaSoKo 15 г, #31 Арахисовое Пралине",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #31 Арахисовое Пралине",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669169", "wb_sku": "765197243",
        "barcode": "4638944332077", "gtin": "04638944332077",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1033": {
        "art": "1033", "shade": "#33", "color_name": "Карамельный Макиато",
        "name": "Гель YaSoKo 15 г, #33 Карамельный Макиато",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #33 Карамельный Макиато",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668938", "wb_sku": "765197246",
        "barcode": "4638944332084", "gtin": "04638944332084",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22001)",
    },
    "1103": {
        "art": "1103", "shade": "#39", "color_name": "Ванильная Глазурь",
        "name": "Гель YaSoKo 15 г, #39 Ванильная Глазурь",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #39 Ванильная Глазурь",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669610", "wb_sku": "765197254",
        "barcode": "4638944332091", "gtin": "04638944332091",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1104": {
        "art": "1104", "shade": "#40", "color_name": "Розовый Леденец",
        "name": "Гель YaSoKo 15 г, #40 Розовый Леденец",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #40 Розовый Леденец",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669152", "wb_sku": "765197255",
        "barcode": "4638944332107", "gtin": "04638944332107",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1202": {
        "art": "1202", "shade": "#52", "color_name": "Молочный Жемчуг",
        "name": "Гель YaSoKo 15 г, #52 Молочный Жемчуг",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #52 Молочный Жемчуг",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669481", "wb_sku": "765197262",
        "barcode": "4638944332114", "gtin": "04638944332114",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "глянцевый топ без УФ-фильтра (арт. 22001), чтобы подчеркнуть мерцание шиммера",
    },
    "1204": {
        "art": "1204", "shade": "#54", "color_name": "Клубничный Жемчуг",
        "name": "Гель YaSoKo 15 г, #54 Клубничный Жемчуг",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #54 Клубничный Жемчуг",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669161", "wb_sku": "765197263",
        "barcode": "4638944332121", "gtin": "04638944332121",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1206": {
        "art": "1206", "shade": "#56", "color_name": "Сиреневое Сияние",
        "name": "Гель YaSoKo 15 г, #56 Сиреневое Сияние",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #56 Сиреневое Сияние",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669406", "wb_sku": "765197264",
        "barcode": "4638944332138", "gtin": "04638944332138",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1302": {
        "art": "1302", "shade": "#59", "color_name": "Жемчужная Жвачка",
        "name": "Гель YaSoKo 15 г, #59 Жемчужная Жвачка",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #59 Жемчужная Жвачка",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669162", "wb_sku": "765197196",
        "barcode": "4638944332145", "gtin": "04638944332145",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1305": {
        "art": "1305", "shade": "#62", "color_name": "Аквамариновая Мечта",
        "name": "Гель YaSoKo 15 г, #62 Аквамариновая Мечта",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #62 Аквамариновая Мечта",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668546", "wb_sku": "765197197",
        "barcode": "4638944332152", "gtin": "04638944332152",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1402": {
        "art": "1402", "shade": "#66", "color_name": "Манговое Смузи",
        "name": "Гель YaSoKo 15 г, #66 Манговое Смузи",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #66 Манговое Смузи",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669594", "wb_sku": "765197229",
        "barcode": "4638944332169", "gtin": "04638944332169",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1403": {
        "art": "1403", "shade": "#67", "color_name": "Желе из Личи",
        "name": "Гель YaSoKo 15 г, #67 Желе из Личи",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #67 Желе из Личи",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669236", "wb_sku": "765197230",
        "barcode": "4638944332176", "gtin": "04638944332176",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000)",
    },
    "1405": {
        "art": "1405", "shade": "#69", "color_name": "Голубая Малина",
        "name": "Гель YaSoKo 15 г, #69 Голубая Малина",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #69 Голубая Малина",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669393", "wb_sku": "765197227",
        "barcode": "4638944332183", "gtin": "04638944332183",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1408": {
        "art": "1408", "shade": "#72", "color_name": "Виноградная Газировка",
        "name": "Гель YaSoKo 15 г, #72 Виноградная Газировка",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #72 Виноградная Газировка",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669407", "wb_sku": "765197228",
        "barcode": "4638944332190", "gtin": "04638944332190",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1503": {
        "art": "1503", "shade": "#75", "color_name": "Сахарная Вата",
        "name": "Гель YaSoKo 15 г, #75 Сахарная Вата",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #75 Сахарная Вата",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "5516476562", "wb_sku": "1442223874",
        "barcode": "4638944332206", "gtin": "04638944332206",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1511": {
        "art": "1511", "shade": "#83", "color_name": "Клубничный Коктейль",
        "name": "Гель YaSoKo 15 г, #83 Клубничный Коктейль",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #83 Клубничный Коктейль",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "5516664814", "wb_sku": "1442223875",
        "barcode": "4638944332213", "gtin": "04638944332213",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000)",
    },

    # --- ДРУГИЕ ГЕЛИ КАТАЛОГА ---
    "1000": {
        "art": "1000", "shade": "Прозрачный", "color_name": "Прозрачный Кристалл",
        "name": "Гель YaSoKo 15 г, Прозрачный Кристалл",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, прозрачный",
        "category": "Прозрачный конструирующий UV/LED-гель", "volume": "15 г",
        "formula": "9-FREE", "ozon_sku": None, "wb_sku": None, "barcode": "4638944332428", "gtin": "04638944332428",
        "composition": "Acrylated Aliphatic Urethane; 1-Hydroxycyclohexyl Phenyl Ketone; 2-Hydroxyethyl Methacrylate",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22000)",
    },
    "1601": {
        "art": "1601", "shade": "#85", "color_name": "Персиковый Парфе (Peach Parfait)",
        "name": "Гель YaSoKo 15 г, #85 Персиковый Парфе",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #85 Персиковый Парфе",
        "category": "Камуфлирующий нюдовый UV/LED-гель", "volume": "15 г", "formula": "9-FREE",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332299", "gtin": "04638944332299",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; VP/DMAPA Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; 2-Hydroxyethyl Methacrylate; CI 77891; CI 77491",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "молочный топ Milky Top (арт. 22002)",
    },
    "1701": {
        "art": "1701", "shade": "#101", "color_name": "Сиреневый Блеск (Lilac Sparkle)",
        "name": "Гель YaSoKo 15 г, кошачий глаз лавандовый #101",
        "title": "Гель для наращивания ногтей YaSoKo 15 г, кошачий глаз лавандовый #101",
        "category": "Моделирующий UV/LED-гель с эффектом «Кошачий глаз»", "volume": "15 г", "formula": "HEMA FREE",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332350", "gtin": "04638944332350",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo без УФ-фильтра (арт. 22001)",
    },

    # --- БАЗЫ ДЛЯ НОГТЕЙ (15 мл) ---
    "21000": {
        "art": "21000", "model": "D44",
        "name": "База каучуковая эластичная YaSoKo, 15 мл HEMA FREE",
        "title": "База каучуковая для ногтей эластичная YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Rubber Base)", "volume": "15 мл",
        "formula": "HEMA FREE, эластичная, pH-нейтральная",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332237", "gtin": "04638944332237",
        "composition": "Styrene/Acrylates Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; Isobornyl Acrylate; 1-Hydroxycyclohexyl Phenyl Ketone; Ethyl (2,4,6-trimethylbenzoyl) Phenylphosphinate",
        "description": "Эластичная каучуковая база. Идеальна для тонких и слоящихся ногтей, а также в качестве тонкой амортизирующей подложки под твердые гели для моделирования.",
        "recommended_upsell": "камуфлирующий гель YaSoKo (арт. 1001 или 1028) и бриллиантовый топ (арт. 22000)",
    },
    "21001": {
        "art": "21001", "model": "D5A",
        "name": "База многофункциональная 5 в 1 жесткая YaSoKo, 15 мл HEMA FREE",
        "title": "База многофункциональная для ногтей 5 в 1 жесткая YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Hard Base)", "volume": "15 мл",
        "formula": "HEMA FREE, жесткая армирующая",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332244", "gtin": "04638944332244",
        "composition": "Styrene/Acrylates Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; Trimethylbenzoyl Diphenylphosphine Oxide; Isobornyl Acrylate",
        "description": "Жесткая база для армирования свободного края, укрепления длины и ремонта ногтей без проседания.",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22000)",
    },
    "21002": {
        "art": "21002", "model": "D5C",
        "name": "База жидкая 8 в 1 адгезивная YaSoKo, 15 мл HEMA FREE",
        "title": "База для ногтей жидкая 8 в 1 адгезивная YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Adhesive Base)", "volume": "15 мл",
        "formula": "HEMA FREE, супер-сцепка",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332251", "gtin": "04638944332251",
        "composition": "Acrylates/VP Copolymer; VP/DMAPA Acrylates Copolymer; Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide",
        "description": "Жидкая адгезивная база для гелевых типс, скоростного выравнивания и надежной фиксации декора.",
        "recommended_upsell": "камуфлирующий гель YaSoKo (арт. 1001, 1002)",
    },

    # --- ТОПЫ ДЛЯ НОГТЕЙ (15 мл) ---
    "22000": {
        "art": "22000", "model": "F2806",
        "name": "Топ без липкого слоя бриллиантовый YaSoKo, 15 мл 9-FREE",
        "title": "Топ для ногтей без липкого слоя бриллиантовый YaSoKo, 15 мл 9-FREE",
        "category": "Финишное покрытие (Diamond Top No Wipe)", "volume": "15 мл",
        "formula": "9-FREE, с УФ-защитой от выгорания",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332268", "gtin": "04638944332268",
        "composition": "Acrylates/VP Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; 2-Hydroxyethyl Methacrylate; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide; Hydroxycyclohexyl Phenyl Ketone",
        "description": "Глянцевый финиш с оптическим осветлителем (УФ-фильтром). Сохраняет зеркальный блеск до 4 недель, предотвращает пожелтение светлых и пастельных оттенков.",
        "recommended_upsell": "камуфлирующий гель YaSoKo светлых тонов (#01, #28)",
    },
    "22001": {
        "art": "22001", "model": "F130D",
        "name": "Топ без липкого слоя кристально-прозрачный YaSoKo, 15 мл 9-FREE",
        "title": "Топ для ногтей без липкого слоя кристально-прозрачный YaSoKo, 15 мл 9-FREE",
        "category": "Финишное покрытие (Crystal Top No Wipe)", "volume": "15 мл",
        "formula": "9-FREE, без синевы на темных оттенках",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332275", "gtin": "04638944332275",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide; 2-Hydroxyethyl Methacrylate",
        "description": "Кристально-прозрачный топ без синего отлива. Идеально передает глубину темных цветов, мерцание шиммера и гель-лаков с эффектом «кошачий глаз».",
        "recommended_upsell": "гель YaSoKo серии «Кошачий глаз» (арт. 1701) или гели с шиммером (арт. 1202, 1204, 1206)",
    },
    "22002": {
        "art": "22002", "model": "Milky Top",
        "name": "Топ молочный без липкого слоя Milky Top YaSoKo, 15 мл HEMA FREE",
        "title": "Топ для ногтей молочный без липкого слоя Milky Top YaSoKo, 15 мл HEMA FREE",
        "category": "Финишное покрытие (Milky Top No Wipe)", "volume": "15 мл",
        "formula": "HEMA FREE, полупрозрачный полуматовый/глянцевый эффект",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332282", "gtin": "04638944332282",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891",
        "description": "Полупрозрачный молочный топ. Мягко сглаживает переход у кутикулы, идеален для создания градиента (бэбибумер) и френч-дизайна.",
        "recommended_upsell": "камуфлирующие гели YaSoKo #01 Ванильный Крем или #02 Зефирная Мечта",
    },
}

# Быстрые обратные индексы внешних идентификаторов маркетплейсов -> артикул продавца (art)
OZON_SKU_TO_ART: Dict[str, str] = {
    str(meta["ozon_sku"]): art_key
    for art_key, meta in PRODUCTS_CATALOG.items()
    if meta.get("ozon_sku")
}

WB_SKU_TO_ART: Dict[str, str] = {
    str(meta["wb_sku"]): art_key
    for art_key, meta in PRODUCTS_CATALOG.items()
    if meta.get("wb_sku")
}

EXTERNAL_ID_TO_ART: Dict[str, str] = {**OZON_SKU_TO_ART, **WB_SKU_TO_ART}


def get_catalog_product(identifier: str) -> Optional[Dict[str, Any]]:
    """Находит карточку товара в каталоге по артикулу продавца, Ozon SKU, WB nmID или штрихкоду."""
    clean = str(identifier or "").strip()
    if not clean:
        return None
    if clean in PRODUCTS_CATALOG:
        return PRODUCTS_CATALOG[clean]
    mapped_art = EXTERNAL_ID_TO_ART.get(clean)
    if mapped_art and mapped_art in PRODUCTS_CATALOG:
        return PRODUCTS_CATALOG[mapped_art]
    for meta in PRODUCTS_CATALOG.values():
        if clean in (meta.get("barcode"), meta.get("gtin")):
            return meta
    return None


def resolve_catalog_external_id(sku: str, marketplace: str) -> str:
    """Возвращает известный nmID WB; Ozon SKU не является product_id."""
    meta = get_catalog_product(sku)
    if not meta:
        return ""
    mp = str(marketplace or "").strip().lower()
    if mp == "wb" and meta.get("wb_sku"):
        return str(meta["wb_sku"])
    return ""
```

### `database/models.py`

```python
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from config import settings


def utcnow_naive() -> datetime:
    """Возвращает текущее время UTC без tzinfo (совместимо с SQLite и Python 3.11+)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    # Составной первичный ключ (sku, marketplace), т.к. артикулы YaSoKo едины на Ozon и WB
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)  # "ozon" | "wb"
    external_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # product_id / числовой nmID
    title: Mapped[str] = mapped_column(String(255), default="")

    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    packaging_cost: Mapped[float] = mapped_column(Float, default=0.0)
    first_mile_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    fbo_logistics_cost: Mapped[float] = mapped_column(Float, default=65.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.23)
    target_margin_rub: Mapped[float] = mapped_column(Float, default=100.0)

    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    stock_fbo: Mapped[int] = mapped_column(Integer, default=0)
    stock_fbs: Mapped[int] = mapped_column(Integer, default=0)
    avg_daily_sales: Mapped[float] = mapped_column(Float, default=1.0)

    in_promo: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_max_price: Mapped[float] = mapped_column(Float, default=0.0)
    promo_id: Mapped[str] = mapped_column(String(64), default="")

    active_campaign_id: Mapped[str] = mapped_column(String(64), default="")
    current_ad_bid: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)  # "ozon" | "wb"
    sku: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    logistics: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(64), default="delivered")


class FirstMileSupply(Base):
    __tablename__ = "first_mile_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_number: Mapped[str] = mapped_column(String(64), index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    total_transport_cost: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SaleRecord(Base):
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    payout_amount: Mapped[float] = mapped_column(Float)
    commission_amount: Mapped[float] = mapped_column(Float)
    logistics_amount: Mapped[float] = mapped_column(Float)
    is_return: Mapped[bool] = mapped_column(Boolean, default=False)
    sale_date: Mapped[datetime] = mapped_column(DateTime, index=True)


class AdSpendRecord(Base):
    __tablename__ = "ad_spend_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    spend_amount: Mapped[float] = mapped_column(Float)
    spend_date: Mapped[datetime] = mapped_column(DateTime, index=True)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)

    @property
    def payload(self) -> Dict[str, Any]:
        """Десериализованный JSON-словарь параметров отложенного действия."""
        if not self.payload_json:
            return {}
        try:
            parsed = json.loads(self.payload_json)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def __int__(self) -> int:
        return int(self.id)

    def __str__(self) -> str:
        return str(self.id) if self.id is not None else ""


_engine_kwargs: Dict[str, Any] = {
    "echo": False,
    "connect_args": {"timeout": 30},
}
if ":memory:" not in settings.DB_URL:
    # NullPool исключает зависание фоновых потоков aiosqlite и утечки файловых дескрипторов SQLite
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.DB_URL, **_engine_kwargs)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA cache_size=-16000;")  # Ограничение кэша до 16MB под 4GB RAM
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Безопасный менеджер сессий БД с гарантированным rollback при ошибках и закрытием соединения."""
    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _migrate_legacy_sqlite_schema(conn) -> None:
    """Безопасная идемпотентная миграция существующей БД store.db без потери данных."""
    tables_res = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('products', 'pending_actions')")
    )
    existing_tables = {row[0] for row in tables_res.fetchall()}

    if "products" in existing_tables:
        info_res = await conn.execute(text("PRAGMA table_info(products)"))
        cols = info_res.fetchall()
        col_names = {row[1] for row in cols}
        pk_cols = [row[1] for row in sorted(cols, key=lambda r: r[5]) if row[5] > 0]

        if "stock_fbs" not in col_names:
            await conn.execute(text("ALTER TABLE products ADD COLUMN stock_fbs INTEGER NOT NULL DEFAULT 0"))

        # Миграция со старого одиночного PK (sku) на составной PK (sku, marketplace)
        if pk_cols == ["sku"]:
            await conn.execute(
                text(
                    """
                    CREATE TABLE products_v2 (
                        sku VARCHAR(64) NOT NULL,
                        marketplace VARCHAR(16) NOT NULL,
                        external_id VARCHAR(64) NOT NULL DEFAULT '',
                        title VARCHAR(255) NOT NULL DEFAULT '',
                        cost_price FLOAT NOT NULL DEFAULT 0.0,
                        packaging_cost FLOAT NOT NULL DEFAULT 0.0,
                        first_mile_unit_cost FLOAT NOT NULL DEFAULT 0.0,
                        fbo_logistics_cost FLOAT NOT NULL DEFAULT 65.0,
                        commission_rate FLOAT NOT NULL DEFAULT 0.23,
                        target_margin_rub FLOAT NOT NULL DEFAULT 100.0,
                        current_price FLOAT NOT NULL DEFAULT 0.0,
                        stock_fbo INTEGER NOT NULL DEFAULT 0,
                        stock_fbs INTEGER NOT NULL DEFAULT 0,
                        avg_daily_sales FLOAT NOT NULL DEFAULT 1.0,
                        in_promo BOOLEAN NOT NULL DEFAULT 0,
                        promo_max_price FLOAT NOT NULL DEFAULT 0.0,
                        promo_id VARCHAR(64) NOT NULL DEFAULT '',
                        active_campaign_id VARCHAR(64) NOT NULL DEFAULT '',
                        current_ad_bid FLOAT NOT NULL DEFAULT 0.0,
                        updated_at DATETIME,
                        PRIMARY KEY (sku, marketplace)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO products_v2 (
                        sku, marketplace, external_id, title,
                        cost_price, packaging_cost, first_mile_unit_cost,
                        fbo_logistics_cost, commission_rate, target_margin_rub,
                        current_price, stock_fbo, stock_fbs, avg_daily_sales,
                        in_promo, promo_max_price, promo_id,
                        active_campaign_id, current_ad_bid, updated_at
                    )
                    SELECT
                        sku,
                        LOWER(COALESCE(NULLIF(TRIM(marketplace), ''), 'ozon')),
                        COALESCE(external_id, ''),
                        COALESCE(title, ''),
                        COALESCE(cost_price, 0.0),
                        COALESCE(packaging_cost, 0.0),
                        COALESCE(first_mile_unit_cost, 0.0),
                        COALESCE(fbo_logistics_cost, 65.0),
                        COALESCE(commission_rate, 0.23),
                        COALESCE(target_margin_rub, 100.0),
                        COALESCE(current_price, 0.0),
                        COALESCE(stock_fbo, 0),
                        COALESCE(stock_fbs, 0),
                        COALESCE(avg_daily_sales, 1.0),
                        COALESCE(in_promo, 0),
                        COALESCE(promo_max_price, 0.0),
                        COALESCE(promo_id, ''),
                        COALESCE(active_campaign_id, ''),
                        COALESCE(current_ad_bid, 0.0),
                        updated_at
                    FROM products
                    """
                )
            )
            await conn.execute(text("DROP TABLE products"))
            await conn.execute(text("ALTER TABLE products_v2 RENAME TO products"))

    if "pending_actions" in existing_tables:
        pa_info = await conn.execute(text("PRAGMA table_info(pending_actions)"))
        pa_cols = {row[1] for row in pa_info.fetchall()}
        if "executed_at" not in pa_cols:
            await conn.execute(text("ALTER TABLE pending_actions ADD COLUMN executed_at DATETIME"))
        await conn.execute(
            text(
                "UPDATE pending_actions SET status = LOWER(status) "
                "WHERE status IN ('PENDING', 'EXECUTED', 'REJECTED')"
            )
        )


async def init_db() -> None:
    async with engine.begin() as conn:
        # sqlite3 legacy mode не начинает транзакцию для DDL автоматически.
        await conn.execute(text("BEGIN IMMEDIATE"))
        await _migrate_legacy_sqlite_schema(conn)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_marketplace ON products (marketplace)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_external_id ON products (external_id)"))


async def close_db() -> None:
    """Корректно закрывает движок БД и освобождает пул соединений при завершении процесса."""
    await engine.dispose()
```

### `database/crud.py`

```python
import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database.models import (
    AdSpendRecord,
    AsyncSessionLocal,
    FirstMileSupply,
    PendingAction,
    Product,
    SaleRecord,
    get_db_session,
    utcnow_naive,
)
from products_catalog import get_catalog_product, resolve_catalog_external_id

# Сериализация конкурентных транзакций записи в SQLite внутри одного процесса воркера
_db_write_lock = asyncio.Lock()


def _resolve_ext_id_and_title(
    sku: str,
    marketplace: str,
    external_id: str = "",
    title: str = "",
) -> Tuple[str, str]:
    """Использует переданный product_id Ozon или известный nmID WB, не путая Ozon SKU с product_id."""
    clean_sku = str(sku or "").strip()
    mp = str(marketplace or "").strip().lower()
    clean_ext = str(external_id or "").strip()
    clean_title = str(title or "").strip()

    cat_meta = get_catalog_product(clean_sku) or (get_catalog_product(clean_ext) if clean_ext else None)
    cat_ext = resolve_catalog_external_id(clean_sku, mp)

    if mp == "wb":
        # Для Wildberries external_id должен быть числовым nmID, а не строковым артикулом продавца
        if (not clean_ext or clean_ext == clean_sku or not clean_ext.isdigit()) and cat_ext:
            clean_ext = cat_ext
    else:
        if (not clean_ext or clean_ext == clean_sku) and cat_ext:
            clean_ext = cat_ext

    if not clean_title and cat_meta:
        clean_title = str(cat_meta.get("title") or cat_meta.get("name") or "")

    return clean_ext, clean_title


async def upsert_product_costs(
    sku: str,
    marketplace: str,
    cost_price: float,
    packaging_cost: float,
    external_id: str = "",
) -> Product:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp, external_id=external_id)

    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()

            if not product:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    cost_price=float(cost_price),
                    packaging_cost=float(packaging_cost),
                )
                session.add(product)
            else:
                product.cost_price = float(cost_price)
                product.packaging_cost = float(packaging_cost)
                if external_id or not product.external_id or product.external_id == clean_sku:
                    product.external_id = resolved_ext
                if not product.title and resolved_title:
                    product.title = resolved_title
                product.updated_at = utcnow_naive()

            await session.commit()
            return product


async def record_first_mile_supply(
    supply_number: str,
    marketplace: str,
    sku: str,
    quantity: int,
    total_transport_cost: float,
) -> FirstMileSupply:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    safe_qty = max(int(quantity), 1)
    unit_cost = round(float(total_transport_cost) / safe_qty, 2)
    resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp)

    async with _db_write_lock:
        async with get_db_session() as session:
            supply = FirstMileSupply(
                supply_number=str(supply_number).strip(),
                marketplace=mp,
                sku=clean_sku,
                quantity=safe_qty,
                total_transport_cost=float(total_transport_cost),
                unit_cost=unit_cost,
            )
            session.add(supply)

            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()
            if product:
                product.first_mile_unit_cost = unit_cost
                if not product.external_id or product.external_id == clean_sku:
                    product.external_id = resolved_ext
                if not product.title and resolved_title:
                    product.title = resolved_title
                product.updated_at = utcnow_naive()
            else:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    first_mile_unit_cost=unit_cost,
                )
                session.add(product)

            await session.commit()
            return supply


async def update_product_market_state(
    sku: str,
    marketplace: str,
    external_id: str,
    current_price: float,
    stock_fbo: int,
    in_promo: bool = False,
    promo_max_price: float = 0.0,
    promo_id: str = "",
    avg_daily_sales: Optional[float] = None,
    stock_fbs: int = 0,
    commission_rate: Optional[float] = None,
    fbo_logistics_cost: Optional[float] = None,
    title: str = "",
) -> Product:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    resolved_ext, resolved_title = _resolve_ext_id_and_title(
        clean_sku, mp, external_id=str(external_id or ""), title=title
    )

    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()

            if not product:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    current_price=float(current_price or 0.0),
                    stock_fbo=int(stock_fbo or 0),
                    stock_fbs=int(stock_fbs or 0),
                    in_promo=bool(in_promo),
                    promo_max_price=float(promo_max_price or 0.0),
                    promo_id=str(promo_id or ""),
                )
                if avg_daily_sales is not None:
                    product.avg_daily_sales = float(avg_daily_sales)
                if commission_rate is not None and commission_rate > 0:
                    product.commission_rate = float(commission_rate)
                if fbo_logistics_cost is not None and fbo_logistics_cost > 0:
                    product.fbo_logistics_cost = float(fbo_logistics_cost)
                session.add(product)
            else:
                product.external_id = resolved_ext
                if resolved_title and not product.title:
                    product.title = resolved_title
                product.current_price = float(current_price or 0.0)
                product.stock_fbo = int(stock_fbo or 0)
                product.stock_fbs = int(stock_fbs or 0)
                product.in_promo = bool(in_promo)
                product.promo_max_price = float(promo_max_price or 0.0)
                product.promo_id = str(promo_id or "")
                if avg_daily_sales is not None:
                    product.avg_daily_sales = float(avg_daily_sales)
                if commission_rate is not None and commission_rate > 0:
                    product.commission_rate = float(commission_rate)
                if fbo_logistics_cost is not None and fbo_logistics_cost > 0:
                    product.fbo_logistics_cost = float(fbo_logistics_cost)
                product.updated_at = utcnow_naive()

            await session.commit()
            return product


async def bulk_update_product_market_state(
    marketplace: str,
    items: Sequence[Dict[str, Any]],
) -> int:
    """Пакетное обновление состояния карточек маркетплейса за одну транзакцию без утечек сессий."""
    if not items:
        return 0

    mp = str(marketplace).strip().lower()
    updated_count = 0
    now = utcnow_naive()

    async with _db_write_lock:
        async with get_db_session() as session:
            existing_res = await session.execute(select(Product).where(Product.marketplace == mp))
            existing_by_sku: Dict[str, Product] = {p.sku: p for p in existing_res.scalars().all()}

            for item in items:
                clean_sku = str(item.get("sku") or "").strip()
                if not clean_sku:
                    continue

                raw_ext = str(
                    item.get("nm_id") if mp == "wb" and item.get("nm_id") else (item.get("external_id") or "")
                )
                resolved_ext, resolved_title = _resolve_ext_id_and_title(
                    clean_sku,
                    mp,
                    external_id=raw_ext,
                    title=str(item.get("title") or ""),
                )

                cur_price = float(item.get("current_price") or 0.0)
                st_fbo = int(item.get("stock_fbo") or 0)
                st_fbs = int(item.get("stock_fbs") or 0)
                in_promo = bool(item.get("in_promo", False))
                promo_max = float(item.get("promo_max_price") or 0.0)
                promo_id = str(item.get("promo_id") or item.get("promo_names") or "")[:64]
                daily_sales = item.get("daily_sales")
                comm_rate = item.get("commission_rate")
                fbo_log = item.get("fbo_logistics_cost")

                product = existing_by_sku.get(clean_sku)
                if not product:
                    product = Product(
                        sku=clean_sku,
                        marketplace=mp,
                        external_id=resolved_ext,
                        title=resolved_title,
                        current_price=cur_price,
                        stock_fbo=st_fbo,
                        stock_fbs=st_fbs,
                        in_promo=in_promo,
                        promo_max_price=promo_max,
                        promo_id=promo_id,
                        updated_at=now,
                    )
                    if daily_sales is not None:
                        product.avg_daily_sales = float(daily_sales)
                    if comm_rate is not None and float(comm_rate) > 0:
                        product.commission_rate = float(comm_rate)
                    if fbo_log is not None and float(fbo_log) > 0:
                        product.fbo_logistics_cost = float(fbo_log)
                    session.add(product)
                    existing_by_sku[clean_sku] = product
                else:
                    product.external_id = resolved_ext
                    if resolved_title and not product.title:
                        product.title = resolved_title
                    product.current_price = cur_price
                    product.stock_fbo = st_fbo
                    product.stock_fbs = st_fbs
                    product.in_promo = in_promo
                    product.promo_max_price = promo_max
                    product.promo_id = promo_id
                    if daily_sales is not None:
                        product.avg_daily_sales = float(daily_sales)
                    if comm_rate is not None and float(comm_rate) > 0:
                        product.commission_rate = float(comm_rate)
                    if fbo_log is not None and float(fbo_log) > 0:
                        product.fbo_logistics_cost = float(fbo_log)
                    product.updated_at = now

                updated_count += 1

            await session.commit()
    return updated_count


async def get_all_products(marketplace: Optional[str] = None) -> Sequence[Product]:
    async with get_db_session() as session:
        stmt = select(Product)
        if marketplace:
            stmt = stmt.where(Product.marketplace == marketplace.strip().lower())
        res = await session.execute(stmt)
        return res.scalars().all()


async def get_lagged_drr(
    sku: str,
    shift_days_start: int = 7,
    shift_days_end: int = 3,
    marketplace: Optional[str] = None,
) -> float:
    """Расчет ДРР со сдвигом на 3-7 дней назад для учета окна атрибуции."""
    now = utcnow_naive()
    date_from = now - timedelta(days=shift_days_start)
    date_to = now - timedelta(days=shift_days_end)
    clean_sku = str(sku).strip()

    async with get_db_session() as session:
        sales_stmt = select(func.sum(SaleRecord.payout_amount)).where(
            SaleRecord.sku == clean_sku,
            SaleRecord.is_return.is_(False),
            SaleRecord.sale_date >= date_from,
            SaleRecord.sale_date <= date_to,
        )
        ad_stmt = select(func.sum(AdSpendRecord.spend_amount)).where(
            AdSpendRecord.sku == clean_sku,
            AdSpendRecord.spend_date >= date_from,
            AdSpendRecord.spend_date <= date_to,
        )
        if marketplace:
            mp = marketplace.strip().lower()
            sales_stmt = sales_stmt.where(SaleRecord.marketplace == mp)
            ad_stmt = ad_stmt.where(AdSpendRecord.marketplace == mp)

        total_sales = float((await session.execute(sales_stmt)).scalar() or 0.0)
        total_ads = float((await session.execute(ad_stmt)).scalar() or 0.0)

        if total_sales <= 0:
            return 0.05  # Базовый консервативный ДРР 5%, если не было продаж
        return min(round(total_ads / total_sales, 4), 0.50)


async def create_pending_action(
    marketplace: str,
    sku: str,
    action_type: str,
    payload: dict,
) -> PendingAction:
    async with _db_write_lock:
        async with get_db_session() as session:
            action = PendingAction(
                marketplace=str(marketplace).strip().lower(),
                sku=str(sku).strip(),
                action_type=str(action_type).strip(),
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            session.add(action)
            await session.commit()
            return action


async def get_pending_action(action_id: int) -> Optional[PendingAction]:
    """Получает отложенное действие по ID."""
    async with get_db_session() as session:
        stmt = select(PendingAction).where(PendingAction.id == int(action_id))
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def mark_action_executed(action_id: int) -> bool:
    """Переводит действие в статус executed."""
    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(PendingAction).where(PendingAction.id == int(action_id))
            res = await session.execute(stmt)
            action = res.scalar_one_or_none()
            if action:
                action.status = "executed"
                action.executed_at = utcnow_naive()
                await session.commit()
                return True
            return False


async def mark_action_rejected(action_id: int) -> bool:
    """Переводит действие в статус rejected."""
    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(PendingAction).where(PendingAction.id == int(action_id))
            res = await session.execute(stmt)
            action = res.scalar_one_or_none()
            if action:
                action.status = "rejected"
                action.executed_at = utcnow_naive()
                await session.commit()
                return True
            return False


async def add_sale_record(
    order_id: str,
    marketplace: str,
    sku: str,
    quantity: int,
    payout_amount: float,
    commission_amount: float,
    logistics_amount: float,
    is_return: bool,
    sale_date: datetime,
) -> None:
    clean_order_id = str(order_id).strip()
    if not clean_order_id:
        return

    async with _db_write_lock:
        async with get_db_session() as session:
            exists = await session.execute(
                select(SaleRecord.id).where(SaleRecord.order_id == clean_order_id)
            )
            if exists.scalar_one_or_none() is not None:
                return
            rec = SaleRecord(
                order_id=clean_order_id,
                marketplace=str(marketplace).strip().lower(),
                sku=str(sku).strip(),
                quantity=int(quantity),
                payout_amount=float(payout_amount),
                commission_amount=float(commission_amount),
                logistics_amount=float(logistics_amount),
                is_return=bool(is_return),
                sale_date=sale_date,
            )
            session.add(rec)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()


async def bulk_upsert_costs(records: list[dict]) -> int:
    """
    Пакетная загрузка себестоимости и упаковки с разделением по (sku, marketplace).
    Формат элементов: [{"marketplace": "ozon", "sku": "1001", "cost_price": 52.0, "packaging_cost": 15.0}, ...]
    """
    if not records:
        return 0

    count = 0
    now = utcnow_naive()

    async with _db_write_lock:
        async with get_db_session() as session:
            existing = {}
            for item in records:
                clean_sku = str(item["sku"]).strip()
                mp = str(item["marketplace"]).strip().lower()
                resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp)

                key = (clean_sku, mp)
                if key not in existing:
                    stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
                    res = await session.execute(stmt)
                    existing[key] = res.scalar_one_or_none()
                product = existing[key]

                if not product:
                    product = Product(
                        sku=clean_sku,
                        marketplace=mp,
                        external_id=resolved_ext,
                        title=resolved_title,
                        cost_price=float(item["cost_price"]),
                        packaging_cost=float(item["packaging_cost"]),
                        updated_at=now,
                    )
                    session.add(product)
                    existing[key] = product
                else:
                    product.cost_price = float(item["cost_price"])
                    product.packaging_cost = float(item["packaging_cost"])
                    if not product.external_id or product.external_id == clean_sku:
                        product.external_id = resolved_ext
                    if not product.title and resolved_title:
                        product.title = resolved_title
                    product.updated_at = now
                count += 1

            await session.commit()
    return count
```

### `supervisor.py`

```python
import ast
import atexit
import json
import os
import py_compile
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import BACKUPS_DIR, BASE_DIR, LOGS_DIR, settings


def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог-файл при скрытом запуске через pythonw.exe (start_hidden.vbs)."""
    log_path = LOGS_DIR / "supervisor.log"
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)


_ensure_stdio_streams()

PATCH_REQUEST_FILE = LOGS_DIR / "patch_request.json"
ROUTE_CACHE_FILE = LOGS_DIR / "proxy_route_cache.json"
TEMP_PATCH_FILE = BASE_DIR / "temp_patch.py"
HEARTBEAT_TIMEOUT_SEC = 90
POST_PATCH_VERIFY_SEC = 45
TELEGRAM_POLLING_COOLDOWN_SEC = 2.5

ALLOWED_TARGET_FILES: Dict[str, Path] = {
    "config.py": BASE_DIR / "config.py",
    "products_catalog.py": BASE_DIR / "products_catalog.py",
    "worker.py": BASE_DIR / "worker.py",
    "models.py": BASE_DIR / "database" / "models.py",
    "crud.py": BASE_DIR / "database" / "crud.py",
    "seller_api.py": BASE_DIR / "services" / "ozon" / "seller_api.py",
    "browser_reviews.py": BASE_DIR / "services" / "ozon" / "browser_reviews.py",
    "statistics_api.py": BASE_DIR / "services" / "wb" / "statistics_api.py",
    "price_auditor.py": BASE_DIR / "services" / "common" / "price_auditor.py",
    "unit_economics.py": BASE_DIR / "services" / "common" / "unit_economics.py",
    "orders_report.py": BASE_DIR / "services" / "common" / "orders_report.py",
    "financial_pnl.py": BASE_DIR / "services" / "common" / "financial_pnl.py",
    "ad_recommendations.py": BASE_DIR / "services" / "common" / "ad_recommendations.py",
    "buyer_questions.py": BASE_DIR / "services" / "common" / "buyer_questions.py",
    "ai_advisory_board.py": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
    "deep_parser.py": BASE_DIR / "services" / "competitors" / "deep_parser.py",
    "api_inspector.py": BASE_DIR / "maintenance" / "api_inspector.py",
    "self_healer.py": BASE_DIR / "maintenance" / "self_healer.py",
    "keyboards.py": BASE_DIR / "bot" / "keyboards.py",
    "callbacks.py": BASE_DIR / "bot" / "handlers" / "callbacks.py",
    "costs_fsm.py": BASE_DIR / "bot" / "handlers" / "costs_fsm.py",
    "logistics_fsm.py": BASE_DIR / "bot" / "handlers" / "logistics_fsm.py",
    "patch_fsm.py": BASE_DIR / "bot" / "handlers" / "patch_fsm.py",
    "questions_fsm.py": BASE_DIR / "bot" / "handlers" / "questions_fsm.py",
    "reviews_fsm.py": BASE_DIR / "bot" / "handlers" / "reviews_fsm.py",
}

_CACHED_ROUTE_NAME: Optional[str] = None


def _get_route_candidates() -> List[Tuple[str, urllib.request.OpenerDirector]]:
    candidates: List[Tuple[str, urllib.request.OpenerDirector]] = []
    proxy_url = (settings.LOCAL_PROXY_URL or "").strip()
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        candidates.append(("local_proxy", urllib.request.build_opener(proxy_handler)))
    return candidates


def _load_cached_route_name() -> Optional[str]:
    global _CACHED_ROUTE_NAME
    if _CACHED_ROUTE_NAME:
        return _CACHED_ROUTE_NAME
    try:
        if ROUTE_CACHE_FILE.exists():
            data = json.loads(ROUTE_CACHE_FILE.read_text(encoding="utf-8"))
            route = data.get("route")
            if isinstance(route, str) and route:
                _CACHED_ROUTE_NAME = route
                return route
    except Exception:
        pass
    return None


def _save_cached_route_name(route_name: str) -> None:
    global _CACHED_ROUTE_NAME
    _CACHED_ROUTE_NAME = route_name
    try:
        ROUTE_CACHE_FILE.write_text(
            json.dumps({"route": route_name, "updated_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _open_via_cached_route(req: urllib.request.Request, timeout: int = 30) -> bytes:
    """Выполняет запрос к Telegram/Gemini через локальный прокси с кэшированием рабочего маршрута."""
    candidates = _get_route_candidates()
    cached_name = _load_cached_route_name()
    if cached_name:
        candidates.sort(key=lambda item: 0 if item[0] == cached_name else 1)

    last_exc: Optional[Exception] = None
    for route_name, opener in candidates:
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                _save_cached_route_name(route_name)
                return raw
        except urllib.error.HTTPError as http_err:
            # HTTP-ответ получен — значит сетевой маршрут рабочий, пробрасываем код ответа вызывающему коду
            _save_cached_route_name(route_name)
            raise http_err
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(f"Все сетевые маршруты недоступны: {last_exc}")


def send_telegram_sync(text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage"
    data = json.dumps(
        {"chat_id": settings.TG_ADMIN_ID, "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        _open_via_cached_route(req, timeout=12)
    except Exception as exc:
        print(f"[Supervisor] Ошибка отправки TG уведомления: {exc}", flush=True)


def resolve_target_file(instruction: str) -> Path:
    clean_instr = instruction.strip()
    first_token = clean_instr.split(maxsplit=1)[0].strip().replace("\\", "/") if clean_instr else ""
    if first_token.endswith(".py"):
        candidate_rel = (BASE_DIR / first_token).resolve()
        if candidate_rel.exists() and candidate_rel.is_file() and BASE_DIR in candidate_rel.parents:
            return candidate_rel

    lower_instr = clean_instr.lower()
    for fname, path in ALLOWED_TARGET_FILES.items():
        if fname.lower() in lower_instr:
            return path

    if any(k in lower_instr for k in ("отзыв", "вопрос", "playwright", "шторк")):
        return ALLOWED_TARGET_FILES["browser_reviews.py"]
    if any(k in lower_instr for k in ("аудит цен", "52%", "299", "300")):
        return ALLOWED_TARGET_FILES["price_auditor.py"]
    if any(k in lower_instr for k in ("реклам", "кампан", "ддр", "дрр", "трафарет")):
        return ALLOWED_TARGET_FILES["ad_recommendations.py"]
    if "ozon" in lower_instr:
        return ALLOWED_TARGET_FILES["seller_api.py"]
    if "wb" in lower_instr or "wildberries" in lower_instr:
        return ALLOWED_TARGET_FILES["statistics_api.py"]
    if "цен" in lower_instr or "floor" in lower_instr or "тариф" in lower_instr:
        return ALLOWED_TARGET_FILES["unit_economics.py"]
    if "отчет" in lower_instr or "pnl" in lower_instr or "дебет" in lower_instr:
        return ALLOWED_TARGET_FILES["financial_pnl.py"]
    return ALLOWED_TARGET_FILES["worker.py"]


def _discover_gemini_text_models(api_key: str, primary_model: str) -> List[str]:
    excluded_tokens = ("tts", "audio", "imagen", "embedding", "aqa", "vision")
    discovered: List[str] = []
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        req = urllib.request.Request(list_url, method="GET")
        raw = _open_via_cached_route(req, timeout=10)
        body = json.loads(raw.decode("utf-8"))
        for item in body.get("models") or []:
            methods = item.get("supportedGenerationMethods") or []
            name = str(item.get("name") or "").replace("models/", "").strip()
            if "generateContent" in methods and name and not any(tok in name.lower() for tok in excluded_tokens):
                discovered.append(name)
    except Exception:
        pass

    fallbacks = [
        primary_model,
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ]
    ordered = [m for m in [primary_model, *discovered, *fallbacks] if m and "tts" not in m.lower()]
    return list(dict.fromkeys(ordered))


def _strip_code_fences(raw_text: str) -> str:
    code = raw_text.strip()
    if "```" in code:
        blocks = code.split("```")
        for block in blocks:
            stripped = block.strip()
            if stripped.startswith("python"):
                return stripped[len("python"):].strip()
        for block in blocks:
            stripped = block.strip()
            if "import " in stripped or "def " in stripped or "class " in stripped:
                return stripped
    return code


def request_gemini_patch(instruction: str, target_file_path: Path, current_code: str) -> str:
    """Запрос на генерацию патча к Gemini API с кэшированием прокси-маршрута и перебором текстовых моделей."""
    raw_model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"
    primary_model = raw_model.strip().replace("models/", "")
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY не задан в .env")

    models_to_try = _discover_gemini_text_models(api_key, primary_model)

    prompt = (
        f"Файл: {target_file_path.name}\n"
        f"Задача: {instruction}\n\n"
        f"Текущий исходный код:\n```python\n{current_code}\n```\n\n"
        "Верни ПОЛНЫЙ рабочий валидный Python-код файла целиком без пропусков и без пояснений."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 16384},
    }
    req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error = "Нет доступных моделей"
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        try:
            raw_resp = _open_via_cached_route(req, timeout=90)
            body = json.loads(raw_resp.decode("utf-8"))
            candidates = body.get("candidates") or []
            if not candidates:
                continue
            parts = candidates[0].get("content", {}).get("parts") or []
            text_out = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
            clean_code = _strip_code_fences(text_out)
            if clean_code:
                return clean_code
        except urllib.error.HTTPError as http_err:
            last_error = f"{model}: HTTP {http_err.code}"
            if http_err.code in (400, 404, 429, 500, 502, 503, 504):
                continue
            raise
        except Exception as exc:
            last_error = f"{model}: {exc}"
            continue

    raise RuntimeError(f"Все доступные модели Gemini временно перегружены или недоступны ({last_error}).")


def _is_local_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _kill_pid_tree(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=8,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


class WorkerSupervisor:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self._lock_socket: Optional[socket.socket] = None
        self._stopping: bool = False

    def acquire_single_instance_lock(self) -> bool:
        """Гарантирует единственный экземпляр supervisor.py в ОС (защита от TelegramConflictError в start_hidden.vbs)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE"), 1)
        try:
            sock.bind(("127.0.0.1", settings.SUPERVISOR_LOCK_PORT))
            sock.listen(1)
        except OSError:
            sock.close()
            return False

        self._lock_socket = sock
        try:
            settings.SUPERVISOR_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
        return True

    def release_single_instance_lock(self) -> None:
        if self._lock_socket is not None:
            try:
                self._lock_socket.close()
            except Exception:
                pass
            self._lock_socket = None
        try:
            if settings.SUPERVISOR_PID_FILE.exists():
                settings.SUPERVISOR_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def cleanup_orphan_worker(self) -> None:
        """Гасит осиротевший worker.py от прошлых сессий и ждет освобождения его сокета перед новым polling."""
        if not _is_local_port_in_use(settings.WORKER_LOCK_PORT) and not settings.WORKER_PID_FILE.exists():
            return

        # PID-файл может устареть и указывать на посторонний процесс. Нельзя
        # завершать его без достоверной проверки владельца и времени запуска.
        if _is_local_port_in_use(settings.WORKER_LOCK_PORT):
            raise RuntimeError("Порт worker занят: остановите старый worker вручную")
        settings.WORKER_PID_FILE.unlink(missing_ok=True)

    def start_worker(self) -> None:
        self.stop_worker()
        self.cleanup_orphan_worker()
        self.write_fresh_heartbeat()

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
            creationflags=creation_flags,
        )
        print(f"[Supervisor] Запущен worker.py (PID={self.process.pid})", flush=True)

    def stop_worker(self) -> None:
        was_running = False
        if self.process is not None:
            pid = self.process.pid
            if self.process.poll() is None:
                was_running = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    _kill_pid_tree(pid)
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            self.process = None

        deadline = time.time() + 8.0
        while time.time() < deadline and _is_local_port_in_use(settings.WORKER_LOCK_PORT):
            time.sleep(0.4)

        if was_running:
            # Пауза для гарантированного закрытия старого getUpdates long-polling на сервере Telegram
            time.sleep(TELEGRAM_POLLING_COOLDOWN_SEC)

    def write_fresh_heartbeat(self) -> None:
        settings.HEARTBEAT_FILE.write_text(
            json.dumps({"timestamp": time.time(), "status": "starting"}),
            encoding="utf-8",
        )

    def get_heartbeat_age(self) -> float:
        if not settings.HEARTBEAT_FILE.exists():
            return 999999.0
        try:
            data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
            return time.time() - float(data.get("timestamp", 0.0))
        except Exception:
            return 999999.0

    def is_worker_healthy(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        return self.get_heartbeat_age() <= HEARTBEAT_TIMEOUT_SEC

    def apply_patch_safely(self, instruction: str) -> None:
        target_file = resolve_target_file(instruction)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUPS_DIR / f"{target_file.name}.{timestamp}.bak"

        shutil.copy2(target_file, backup_path)
        current_code = target_file.read_text(encoding="utf-8")

        try:
            patched_code = request_gemini_patch(
                instruction=instruction,
                target_file_path=target_file,
                current_code=current_code,
            )
            if not patched_code:
                raise RuntimeError("Пустой ответ от модели")
            ast.parse(patched_code, filename=str(target_file))
            TEMP_PATCH_FILE.write_text(patched_code, encoding="utf-8")
        except Exception as exc:
            TEMP_PATCH_FILE.unlink(missing_ok=True)
            send_telegram_sync(f"❌ Ошибка при генерации патча LLM: <code>{exc}</code>. Патч отменен.")
            return

        compile_res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(TEMP_PATCH_FILE)],
            capture_output=True,
            text=True,
        )
        if compile_res.returncode != 0:
            TEMP_PATCH_FILE.unlink(missing_ok=True)
            send_telegram_sync(
                "❌ Ошибка компиляции при проверке патча. Патч отменен, бот продолжает работу на прежней версии."
            )
            return

        shutil.move(str(TEMP_PATCH_FILE), str(target_file))
        pre_restart_ts = time.time()
        self.stop_worker()
        settings.HEARTBEAT_FILE.unlink(missing_ok=True)

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
            creationflags=creation_flags,
        )

        deadline = time.time() + POST_PATCH_VERIFY_SEC
        heartbeat_updated = False
        while time.time() < deadline:
            time.sleep(2)
            if self.process.poll() is not None:
                break
            if settings.HEARTBEAT_FILE.exists():
                try:
                    hb_data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
                    if float(hb_data.get("timestamp", 0.0)) > pre_restart_ts and hb_data.get("status") != "starting":
                        heartbeat_updated = True
                        break
                except Exception:
                    pass

        if heartbeat_updated:
            send_telegram_sync(
                f"✅ Патч успешно применен к файлу <code>{target_file.name}</code>.\n"
                f"Резервная копия: <code>{backup_path.name}</code>."
            )
        else:
            self.stop_worker()
            shutil.copy2(backup_path, target_file)
            self.start_worker()
            send_telegram_sync(
                f"⚠️ Воркер не подтвердил запуск после патча <code>{target_file.name}</code>. "
                f"Выполнен автоматический откат на <code>{backup_path.name}</code>."
            )

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        print("[Supervisor] Корректное завершение работы...", flush=True)
        self.stop_worker()
        self.release_single_instance_lock()

    def run_forever(self) -> None:
        if not self.acquire_single_instance_lock():
            print(
                f"[Supervisor] Другой экземпляр supervisor.py уже удерживает порт {settings.SUPERVISOR_LOCK_PORT}. "
                "Повторный запуск отменен для защиты от TelegramConflictError.",
                flush=True,
            )
            return

        atexit.register(self.shutdown)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: sys.exit(0))
            except Exception:
                pass

        try:
            self.start_worker()
            while not self._stopping:
                time.sleep(5)

                if PATCH_REQUEST_FILE.exists():
                    try:
                        req_data = json.loads(PATCH_REQUEST_FILE.read_text(encoding="utf-8"))
                        PATCH_REQUEST_FILE.unlink(missing_ok=True)
                        instruction = str(req_data.get("instruction") or "").strip()
                        if instruction:
                            self.apply_patch_safely(instruction)
                    except Exception as exc:
                        print(f"[Supervisor] Ошибка чтения запроса патча: {exc}", flush=True)

                if settings.RESTART_SIGNAL_FILE.exists():
                    settings.RESTART_SIGNAL_FILE.unlink(missing_ok=True)
                    print("[Supervisor] Получен сигнал перезапуска worker.py.", flush=True)
                    self.start_worker()

                if not self.is_worker_healthy():
                    print("[Supervisor] Воркер не отвечает или завершился. Перезапуск...", flush=True)
                    self.start_worker()
        finally:
            self.shutdown()


if __name__ == "__main__":
    supervisor = WorkerSupervisor()
    supervisor.run_forever()
```

### `start_hidden.vbs`

```vb
Option Explicit

Dim objWMIService, colProcesses, WshShell
Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMIService.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE (Name = 'python.exe' OR Name = 'pythonw.exe') AND CommandLine LIKE '%supervisor.py%'")

If colProcesses.Count > 0 Then
    WScript.Quit 0
End If

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\marketplace_agent"
WshShell.Run """C:\marketplace_agent\venv\Scripts\pythonw.exe"" ""C:\marketplace_agent\supervisor.py""", 0, False
```

### `worker.py`

```python
import asyncio
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import LOGS_DIR, settings
from database.crud import bulk_update_product_market_state
from database.models import close_db, init_db
from maintenance.api_inspector import run_monthly_api_audit
from maintenance.self_healer import run_full_self_check_and_heal
from services.common.ad_recommendations import generate_weekly_ads_report_and_recommendations
from services.common.ai_advisory_board import generate_monday_ai_board_report
from services.common.financial_pnl import (
    generate_monthly_pnl_report,
    generate_weekly_top_and_unprofitable_report,
    get_previous_month_bounds,
)
from services.common.orders_report import sync_and_generate_orders_report
from services.common.price_auditor import run_daily_price_audit
from services.ozon.browser_reviews import (
    auto_reply_unanswered_questions,
    auto_reply_unanswered_reviews,
)
from services.ozon.seller_api import CircuitBreakerOpenError, OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

from bot.handlers.callbacks import callbacks_router
from bot.handlers.costs_fsm import cmd_costs_start, costs_router
from bot.handlers.logistics_fsm import cmd_first_mile_start, logistics_router
from bot.handlers.patch_fsm import patch_router
from bot.handlers.questions_fsm import questions_router
from bot.handlers.reviews_fsm import reviews_router
from bot.keyboards import get_main_menu_keyboard


def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог при скрытом запуске через pythonw.exe (start_hidden.vbs)."""
    log_path = LOGS_DIR / "worker.log"
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)


_ensure_stdio_streams()

PATCH_REQUEST_FILE = LOGS_DIR / "patch_request.json"
TG_ROUTE_CACHE_FILE = LOGS_DIR / "tg_route_cache.json"
MSK_TZ = ZoneInfo(settings.TIMEZONE)

_WORKER_LOCK_SOCKET: Optional[socket.socket] = None
_BROWSER_JOB_LOCK = asyncio.Lock()
_PRICE_AUDIT_LOCK = asyncio.Lock()


def _load_cached_tg_proxy() -> Optional[str]:
    """Читает последний проверенный рабочий маршрут к Telegram API."""
    if TG_ROUTE_CACHE_FILE.exists():
        try:
            data = json.loads(TG_ROUTE_CACHE_FILE.read_text(encoding="utf-8"))
            if "proxy" in data:
                val = data["proxy"]
                if val and str(val).strip() == settings.LOCAL_PROXY_URL:
                    return str(val).strip()
        except Exception:
            pass
    return settings.LOCAL_PROXY_URL or None


def _save_cached_tg_proxy(proxy_url: Optional[str]) -> None:
    try:
        TG_ROUTE_CACHE_FILE.write_text(
            json.dumps({"proxy": proxy_url, "updated_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _make_tg_session(proxy_url: Optional[str], timeout: float = 60.0) -> AiohttpSession:
    if not proxy_url:
        raise ValueError("Для Telegram необходим LOCAL_PROXY_URL")
    return AiohttpSession(proxy=proxy_url, timeout=timeout)


_initial_proxy = _load_cached_tg_proxy()
bot = Bot(token=settings.TG_BOT_TOKEN, session=_make_tg_session(_initial_proxy))
dp = Dispatcher()

dp.include_router(costs_router)
dp.include_router(logistics_router)
dp.include_router(callbacks_router)
dp.include_router(patch_router)
dp.include_router(reviews_router)
dp.include_router(questions_router)


async def ensure_working_telegram_route() -> None:
    """Проверяет соединение с Telegram через локальный прокси с кэшированием рабочего маршрута и закрытием тестовых сессий."""
    global bot
    try:
        await bot.get_me()
        _save_cached_tg_proxy(_initial_proxy)
        return
    except Exception as first_exc:
        print(f"[Worker] Первичный маршрут TG ({_initial_proxy or 'direct'}) недоступен: {first_exc}", flush=True)

    candidate_proxies: List[Optional[str]] = []
    for cand in (settings.LOCAL_PROXY_URL,):
        if cand != _initial_proxy and cand not in candidate_proxies:
            candidate_proxies.append(cand)

    for cand_proxy in candidate_proxies:
        test_session = _make_tg_session(cand_proxy, timeout=15.0)
        test_bot = Bot(token=settings.TG_BOT_TOKEN, session=test_session)
        try:
            await test_bot.get_me()
        except Exception:
            await test_bot.session.close()
            continue
        else:
            await test_bot.session.close()
            old_session = bot.session
            bot = Bot(token=settings.TG_BOT_TOKEN, session=_make_tg_session(cand_proxy))
            await old_session.close()
            _save_cached_tg_proxy(cand_proxy)
            print(f"[Worker] Активирован и закэширован маршрут TG: {cand_proxy or 'direct'}", flush=True)
            return

    raise RuntimeError("Telegram недоступен через настроенный локальный прокси")


def _split_html_chunks(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current_lines: List[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > limit and current_lines:
            chunks.append("".join(current_lines))
            current_lines = [line]
            current_len = len(line)
        else:
            current_lines.append(line)
            current_len += len(line)
    if current_lines:
        chunks.append("".join(current_lines))
    return chunks


async def send_admin_html(text: str, disable_notification: bool = False) -> None:
    for chunk in _split_html_chunks(text):
        await bot.send_message(
            chat_id=settings.TG_ADMIN_ID,
            text=chunk,
            parse_mode="HTML",
            disable_notification=disable_notification,
        )


async def notify_admin(text: str) -> None:
    try:
        await send_admin_html(text, disable_notification=False)
    except Exception as exc:
        print(f"[Worker] Ошибка отправки сообщения в TG: {exc}", flush=True)


ozon_service = OzonSellerAPI(notify_callback=notify_admin)
wb_service = WBStatisticsAPI(notify_callback=notify_admin)


def _write_heartbeat_sync(payload_str: str) -> None:
    tmp_file = settings.HEARTBEAT_FILE.with_suffix(".tmp")
    tmp_file.write_text(payload_str, encoding="utf-8")
    tmp_file.replace(settings.HEARTBEAT_FILE)


async def write_heartbeat() -> None:
    payload = {
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "pid": os.getpid(),
        "ozon_degraded": ozon_service.is_degraded,
        "wb_degraded": wb_service.is_degraded,
    }
    try:
        await asyncio.to_thread(_write_heartbeat_sync, json.dumps(payload))
    except Exception as exc:
        print(f"[Worker] Ошибка записи heartbeat: {exc}", flush=True)


async def _heartbeat_loop() -> None:
    while True:
        await write_heartbeat()
        await asyncio.sleep(10)


def acquire_worker_single_instance_lock() -> bool:
    """Захватывает эксклюзивный TCP-сокет, исключая параллельный запуск двух worker.py и TelegramConflictError."""
    global _WORKER_LOCK_SOCKET
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE"), 1)
    try:
        sock.bind(("127.0.0.1", settings.WORKER_LOCK_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False

    _WORKER_LOCK_SOCKET = sock
    try:
        settings.WORKER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    return True


def release_worker_single_instance_lock() -> None:
    global _WORKER_LOCK_SOCKET
    if _WORKER_LOCK_SOCKET is not None:
        try:
            _WORKER_LOCK_SOCKET.close()
        except Exception:
            pass
        _WORKER_LOCK_SOCKET = None
    try:
        if settings.WORKER_PID_FILE.exists():
            settings.WORKER_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


async def sync_marketplace_silent(mp_name: str) -> List[Dict[str, Any]]:
    try:
        if mp_name == "ozon":
            items = await ozon_service.fetch_stocks_and_prices()
        else:
            items = await wb_service.fetch_stocks_and_prices()
    except CircuitBreakerOpenError:
        return []
    except Exception as exc:
        print(f"[Silent Sync {mp_name.upper()} Error]: {exc}", flush=True)
        return []

    if items:
        try:
            await bulk_update_product_market_state(marketplace=mp_name, items=items)
        except Exception as exc:
            print(f"[DB Sync {mp_name.upper()} Error]: {exc}", flush=True)
    return items


async def scheduled_marketplaces_poll() -> None:
    await asyncio.gather(
        sync_marketplace_silent("ozon"),
        sync_marketplace_silent("wb"),
        return_exceptions=True,
    )


async def scheduled_reviews_and_questions_job() -> None:
    """Запуск при старте и каждые 30 минут. До проверки фильтра даты — только dry-run."""
    if _BROWSER_JOB_LOCK.locked():
        print("[Worker] Предыдущий цикл Playwright еще выполняется, пропуск дублирующего запуска.", flush=True)
        return

    async with _BROWSER_JOB_LOCK:
        try:
            await auto_reply_unanswered_questions(headless=True, dry_run=True, max_per_run=15)
        except Exception as exc:
            print(f"[Worker] Ошибка автоответчика на вопросы Ozon: {exc}", flush=True)

        try:
            await auto_reply_unanswered_reviews(headless=True, dry_run=True, max_per_run=15)
        except Exception as exc:
            print(f"[Worker] Ошибка автоответчика на отзывы Ozon: {exc}", flush=True)


async def scheduled_daily_price_audit_job() -> None:
    """Ежедневный аудит цен Ozon (3 кнопки: порог 299 ₽, >300 ₽ с 52%, Отклонить) и WB (FBO+FBS по числовому nmID)."""
    if _PRICE_AUDIT_LOCK.locked():
        return
    async with _PRICE_AUDIT_LOCK:
        await scheduled_marketplaces_poll()
        await run_daily_price_audit(bot, settings.TG_ADMIN_ID, ozon_service, wb_service)


async def scheduled_weekly_ads_job() -> None:
    """Еженедельный отчет и рекомендации по внутренней рекламе Ozon (2 гибридные РК) и WB."""
    report = await generate_weekly_ads_report_and_recommendations(ozon_service, wb_service)
    await send_admin_html(f"📈 <b>Еженедельный аудит и план рекламы Ozon & WB</b>\n\n{report}")


async def scheduled_1st_day_pnl_notice() -> None:
    bounds = get_previous_month_bounds()
    await notify_admin(
        f"📅 <b>Финансовый календарь ({bounds['title']}):</b>\n"
        f"Ozon и Wildberries завершают формирование закрывающих актов и детализации начислений за {bounds['title']}.\n"
        f"Итоговый месячный P&L отчет со всеми опубликованными расходами придет автоматически <b>5-го числа в 09:30</b> "
        f"(либо вы можете запросить предварительный срез кнопкой «📊 P&L отчет (Месяц)»)."
    )


async def scheduled_5th_day_monthly_pnl() -> None:
    report_html = await generate_monthly_pnl_report(ozon_service, wb_service)
    await send_admin_html(report_html)


async def scheduled_monday_top5_report() -> None:
    report_html = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
    await send_admin_html(report_html)


async def scheduled_monday_evening_ai_board() -> None:
    summary_text, file_path = await generate_monday_ai_board_report(ozon_service, wb_service)
    await bot.send_message(chat_id=settings.TG_ADMIN_ID, text=summary_text)
    await bot.send_document(
        chat_id=settings.TG_ADMIN_ID,
        document=FSInputFile(str(file_path)),
        caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
    )


async def scheduled_monthly_audit() -> None:
    audit_html = await run_monthly_api_audit()
    await send_admin_html(audit_html)


async def scheduled_night_self_heal() -> None:
    """Тихая ночная самопроверка в 04:30. Присылает алерт только если были проблемы."""
    res = await run_full_self_check_and_heal()
    if res["total_issues"] > 0:
        details_txt = "\n".join(res["details"])
        await notify_admin(
            f"🛠 <b>[Автономная самопроверка 04:30]</b>\n"
            f"Обнаружено ошибок: <b>{res['total_issues']}</b>\n"
            f"Успешно исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}"
        )


# --- Хэндлеры текстовых команд и экранных кнопок ---


@dp.message(F.text.in_({"/start", "/menu"}))
async def cmd_start_menu(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await message.answer(
        "👋 Панель управления магазинами Ozon и Wildberries активна.\n"
        "Используйте кнопки меню внизу экрана для быстрого доступа:",
        reply_markup=get_main_menu_keyboard(),
    )


@dp.message(F.text.in_({"📊 P&L отчет (Месяц)", "📊 P&L отчет"}))
@dp.message(Command("pnl"))
async def cmd_manual_pnl(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Собираю финансовый P&L отчет по итогам прошлого месяца из Ozon и WB...")
    try:
        report = await generate_monthly_pnl_report(ozon_service, wb_service)
        await status_msg.delete()
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка P&L отчета:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "🏆 ТОП-5 и Убыточные")
@dp.message(Command("top5"))
async def cmd_top5_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Анализирую поштучную рентабельность товаров за 7 дней...")
    try:
        report = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка расчета ТОП-5:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "🧠 ИИ-Советник (Разбор)")
@dp.message(Command("ai_board"))
async def cmd_ai_board(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer(
        "🧠 Созываю Совет из 5 ИИ-специалистов (Операционист, Скептик, Маркетолог, Финансист, Директор по развитию)...\n"
        "<i>Анализ реальных отчетов Ozon и WB займет около 20–30 секунд.</i>",
        parse_mode="HTML",
    )
    try:
        summary_text, file_path = await generate_monday_ai_board_report(ozon_service, wb_service)
        await status_msg.delete()
        await message.answer(summary_text)
        await message.answer_document(
            document=FSInputFile(str(file_path)),
            caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
        )
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка ИИ-Советника:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "📦 Отчет по заказам")
@dp.message(Command("orders_pnl"))
async def cmd_orders_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Запрашиваю заказы и финансовые начисления из Ozon и WB за 7 дней...")
    try:
        report_text = await sync_and_generate_orders_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        for chunk in _split_html_chunks(report_text):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(
            f"❌ <b>Ошибка при формировании отчета:</b>\n<code>{type(exc).__name__}: {exc}</code>",
            parse_mode="HTML",
        )


@dp.message(F.text == "🔍 Аудит API")
@dp.message(Command("audit"))
async def cmd_manual_audit(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    audit = await run_monthly_api_audit()
    for chunk in _split_html_chunks(audit):
        await message.answer(chunk, parse_mode="HTML")


@dp.message(F.text == "📦 Себестоимость (поштучно)")
async def btn_costs_entry(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_costs_start(message, state)


@dp.message(F.text == "🚚 Первая миля")
async def btn_first_mile_entry(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_first_mile_start(message, state)


@dp.message(F.text == "🔄 Синхронизировать МП")
async def btn_sync_mp(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await message.answer("⏳ Запущена синхронизация остатков FBO/FBS и эталонный аудит цен Ozon & WB...")
    await scheduled_daily_price_audit_job()
    await message.answer("✅ Синхронизация остатков и аудит цен завершены.")


@dp.message(Command("patch"))
async def cmd_patch_cli(message: Message, command: CommandObject) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/patch [имя_файла] описание изменений</code>",
            parse_mode="HTML",
        )
        return

    PATCH_REQUEST_FILE.write_text(
        json.dumps({"instruction": command.args.strip()}, ensure_ascii=False),
        encoding="utf-8",
    )
    await message.answer(
        "🛠 Запрос передан в <code>supervisor.py</code>. "
        "Создается резервная копия, генерируется код и проверяется синтаксис...",
        parse_mode="HTML",
    )


@dp.message(F.text == "🔎 Пробив конкурентов")
@dp.message(Command("competitors"))
async def cmd_competitors_audit(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    wait_msg = await message.answer(
        "🔎 <b>Запущен сбор данных конкурентов WB и Ozon...</b>\n"
        "<i>Парсинг 20–30 лидеров, расчет юнит-экономики и аудит болей займет 15–20 секунд.</i>",
        parse_mode="HTML",
    )
    try:
        from services.competitors.deep_parser import run_full_competitor_deep_audit

        report_text, _ = await run_full_competitor_deep_audit("гель для моделирования ногтей 15г")
        await wait_msg.delete()
        for chunk in _split_html_chunks(report_text):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка парсинга конкурентов: <code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "🛠 Самопроверка и автопочинка")
@dp.message(Command("heal"))
async def cmd_manual_self_heal(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    wait_msg = await message.answer("🔍 Запущена комплексная самопроверка всех модулей системы...")
    try:
        res = await run_full_self_check_and_heal()
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка самодиагностики: <code>{exc}</code>", parse_mode="HTML")
        return

    await wait_msg.delete()
    if res["total_issues"] == 0:
        await message.answer(
            "✅ <b>Все модули работают исправно:</b> ошибок не обнаружено, вмешательство ИИ не требуется.",
            parse_mode="HTML",
        )
    else:
        details_txt = "\n".join(res["details"])
        await message.answer(
            f"🛠 <b>Результаты самодиагностики и починки:</b>\n\n"
            f"Обнаружено сбоев: <b>{res['total_issues']}</b>\n"
            f"Исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}",
            parse_mode="HTML",
        )


async def setup_bot_commands(bot_instance: Bot) -> None:
    commands = [
        BotCommand(command="menu", description="Главное меню с кнопками"),
        BotCommand(command="pnl", description="P&L отчет за прошлый месяц"),
        BotCommand(command="orders_pnl", description="Отчет по заказам за 7 дней"),
        BotCommand(command="top5", description="ТОП-5 популярных и убыточных товаров"),
        BotCommand(command="ai_board", description="Разбор магазина от ИИ-Совета"),
        BotCommand(command="competitors", description="Глубокий аудит конкурентов"),
        BotCommand(command="question", description="Ответить на вопрос покупателя"),
        BotCommand(command="review", description="Сгенерировать ответ на отзыв"),
        BotCommand(command="costs", description="Себестоимость товара (поштучно)"),
        BotCommand(command="bulk_costs", description="Массовая загрузка себестоимости"),
        BotCommand(command="first_mile", description="Затраты первой мили"),
        BotCommand(command="audit", description="Аудит API Ozon, WB и ИИ"),
        BotCommand(command="heal", description="Самопроверка и автопочинка"),
    ]
    await bot_instance.set_my_commands(commands)


def configure_scheduler() -> AsyncIOScheduler:
    """Регистрирует все фоновые задачи без дублей с защитой от параллельного наслоения (max_instances=1)."""
    scheduler = AsyncIOScheduler(
        timezone=MSK_TZ,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 600,
        },
    )
    now_msk = datetime.now(MSK_TZ)

    # 1. Тихая синхронизация остатков FBO/FBS и цен Ozon + WB при старте и каждые 30 минут
    scheduler.add_job(
        scheduled_marketplaces_poll,
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="mp_poll_job",
        replace_existing=True,
    )

    # 2. Автоответчик Ozon: при старте и каждые 30 минут, только dry-run до проверки фильтра даты
    scheduler.add_job(
        scheduled_reviews_and_questions_job,
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="ozon_rev_q_30m",
        replace_existing=True,
    )

    # 3. Единый эталонный ежедневный аудит цен Ozon (299 ₽ / >300 ₽ с 52%, 3 кнопки) и WB (FBO+FBS) в 10:00 МСК
    scheduler.add_job(
        scheduled_daily_price_audit_job,
        CronTrigger(hour=10, minute=0, timezone=MSK_TZ),
        id="daily_price_audit_job",
        replace_existing=True,
    )

    # 4. Еженедельный аудит и рекомендации по рекламе Ozon (2 гибридные РК) и WB — понедельник 10:30 МСК
    scheduler.add_job(
        scheduled_weekly_ads_job,
        CronTrigger(day_of_week="mon", hour=10, minute=30, timezone=MSK_TZ),
        id="weekly_ads_audit_job",
        replace_existing=True,
    )

    # 5. Еженедельный отчет ТОП-5 популярных и убыточных товаров — понедельник 09:00 МСК
    scheduler.add_job(
        scheduled_monday_top5_report,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=MSK_TZ),
        id="weekly_top5_monday_morning",
        replace_existing=True,
    )

    # 6. Стратегический Совет ИИ-специалистов — понедельник 19:00 МСК
    scheduler.add_job(
        scheduled_monday_evening_ai_board,
        CronTrigger(day_of_week="mon", hour=19, minute=0, timezone=MSK_TZ),
        id="weekly_ai_board_monday_evening",
        replace_existing=True,
    )

    # 7. Уведомление финансового календаря 1-го числа в 09:00 МСК
    scheduler.add_job(
        scheduled_1st_day_pnl_notice,
        CronTrigger(day=1, hour=9, minute=0, timezone=MSK_TZ),
        id="monthly_pnl_notice_1st",
        replace_existing=True,
    )

    # 8. Ежемесячный аудит доступности API Ozon, WB и Gemini 1-го числа в 09:05 МСК
    scheduler.add_job(
        scheduled_monthly_audit,
        CronTrigger(day=1, hour=9, minute=5, timezone=MSK_TZ),
        id="monthly_audit_job",
        replace_existing=True,
    )

    # 9. Итоговый месячный P&L отчет по закрывающим актам 5-го числа в 09:30 МСК
    scheduler.add_job(
        scheduled_5th_day_monthly_pnl,
        CronTrigger(day=5, hour=9, minute=30, timezone=MSK_TZ),
        id="monthly_pnl_final_5th",
        replace_existing=True,
    )

    # 10. Автономная ночная самопроверка и автопочинка в 04:30 МСК
    scheduler.add_job(
        scheduled_night_self_heal,
        CronTrigger(hour=4, minute=30, timezone=MSK_TZ),
        id="night_self_heal_job",
        replace_existing=True,
    )

    return scheduler


async def _run_polling_with_conflict_guard() -> None:
    """Запускает long-polling с защитой от транзиентного TelegramConflictError при быстром перезапуске."""
    max_conflict_retries = 3
    for attempt in range(1, max_conflict_retries + 1):
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                allowed_updates=dp.resolve_used_update_types(),
            )
            return
        except TelegramConflictError as conflict_err:
            if attempt >= max_conflict_retries:
                raise
            wait_sec = 4 * attempt
            print(
                f"[Worker] TelegramConflictError (попытка {attempt}/{max_conflict_retries}): {conflict_err}. "
                f"Ожидание закрытия предыдущей сессии {wait_sec} сек...",
                flush=True,
            )
            await asyncio.sleep(wait_sec)


async def main() -> None:
    if not acquire_worker_single_instance_lock():
        print(
            f"[Worker] Другой экземпляр worker.py уже удерживает порт {settings.WORKER_LOCK_PORT}. "
            "Запуск отменен во избежание TelegramConflictError.",
            flush=True,
        )
        return

    scheduler: Optional[AsyncIOScheduler] = None
    heartbeat_task: Optional[asyncio.Task] = None

    try:
        await init_db()
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        await ensure_working_telegram_route()
        try:
            await setup_bot_commands(bot)
        except Exception as exc:
            print(f"[Worker] Предупреждение: не удалось обновить меню команд TG: {exc}", flush=True)

        scheduler = configure_scheduler()
        scheduler.start()

        await _run_polling_with_conflict_guard()
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        try:
            await bot.session.close()
        except Exception:
            pass
        try:
            await close_db()
        except Exception:
            pass
        release_worker_single_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
```

### `requirements.txt.txt`

```text
aiogram==3.13.1
aiohttp==3.10.5
SQLAlchemy==2.0.35
aiosqlite==0.20.0
APScheduler==3.10.4
pydantic==2.9.2
pydantic-settings==2.5.2
aiohttp-socks==0.9.1
playwright==1.48.0
```
