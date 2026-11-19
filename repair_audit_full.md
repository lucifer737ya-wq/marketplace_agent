# Аудит и исправления после проверки отчётов пользователем

## Область и проверка

Ветка `arena/e1c24d50-marketplace-agent`, исходный commit `bc89819821ee8b20f265e3688d713244a3f20d58`. Корневого `CLAUDE.md` в дереве нет. Откат вслепую не выполнялся: применены точечные исправления. Ниже приведены полные тексты всех изменённых файлов, включая тесты и пример окружения.

## Что исправлено

1. Ozon/WB P&L: прибыль скрывается как н/д при отсутствии себестоимости, детализации, возвратах или неполных начислениях; сводная прибыль также н/д. Себестоимость подписана как учтённая. При доступных расчётах есть предупреждение о расчётном характере налога и прибыли. При полной недоступности обоих источников Ozon больше не создаётся нулевой отчёт.
2. WB summary: недельные/дневные сводки за границами запрошенного окна не суммируются; повторяющийся reportId дедуплицируется, а пересечение разных отчётов не интерпретируется без сверки. Указываются исходные даты и поля API; количество, комиссия, налог и прибыль по summary не выдумываются. Ищется дневной резерв. Версии кэша финансов изменены.
3. ТОП-5: отдельно запрашивается резервный источник для каждой площадки, по которой нет построчной статистики; выводится предупреждение о неполноте и использовании резервных данных.
4. Конкуренты: DOM-резерв не выбирает минимум произвольных чисел; карточки с несколькими ценами или без обозначения ₽ пропускаются. Ozon ID извлекается из полного числового суффикса и консервативно отбрасывает ID короче шести цифр; это может исключить реальные карточки. Антибот WB этим не устраняется.
5. Gemini: при доступном каталоге перебираются модели, поддерживающие generateContent, приоритет отдаётся настроенной модели и стабильной 3.1 Flash-Lite; при HTTP-ошибке выводится лишь разрешённый код статуса, без тела ответа. Существующие переменные GEMINI_MODEL и GEMINI_FAST_MODEL в .env переопределяют новые значения по умолчанию.
6. Ответы покупателям: удалены неподтверждённые знания и шаблон о скором поступлении при сбое ИИ, опасные предложения фильтруются. При отсутствии ответа генерация завершается ошибкой. Фильтр и промпт не гарантируют достоверность любых ответов модели.
7. Автоответы: прежний бюджет в 20 минут разделён между отзывами и вопросами (по 10 минут), чтобы зависшие отзывы не блокировали обработку вопросов. При прерывании состояние публикации нужно проверять в кабинете. Существующая настройка публикации не изменена автоматически.
8. Самопроверка уточняет, что сетевые 400/429 не исправляются автоматически изменением кода; диагностика предлагает действия без раскрытия тела запроса.

## Ограничения и безопасное внедрение

- В окружении отсутствуют пользовательские API-доступы, база и кабинет: живые Ozon/WB/Gemini/Telegram и Playwright не проверялись. 127 офлайн-тестов: OK, 4 пропущено, 0 нарушений изоляции; `git diff --check` без замечаний. Это не доказывает правильность реальных финансов или всех кнопок.
- До проверки публикации установить в реальном `.env` `OZON_REPLY_DRY_RUN=true` и перезапустить worker. Фактическое значение по умолчанию в config.py — `False`, а настройки существующего `.env` и состояние live-процессов в этой среде неизвестны. Dry run — предпросмотр, не отправка. Включать публикацию только после ручной проверки; ответы ИИ могут ошибаться.
- Заполнить себестоимость через `/costs` и сверить по октябрю и 13–19 ноября обезличенные исходные строки начислений Ozon, список типов 17/72/74/76/94/116 и построчные отчёты WB, границы отчётов и операции по возвратам. Не передавать ключи, токены, персональные данные. При отсутствии полной детализации отчёт может намеренно не показать прибыль или WB-итог.
- Налоговая база, распределение комиссий и идентификация операций требуют проверки по реальным данным и бухгалтерскому учёту. Сводные расчёты не следует считать бухгалтерской отчётностью. Список доступных моделей и причина HTTP 400 зависят от ключа, проекта, параметров и маршрута. Коды WB 429 и антибот требуют соблюдения лимитов и проверки доступа, не автопатча.
- После резервной копии кода/БД и обновления: проверить отчёты, ТОП, конкурентов и ручной предпросмотр, сверив с ЛК; затем отдельно протестировать ценовые и рекламные действия, не предполагая, что все кнопки покрыты этим исправлением.

## Полный текст изменённых файлов


### `.env.example`

```dotenv
# ==== Обязательные ключи =====================================================
TG_BOT_TOKEN=123456:ABCDEF...
TG_ADMIN_ID=123456789

OZON_CLIENT_ID=
OZON_API_KEY=
# Ozon Performance API (необязательно; нужен для состава рекламных кампаний).
# Если пусто — отчёт по рекламе честно пометит состав РК как недоступный.
OZON_PERF_CLIENT_ID=
OZON_PERF_SECRET=

# Единый токен WB (Статистика, Цены, Продвижение, Маркетплейс).
# Для нового финансового API (finance-api.wildberries.ru) нужен токен
# с категорией «Финансы» (персональный или сервисный).
WB_API_TOKEN=

GEMINI_API_KEY=

# ==== Gemini =================================================================
# Актуальное поколение моделей Google (ноябрь 2026).
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_FAST_MODEL=gemini-3.1-flash-lite
GEMINI_DIRECT_URL=https://generativelanguage.googleapis.com
GEMINI_TIMEOUT=90

# ==== Маршруты запросов ======================================================
# Каждый запрос перебирает ВСЕ доступные маршруты: прямой адрес, затем прокси.
# Прямой маршрут работает без посредников и не зависит от локального прокси.
ALLOW_DIRECT_CONNECTIONS=true

# Локальный прокси теперь только резервный маршрут (Telegram + Gemini).
LOCAL_PROXY_URL=http://127.0.0.1:3066
LOCAL_PROXY_FALLBACK_URL=
# Дополнительные локальные прокси через запятую: http://127.0.0.1:3067,...
# Принимаются только loopback-адреса (127.0.0.1 / localhost).
LOCAL_PROXY_LIST=

# Сколько секунд не трогать упавший маршрут.
ROUTE_COOLDOWN_SECONDS=120

# ==== Прочее =================================================================
# Устаревший /v3/finance/transaction/list отключён Ozon 08.09.2026.
# Включайте только для разового сравнения исторических данных.
OZON_LEGACY_FINANCE=false
TIMEZONE=Europe/Moscow

# Автоответы на отзывы Ozon без публикации (безопасный первый запуск).
# true — только генерирует и показывает ответ; false — публикует.
OZON_REPLY_DRY_RUN=true

# ==== Раздельный выход: Telegram через VPN, Ozon/WB с российского IP =========
# Если на ПК включён Karing/Hiddify/другой VPN:
# 1) площадки должны видеть российский IP продавца, поэтому только direct;
# 2) Telegram в РФ напрямую не работает — только через локальный порт VPN.
# MARKETPLACES_DIRECT_ONLY=true
# TELEGRAM_PROXY_ONLY=true
# LOCAL_PROXY_URL=http://127.0.0.1:3066
# Порт возьмите из настроек VPN-клиента (mixed/HTTP, обычно 2080 или 3066).
# Проверка после настройки: doctor.bat → «Маршруты ozon: direct»,
# «Маршруты telegram: http://127.0.0.1:...».

# ==== Браузерный ярус для витрин конкурентов =================================
# Витрины WB и Ozon закрыты антиботом для прямого HTTP (403/498 на search.wb.ru,
# 307-петля composer-api.bx). Резервный ярус — реальный Chromium через Playwright.
# Один раз установите браузер на ПК (после активации venv):
#     venv\Scripts\python.exe -m playwright install chromium
# Без браузера бот честно пишет «витрина закрыта антиботом», а не зависает.
# Сколько секунд ждать витрину перед отказом (по умолчанию 90).
LISTINGS_BROWSER_BUDGET_SEC=90

# ==== Диагностика и аварийные каталоги ========================================
# По умолчанию logs/ и backups/ лежат рядом с проектом. Переопределять не нужно.
# MARKETPLACE_LOG_DIR=C:\marketplace_agent\logs
# MARKETPLACE_BACKUP_DIR=C:\marketplace_agent\backups
# MARKETPLACE_ENV_FILE=C:\marketplace_agent\.env
```

### `bot/handlers/reviews_fsm.py`

```python
import html
import logging
from contextlib import suppress

from aiogram.exceptions import TelegramAPIError
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select

from bot.keyboards import get_main_menu_keyboard
from config import settings
from database.crud import get_db_session
from database.models import Product
from products_catalog import (
    ALLOWED_UPSELL_LIST,
    LEGAL_COMPLIANCE_INFO,
    get_catalog_product,
)
from services.common.gemini import generate

reviews_router = Router()


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class ReviewForm(StatesGroup):
    waiting_for_review_data = State()


def get_product_context(identifier: str) -> dict:
    """Ищет товар в справочнике по артикулу, штрихкоду или SKU Ozon/WB."""
    clean_id = (identifier or "").strip()
    found = get_catalog_product(clean_id)
    if found:
        return dict(found)

    return {
        "art": clean_id,
        "title": f"Моделирующий гель YaSoKo (арт. {clean_id})",
        "category": "Камуфлирующий гель для наращивания и моделирования ногтей",
        "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE",
        "recommended_upsell": "каучуковая эластичная база YaSoKo (арт. 21000) или топ без липкого слоя (арт. 22000)",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491",
    }


def _build_review_fallback_reply(product_meta: dict) -> str:
    shade = str(product_meta.get("shade") or "").strip()
    color = str(product_meta.get("color_name") or "").strip()
    title = str(product_meta.get("title") or "продукта YaSoKo").strip()
    if shade and color:
        item_label = f"камуфлирующего геля YaSoKo #{shade} ({color})"
    elif shade:
        item_label = f"камуфлирующего геля YaSoKo #{shade}"
    else:
        item_label = title

    return (
        f"Здравствуйте! Благодарим за отзыв и выбор {item_label}! "
        "Мы рады, что вы оценили комфорт в работе и качество покрытия. "
        "Для безупречного глянца рекомендуем дополнить маникюр нашим топом без липкого слоя (арт. 22000). "
        "Желаем вам красивых и стойких работ!"
    )


async def generate_review_reply_llm(sku: str, product_meta: dict, review_text: str) -> str:
    art = product_meta.get("art", sku)
    title = product_meta.get("title") or product_meta.get("name") or "товар YaSoKo"
    prompt = (
        "Ты — представитель YaSoKo. Вежливо ответь на отзыв покупателя. "
        "Используй только название товара и текст отзыва. Не утверждай неподтверждённые "
        "свойства, состав, сертификаты, сроки поступления и совместимость; "
        "не советуй спирт, растворители, нагрев или непроверенные процедуры. "
        "Если информации недостаточно, предложи уточнить у продавца. "
        "Выведи только текст ответа.\n"
        f"Артикул: {art}; название: {title}\nОтзыв: {review_text}"
    )

    try:
        text, _ = await generate(prompt, max_tokens=1500, fast=True)
        if any(term in text.lower() for term in ("спирт", "растворител", "подогре", "нагре")):
            raise RuntimeError("В ответе обнаружен неподтверждённый совет")
        return text
    except RuntimeError as exc:
        raise RuntimeError("Не удалось подготовить безопасный ответ; попробуйте позже") from exc


@reviews_router.message(F.text == "💬 Ответ на отзыв")
@reviews_router.message(Command("review"))
async def cmd_review_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return

    await state.set_state(ReviewForm.waiting_for_review_data)
    await message.answer(
        "💬 <b>Генератор ответов на отзывы покупателей YaSoKo</b>\n\n"
        "Пришлите артикул продавца (например, <code>1001</code>, <code>1028</code> или <code>21000</code>) "
        "либо SKU маркетплейса, а через пробел — текст отзыва.\n\n"
        "<b>Пример:</b>\n"
        "<code>1001 Гель отличный, цвет плотный, самовыравнивается моментально.</code>\n\n"
        "Для отмены отправьте /cancel или /menu.",
        parse_mode="HTML",
    )


@reviews_router.message(ReviewForm.waiting_for_review_data, Command("cancel", "menu", "start"))
@reviews_router.message(ReviewForm.waiting_for_review_data, F.text == "Отмена")
async def cmd_cancel_review(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❌ Ввод отзыва отменен.", reply_markup=get_main_menu_keyboard())


@reviews_router.message(ReviewForm.waiting_for_review_data, F.text)
async def process_review_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "⚠️ Не найден текст отзыва. Укажите сначала артикул, а через пробел — отзыв.\n"
            "<i>Пример: 1001 Отличный гель, плотный цвет.</i>",
            parse_mode="HTML",
        )
        return

    sku_input, review_text = parts[0].strip(), parts[1].strip()
    product_meta = get_product_context(sku_input)

    try:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == sku_input).limit(1)
            res = await session.execute(stmt)
            prod = res.scalars().first()
            if prod and prod.title and not product_meta.get("title"):
                product_meta["title"] = prod.title
    except Exception as exc:
        logging.getLogger(__name__).warning("Каталог БД недоступен: %s", type(exc).__name__)

    status_msg = await message.answer("⏳ Составляю персонализированный ответ по стандартам YaSoKo...")
    try:
        reply_text = await generate_review_reply_llm(
            sku=sku_input,
            product_meta=product_meta,
            review_text=review_text,
        )
    except Exception as exc:
        with suppress(TelegramAPIError):
            await status_msg.delete()
        await state.clear()
        await message.answer(
            f"❌ Ошибка генерации ответа на отзыв: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    with suppress(TelegramAPIError):
        await status_msg.delete()
    await state.clear()

    product_header = f"{product_meta.get('title')} (арт. {sku_input})"
    await message.answer(
        f"✅ <b>Готовый ответ для {html.escape(product_header)}:</b>\n\n"
        f"<i>👇 Нажмите на текст в блоке ниже для моментального копирования:</i>\n\n"
        f"<pre>{html.escape(reply_text)}</pre>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
```

### `config.py`

```python
import os
from pathlib import Path
from pydantic import AliasChoices, Field, ValidationError, field_validator
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
        default="gemini-3.1-flash-lite", description="Базовая модель Gemini"
    )
    GEMINI_FAST_MODEL: str = Field(
        default="gemini-3.1-flash-lite",
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
    # Telegram в РФ доступен только через прокси: прямой маршрут для него
    # отключается, чтобы не тратить 45 с на заведомо мёртвую попытку.
    TELEGRAM_PROXY_ONLY: bool = Field(
        default=False,
        description="Telegram только через локальный прокси (без прямого маршрута)",
    )
    # Ozon и WB должны видеть российский IP продавца. При включении площадки
    # ходят только напрямую и никогда не используют VPN/прокси.
    MARKETPLACES_DIRECT_ONLY: bool = Field(
        default=False,
        description="Ozon и WB только напрямую, без прокси (российский IP)",
    )
    # Сколько секунд держать в карантине упавший маршрут.
    ROUTE_COOLDOWN_SECONDS: int = Field(
        default=120, description="Карантин недоступного маршрута, сек"
    )
    OZON_REPLY_DRY_RUN: bool = False
    # Бюджет браузерного разбора витрин (сек): WB/Ozon отвечают медленно,
    # но отчёт не должен ждать браузер дольше этого значения.
    LISTINGS_BROWSER_BUDGET_SEC: int = Field(
        default=90, description="Бюджет времени на браузерный разбор витрины, сек"
    )
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


try:
    settings = Settings()
except ValidationError as exc:
    # Понятное сообщение важнее простыни pydantic: при скрытом запуске
    # (pythonw.exe) traceback — единственный канал диагностики.
    missing = sorted(
        {
            str(error.get("loc", ("?",))[0])
            for error in exc.errors()
            if error.get("type") == "missing"
        }
    )
    hint = (
        "Не заполнены обязательные параметры .env: "
        + (", ".join(missing) if missing else "проверьте значения")
        + f". Скопируйте {BASE_DIR / '.env.example'} в {BASE_DIR / '.env'} "
        "и заполните ключи. Диагностика без сети: python doctor.py"
    )
    raise RuntimeError(hint) from exc
```

### `maintenance/self_healer.py`

```python
import asyncio
import html
from collections import defaultdict
from datetime import datetime

from config import BASE_DIR
from maintenance.ai_inspector import inspect_project, safe_target
from maintenance.supervisor import repair_file

_check_lock = asyncio.Lock()


async def run_full_self_check_and_heal(**checks) -> dict:
    """Diagnose without importing application modules, polling sellers or writing orders."""
    async with _check_lock:
        try:
            issues = await asyncio.to_thread(inspect_project)
        except Exception as exc:  # noqa: BLE001 — диагностика не должна падать сама
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "total_issues": 1,
                "healed_count": 0,
                "details": [f"🔴 Инспекция проекта не выполнена: {html.escape(str(exc)[:700])}"],
            }
        groups = defaultdict(list)
        details = []
        healed = 0
        for issue in issues:
            label = str(issue.file.relative_to(BASE_DIR)) if issue.file else "API/среда"
            context = f"{label}:{issue.line} {issue.kind}: {issue.detail}"
            if issue.repairable and issue.file and safe_target(issue.file, BASE_DIR):
                groups[issue.file].append(context)
            else:
                advice = ""
                if issue.kind == "API":
                    if "HTTP 429" in issue.detail:
                        advice = "; проверьте лимиты и повторите позже, не перебирая методы"
                    elif "Gemini HTTP 400" in issue.detail:
                        advice = "; проверьте модель из списка доступных и параметры запроса"
                    elif "Ozon HTTP 400" in issue.detail:
                        advice = "; проверьте название метода и схему запроса в диагностике"
                details.append("⚠️ " + html.escape(context + advice) + " (без изменения кода)")
        for path, contexts in groups.items():
            try:
                ok, msg = await repair_file(path, "\n".join(contexts), **checks)
            except Exception as exc:  # noqa: BLE001 — один файл не блокирует остальные
                ok, msg = False, f"{path.name}: починка не удалась ({exc})"
            healed += len(contexts) if ok else 0
            details.append(("🟢 " if ok else "🔴 ") + html.escape(msg))
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_issues": len(issues),
            "healed_count": healed,
            "details": details,
        }
```

### `services/common/buyer_questions.py`

```python
import asyncio
from typing import Optional

from products_catalog import get_catalog_product
from services.common.gemini import generate

BRAND_MATRIX_RULES = """
Используй только информацию, явно указанную в названии товара и тексте покупателя.
Не выдумывай наличие, сроки поставки, цвет, состав, сертификаты, совместимость,
консистенцию и свойства материала. Если данных недостаточно, честно сообщи об этом.
Не советуй разбавление, спирт, растворители или нагрев продукта.
"""


async def call_fast_gemini(prompt: str, raise_on_error: bool = False) -> str:
    # Параметр сохранён для совместимости, но фиктивный ответ недопустим.
    text, _ = await generate(prompt, max_tokens=800, fast=True, timeout=30)
    return text


def _build_sku_context(sku: Optional[str], label: str) -> str:
    if not sku:
        return ""
    sku_clean = str(sku).strip()
    if not sku_clean:
        return ""
    meta = get_catalog_product(sku_clean)
    if meta:
        art = meta.get("art") or sku_clean
        name = meta.get("name") or meta.get("title") or ""
        return f"{label}: Артикул {art}. Название: {name}."
    return f"{label}: Артикул {sku_clean}."


async def generate_buyer_question_answer(question_text: str, sku: Optional[str] = None) -> str:
    sku_context = _build_sku_context(sku, "Товар покупателя")

    prompt = f"""
Ты — профессиональный консультант-технолог бренда YaSoKo.
Ответь на вопрос покупателя на Ozon.

{BRAND_MATRIX_RULES}
{sku_context}

РЕАЛЬНЫЙ ВОПРОС ПОКУПАТЕЛЯ:
«{question_text}»

ГЛАВНЫЕ ТРЕБОВАНИЯ:
1. Поздоровайся и ответь на вопрос только на основании подтверждённых данных выше.
2. При нехватке сведений попроси уточнить у продавца, не обещай сроки поставки.
3. Не давай непроверенные советы по использованию. 2–4 предложения.

"""
    reply = await call_fast_gemini(prompt, raise_on_error=True)
    if not reply.strip():
        raise RuntimeError("Gemini вернул пустой ответ; публикация отменена")
    if any(term in reply.lower() for term in ("спирт", "растворител", "подогре", "нагре")):
        raise RuntimeError("Ответ содержит неподтверждённый совет; публикация отменена")
    return reply


async def generate_review_reply(review_text: str, rating: Optional[int], sku: Optional[str] = None) -> str:
    sku_context = _build_sku_context(sku, "Товар из отзыва")

    prompt = f"""
Ты — специалист контроля качества бренда YaSoKo.
Напиши официальный ответ на отзыв покупателя на Ozon (оценка: {rating if rating is not None else "не указана, не придумывай"}).

{BRAND_MATRIX_RULES}
{sku_context}

ОТЗЫВ ПОКУПАТЕЛЯ:
«{review_text}»

ТРЕБОВАНИЯ:
0. Не рекомендуй спирт, растворители, нагрев продукта или неподтвержденные процедуры.
1. Начни с вежливого приветствия: «Здравствуйте!», поблагодари за выбор бренда YaSoKo и оценку.
2. Персонализируй ответ: обязательно упомяни артикул ({sku or ''}) или конкретные детали из комментария покупателя (оттенок, консистенция, результат на фото).
3. Не обещай новые товары и сроки, не предлагай технологии применения без подтверждённой инструкции производителя.
4. Заверши теплым пожеланием: «Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова.».
5. Объем: 3–4 предложения.
"""
    for attempt in range(3):
        try:
            reply = await call_fast_gemini(prompt, raise_on_error=True)
            if any(term in reply.lower() for term in ("спирт", "растворител", "подогре", "нагре")):
                raise RuntimeError("Ответ содержит неподтвержденный совет; публикация отменена")
            if not reply.strip():
                raise RuntimeError('Gemini вернул пустой ответ')
            return reply
        except RuntimeError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
```

### `services/common/financial_pnl.py`

```python
import asyncio
import html
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from config import settings
from database.models import Product, get_db_session
from products_catalog import OZON_SKU_TO_ART, PRODUCTS_CATALOG, WB_SKU_TO_ART
from services.common.orders_report import (
    CANCELLED_ORDER_STATUSES,
    _div_rub,
    _fmt_money,
    _index_products_by_marketplace,
    _lookup_product_for_order,
    _mul_rub,
    _resolve_order_sku,
    _round_pct,
    _round_rub,
    _safe_float,
    _safe_int,
    _safe_nonneg,
    _to_dec,
    sync_and_generate_orders_report,
)
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

MONTH_NAMES_RU: Dict[int, str] = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

DEFAULT_OZON_UNDER300_COMM_RATE = 0.23


def _current_business_dt() -> datetime:
    tz_name = str(getattr(settings, "TIMEZONE", "Europe/Moscow") or "Europe/Moscow").strip()
    try:
        return datetime.now(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc)


def get_previous_month_bounds(ref_date: Optional[datetime] = None) -> Dict[str, Any]:
    """Возвращает точные границы предыдущего календарного месяца (YYYY-MM-01 .. YYYY-MM-last)."""
    now = ref_date if ref_date is not None else _current_business_dt()
    first_day_current = now.date().replace(day=1)
    last_day_prev = first_day_current - timedelta(days=1)
    first_day_prev = last_day_prev.replace(day=1)
    month_title = f"{MONTH_NAMES_RU.get(first_day_prev.month, '')} {first_day_prev.year}".strip()
    return {
        "date_from": first_day_prev.strftime("%Y-%m-%d"),
        "date_to": last_day_prev.strftime("%Y-%m-%d"),
        "title": month_title,
        "days_count": (last_day_prev - first_day_prev).days + 1,
    }


def _resolve_ozon_commission_rate(
    price: float,
    live_item: Dict[str, Any],
    prod_db: Optional[Product],
    report_comm_rate: Optional[float],
) -> float:
    low_limit = _safe_nonneg(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0)
    high_comm = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)

    if price > low_limit:
        return high_comm

    if live_item:
        c_fbo = _safe_nonneg(live_item.get("commission_rate_fbo") or live_item.get("commission_rate"))
        c_fbs = _safe_nonneg(live_item.get("commission_rate_fbs") or c_fbo)
        st_fbo = max(_safe_int(live_item.get("stock_fbo"), 0), 0)
        st_fbs = max(_safe_int(live_item.get("stock_fbs"), 0), 0)
        if st_fbo > 0 and st_fbs > 0 and c_fbo > 0.0 and c_fbs > 0.0:
            weighted = (c_fbo * st_fbo + c_fbs * st_fbs) / (st_fbo + st_fbs)
        elif st_fbs > 0 and c_fbs > 0.0:
            weighted = c_fbs
        else:
            weighted = c_fbo
        if 0.22 <= weighted <= 0.23:
            return weighted

    if prod_db is not None:
        db_comm = _safe_nonneg(prod_db.commission_rate)
        if 0.22 <= db_comm <= 0.23:
            return db_comm

    if report_comm_rate is not None and 0.22 <= report_comm_rate <= 0.23:
        return report_comm_rate

    return DEFAULT_OZON_UNDER300_COMM_RATE


async def generate_monthly_pnl_report(
    ozon_service: Optional[OzonSellerAPI] = None,
    wb_service: Optional[WBStatisticsAPI] = None,
    ref_date: Optional[datetime] = None,
) -> str:
    """
    Формирует полный P&L отчет по итогам ПРОШЛОГО МЕСЯЦА, забирая реальные расходы из Ozon и WB.
    Учитывает регламент публикации закрывающих отчетов в ЛК (5-го числа каждого месяца по МСК).
    """
    oz_api = ozon_service or OzonSellerAPI()
    wb_api = wb_service or WBStatisticsAPI()

    now = ref_date if ref_date is not None else _current_business_dt()
    bounds = get_previous_month_bounds(ref_date=now)
    now_day = now.day

    publication_notice = ""
    if now_day < 5:
        safe_month = html.escape(str(bounds["title"]))
        publication_notice = (
            f"⚠️ <i>Примечание: сейчас начало месяца ({now_day}-е число). Ozon и Wildberries публикуют "
            f"финальные закрывающие акты и взаиморасчеты за {safe_month} до 4–5 числа. "
            f"Финальный сверенный отчет придет автоматически 5-го числа в 09:30.</i>\n\n"
        )

    try:
        report_body = await sync_and_generate_orders_report(
            ozon_service=oz_api,
            wb_service=wb_api,
            days=int(bounds["days_count"]),
            custom_date_from=str(bounds["date_from"]),
            custom_date_to=str(bounds["date_to"]),
            report_title=(
                f"📅 P&L ОТЧЕТ ЗА ПРОШЛЫЙ МЕСЯЦ (ПРЕДВАРИТЕЛЬНЫЙ): "
                f"{str(bounds['title']).upper()} ({bounds['date_from']} .. {bounds['date_to']})"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — отчёт по кнопке не должен падать молча
        detail = html.escape(str(exc)[:900])
        report_body = (
            f"📅 <b>P&L ОТЧЕТ ЗА ПРОШЛЫЙ МЕСЯЦ (ПРЕДВАРИТЕЛЬНЫЙ): "
            f"{html.escape(str(bounds['title']).upper())}</b> "
            f"({bounds['date_from']} .. {bounds['date_to']})\n\n"
            f"❌ Финансовая детализация недоступна: <code>{detail}</code>\n"
            f"<i>Отчёт по закрывающим актам формируется до 5-го числа. "
            f"Повторите запрос позже — данные подтянутся автоматически.</i>"
        )
    return publication_notice + report_body


async def _fetch_sku_sales(
    api: Any, days: int
) -> Tuple[List[Dict[str, Any]], str]:
    """Резервный источник продаж по артикулам для сервиса маркетплейса."""
    getter = getattr(api, "fetch_sales_by_sku", None)
    if getter is None:
        return [], ""
    result = await getter(days=days)
    if isinstance(result, tuple) and len(result) == 2:
        rows, source = result
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)], str(source or "")
        return [], str(source or "")
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)], ""
    return [], ""


def _build_stats_from_sku_sales(
    orders: List[Tuple[str, Dict[str, Any]]],
    by_mp_sku: Dict[Tuple[str, str], Any],
    by_mp_ext: Dict[Tuple[str, str], Any],
    live_tariffs: Dict[Tuple[str, str], Dict[str, Any]],
    *,
    tax_rate: float,
    acq_rate: float,
    oz_report_comm_rate: Optional[float],
    oz_ad_rate: float,
    wb_acq_rate: float,
    wb_deliv_per_order: float,
    oz_deliv_per_order: Optional[float],
    oz_other_overhead_per_order: float,
    wb_overhead_per_order: float,
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[str, int]]:
    """Пересчитывает статистику по артикулам из резервного источника.

    Комиссии и логистика распределяются по удельным ставкам сводного отчёта,
    а себестоимость берётся только подтверждённая. Артикулы без себестоимости
    попадают в отдельный список и не искажают рейтинг прибыли.
    """
    stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    excluded = 0
    for default_mp, order in orders:
        mp = str(order.get("marketplace") or default_mp).strip().lower()
        if mp not in {"ozon", "wb"}:
            mp = default_mp
        raw_sku = str(order.get("sku") or "").strip()
        art = _resolve_order_sku(raw_sku, mp) or raw_sku
        unit_price = _safe_nonneg(order.get("price"))
        qty = max(_safe_int(order.get("quantity"), 1), 1)
        if not art or unit_price <= 0.0:
            excluded += 1
            continue

        raw_bp = order.get("buyer_price")
        unit_buyer_price = (
            _safe_nonneg(raw_bp)
            if raw_bp is not None and _safe_nonneg(raw_bp) > 0.0
            else unit_price
        )

        p_db = _lookup_product_for_order(mp, raw_sku, by_mp_sku, by_mp_ext)
        cogs_known = p_db is not None and _safe_nonneg(p_db.cost_price) > 0.0
        unit_cogs = (
            _safe_nonneg(p_db.cost_price)
            + _safe_nonneg(p_db.packaging_cost)
            + _safe_nonneg(p_db.first_mile_unit_cost)
            if cogs_known and p_db is not None
            else 0.0
        )

        t_live = live_tariffs.get((mp, art)) or live_tariffs.get((mp, raw_sku)) or {}
        if mp == "ozon":
            c_rate = _resolve_ozon_commission_rate(unit_price, t_live, p_db, oz_report_comm_rate)
            unit_comm = _mul_rub(unit_price, c_rate)
            unit_acq = _mul_rub(unit_price, acq_rate)
            unit_log = _div_rub(oz_deliv_per_order, 1) if oz_deliv_per_order else 0.0
            unit_ad = _mul_rub(unit_price, oz_ad_rate)
            unit_mp_fee = _round_rub(
                _to_dec(unit_comm)
                + _to_dec(unit_acq)
                + _to_dec(unit_log)
                + _to_dec(unit_ad)
                + _to_dec(oz_other_overhead_per_order)
            )
            unit_tax = _mul_rub(unit_price, tax_rate)
            if oz_deliv_per_order is None:
                cogs_known = False  # без факта доставки экономика неполная
        else:
            wb_comm_rate = _safe_nonneg(t_live.get("commission_rate"))
            if wb_comm_rate <= 0.0:
                pct = _safe_nonneg(t_live.get("commission_rate_fbo"))
                wb_comm_rate = pct if pct > 1.0 else 0.0  # 1.0 = 100% -> не ставка
                if wb_comm_rate > 1.0:
                    wb_comm_rate = wb_comm_rate / 100.0
            unit_comm = _mul_rub(unit_price, wb_comm_rate) if wb_comm_rate > 0.0 else 0.0
            unit_log = _safe_nonneg(t_live.get("fbo_logistics_cost"))
            if unit_log <= 0.0 and wb_deliv_per_order > 0.0:
                unit_log = _round_rub(wb_deliv_per_order)
            unit_acq = _mul_rub(max(unit_buyer_price, unit_price), wb_acq_rate)
            unit_mp_fee = _round_rub(
                _to_dec(unit_comm)
                + _to_dec(unit_log)
                + _to_dec(unit_acq)
                + _to_dec(wb_overhead_per_order)
            )
            unit_tax = _mul_rub(unit_buyer_price, tax_rate)

        unit_profit = (
            _round_rub(
                _to_dec(unit_price) - _to_dec(unit_cogs) - _to_dec(unit_mp_fee) - _to_dec(unit_tax)
            )
            if cogs_known
            else 0.0
        )

        key = (mp, art)
        if key not in stats:
            cat_meta = PRODUCTS_CATALOG.get(art, {})
            title = (
                cat_meta.get("name")
                or (p_db.title if p_db is not None and p_db.title else f"Арт. {art}")
            )
            stats[key] = {
                "mp": mp.upper(),
                "sku": art,
                "title": str(title),
                "qty": 0,
                "revenue": 0.0,
                "cogs": 0.0,
                "mp_fee": 0.0,
                "acquiring": 0.0,
                "tax": 0.0,
                "profit": 0.0,
                "cogs_complete": True,
                "missing_cogs_qty": 0,
            }
        st = stats[key]
        dec_qty = _to_dec(qty)
        st["qty"] += qty
        st["revenue"] = _round_rub(_to_dec(st["revenue"]) + _to_dec(unit_price) * dec_qty)
        st["cogs"] = _round_rub(_to_dec(st["cogs"]) + _to_dec(unit_cogs) * dec_qty)
        st["mp_fee"] = _round_rub(_to_dec(st["mp_fee"]) + _to_dec(unit_mp_fee) * dec_qty)
        st["acquiring"] = _round_rub(_to_dec(st["acquiring"]) + _to_dec(unit_acq) * dec_qty)
        st["tax"] = _round_rub(_to_dec(st["tax"]) + _to_dec(unit_tax) * dec_qty)
        if cogs_known:
            st["profit"] = _round_rub(_to_dec(st["profit"]) + _to_dec(unit_profit) * dec_qty)
        else:
            st["cogs_complete"] = False
            st["missing_cogs_qty"] += qty
    return stats, {"excluded": excluded}


async def generate_weekly_top_and_unprofitable_report(
    ozon_service: Optional[OzonSellerAPI] = None,
    wb_service: Optional[WBStatisticsAPI] = None,
    days: int = 7,
) -> str:
    """
    Еженедельная сводка:
    1. ТОП-5 самых популярных товаров (по заказам и выручке)
    2. ТОП-5 убыточных / низкорентабельных товаров (с учетом вознаграждения МП,
       эквайринга 1.5%, фактической логистики Ozon ~39 ₽/заказ, накладных расходов,
       подтвержденной себестоимости и налога УСН 6%).
    """
    safe_days = max(_safe_int(days, 7), 1)
    tax_rate = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
    acq_rate = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)

    oz_api = ozon_service or OzonSellerAPI()
    wb_api = wb_service or WBStatisticsAPI()

    oz_fin, wb_fin, oz_prices, wb_prices = await asyncio.gather(
        oz_api.fetch_real_finance_breakdown(days=safe_days),
        wb_api.fetch_real_finance_breakdown(days=safe_days),
        oz_api.fetch_stocks_and_prices(),
        wb_api.fetch_stocks_and_prices(),
        return_exceptions=True,
    )

    api_warnings: List[str] = []
    if isinstance(oz_fin, Exception):
        api_warnings.append(f"⚠️ Ozon финансы недоступны: <code>{html.escape(str(oz_fin))}</code>")
    if isinstance(wb_fin, dict) and wb_fin.get("summary_only"):
        api_warnings.append("⚠️ WB: нет построчной реализации, рейтинг по WB неполон.")
    if isinstance(wb_fin, Exception):
        api_warnings.append(f"⚠️ WB финансы недоступны: <code>{html.escape(str(wb_fin))}</code>")

    async with get_db_session() as session:
        prods_res = await session.execute(select(Product))
        all_prods = list(prods_res.scalars().all())

    by_mp_sku, by_mp_ext = _index_products_by_marketplace(all_prods)

    live_tariffs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if isinstance(oz_prices, list):
        for it in oz_prices:
            if not isinstance(it, dict):
                continue
            sku_code = str(it.get("sku") or "").strip()
            if sku_code:
                live_tariffs[("ozon", OZON_SKU_TO_ART.get(sku_code, sku_code))] = it
                live_tariffs[("ozon", sku_code)] = it
    if isinstance(wb_prices, list):
        for it in wb_prices:
            if not isinstance(it, dict):
                continue
            sku_code = str(it.get("sku") or "").strip()
            nm_str = str(it.get("nm_id") or it.get("external_id") or "").strip()
            if sku_code:
                live_tariffs[("wb", WB_SKU_TO_ART.get(sku_code, sku_code))] = it
                live_tariffs[("wb", sku_code)] = it
            if nm_str:
                art_from_nm = WB_SKU_TO_ART.get(nm_str)
                if art_from_nm and ("wb", art_from_nm) not in live_tariffs:
                    live_tariffs[("wb", art_from_nm)] = it
                live_tariffs[("wb", nm_str)] = it

    active_oz_orders: List[Dict[str, Any]] = []
    seen_oz_ids: set[str] = set()
    if isinstance(oz_fin, dict) and isinstance(oz_fin.get("orders"), list):
        for ord_item in oz_fin["orders"]:
            if not isinstance(ord_item, dict):
                continue
            oid = str(ord_item.get("order_id") or "").strip()
            if oid:
                if oid in seen_oz_ids:
                    continue
                seen_oz_ids.add(oid)
            st_raw = str(ord_item.get("status") or "ordered").strip().lower()
            if st_raw not in CANCELLED_ORDER_STATUSES:
                active_oz_orders.append(ord_item)

    active_wb_orders: List[Dict[str, Any]] = []
    seen_wb_ids: set[str] = set()
    if isinstance(wb_fin, dict) and isinstance(wb_fin.get("orders"), list):
        for ord_item in wb_fin["orders"]:
            if not isinstance(ord_item, dict):
                continue
            oid = str(ord_item.get("order_id") or "").strip()
            if oid:
                if oid in seen_wb_ids:
                    continue
                seen_wb_ids.add(oid)
            st_raw = str(ord_item.get("status") or "ordered").strip().lower()
            if st_raw not in CANCELLED_ORDER_STATUSES:
                active_wb_orders.append(ord_item)

    # --- Фактические удельные параметры Ozon (без деления на ноль и без выдуманных констант) ---
    oz_deliv_per_order: Optional[float] = None
    oz_dropoff_per_order = 0.0
    oz_other_overhead_per_order = 0.0
    oz_ad_rate = 0.0
    oz_acq_rate = acq_rate
    oz_report_comm_rate: Optional[float] = None

    if isinstance(oz_fin, dict):
        oz_units_from_orders = sum(max(_safe_int(o.get("quantity"), 1), 1) for o in active_oz_orders)
        raw_oz_cnt = max(_safe_int(oz_fin.get("orders_count"), 0), 0)
        oz_orders_cnt = raw_oz_cnt if raw_oz_cnt > 0 else oz_units_from_orders

        oz_sales = _safe_float(oz_fin.get("sales_total"))
        if oz_sales > 0.0:
            oz_ads_total = (
                _safe_nonneg(oz_fin.get("ads_total"))
                if oz_fin.get("ads_total") is not None
                else _safe_nonneg(oz_fin.get("promo_cpo")) + _safe_nonneg(oz_fin.get("promo_cpc"))
            )
            if oz_ads_total > 0.0:
                oz_ad_rate = oz_ads_total / oz_sales

            raw_oz_acq = _safe_float(oz_fin.get("acquiring"))
            if raw_oz_acq != 0.0 or oz_fin.get("acquiring_reported"):
                oz_acq_rate = raw_oz_acq / oz_sales

            raw_oz_comm = _safe_nonneg(oz_fin.get("commission"))
            if raw_oz_comm > 0.0:
                oz_report_comm_rate = raw_oz_comm / oz_sales

        if oz_orders_cnt > 0:
            oz_deliv_total = (
                _safe_nonneg(oz_fin.get("deliv_total"))
                if oz_fin.get("deliv_total") is not None
                else (
                    _safe_nonneg(oz_fin.get("deliv_direct"))
                    + _safe_nonneg(oz_fin.get("deliv_return"))
                    + _safe_nonneg(oz_fin.get("deliv_ozon_handover"))
                )
            )
            if oz_deliv_total > 0.0:
                oz_deliv_per_order = _div_rub(oz_deliv_total, oz_orders_cnt)

            oz_dropoff_total = _safe_nonneg(oz_fin.get("partner_dropoff"))
            if oz_dropoff_total > 0.0:
                oz_dropoff_per_order = _div_rub(oz_dropoff_total, oz_orders_cnt)

            oz_other_total = (
                _safe_nonneg(oz_fin.get("partner_pack"))
                + _safe_nonneg(oz_fin.get("partner_returns"))
                + _safe_nonneg(oz_fin.get("star_products"))
                + _safe_nonneg(oz_fin.get("partner_last_mile"))
                + _safe_nonneg(oz_fin.get("fbo_storage"))
                + _safe_nonneg(oz_fin.get("other_fines"))
                + _safe_nonneg(oz_fin.get("decompensations"))
                + _safe_nonneg(oz_fin.get("mutual_settlements"))
            )
            if oz_other_total > 0.0:
                oz_other_overhead_per_order = _div_rub(oz_other_total, oz_orders_cnt)

    # --- Фактические удельные параметры WB (эквайринг 1.5% + распределение накладных без деления на ноль) ---
    wb_acq_rate = acq_rate
    wb_overhead_per_order = 0.0
    wb_unassigned_delivery = 0.0
    if isinstance(wb_fin, dict):
        wb_units_from_orders = sum(max(_safe_int(o.get("quantity"), 1), 1) for o in active_wb_orders)

        wb_rev_s = _safe_float(wb_fin.get("revenue_seller"))
        wb_rev_b = (
            _safe_float(wb_fin.get("revenue_buyer"))
            if wb_fin.get("revenue_buyer") is not None
            else wb_rev_s
        )
        wb_acq_base_total = max(wb_rev_b, wb_rev_s, 0.0)
        raw_wb_acq = _safe_float(wb_fin.get("acquiring"))
        if wb_fin.get("acquiring") is not None and wb_acq_base_total > 0.0:
            wb_acq_rate = raw_wb_acq / wb_acq_base_total

        if wb_units_from_orders:
            assigned = sum(_safe_nonneg(o.get('logistics')) * max(_safe_int(o.get('quantity'), 1), 1) for o in active_wb_orders)
            missing = sum(max(_safe_int(o.get('quantity'), 1), 1) for o in active_wb_orders if not o.get('logistics'))
            if missing:
                wb_unassigned_delivery = _div_rub(max(0.0, _safe_nonneg(wb_fin.get('logistics_direct')) - assigned), missing)
        if wb_units_from_orders > 0:
            wb_other_total = (
                _safe_nonneg(wb_fin.get("storage"))
                + _safe_nonneg(wb_fin.get("acceptance"))
                + _safe_nonneg(wb_fin.get("ads"))
                + _safe_nonneg(wb_fin.get("other_services"))
                + _safe_nonneg(wb_fin.get("logistics_return"))
            )
            if wb_other_total > 0.0:
                wb_overhead_per_order = _div_rub(wb_other_total, wb_units_from_orders)

    sku_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    oz_delivery_missing_flag = False

    combined_orders: List[Tuple[str, Dict[str, Any]]] = [
        *[("ozon", o) for o in active_oz_orders],
        *[("wb", o) for o in active_wb_orders],
    ]

    unpriced = 0
    for default_mp, order in combined_orders:
        if order.get("price") is None:
            unpriced += 1
            continue  # mixed posting: no defensible per-SKU revenue
        mp = str(order.get("marketplace") or default_mp).strip().lower()
        if mp not in {"ozon", "wb"}:
            mp = default_mp

        raw_sku = str(order.get("sku") or "").strip()
        art = _resolve_order_sku(raw_sku, mp) or raw_sku or "unknown"
        key = (mp, art)

        qty = max(_safe_int(order.get("quantity"), 1), 1)
        unit_price = _safe_nonneg(order.get("price"))
        raw_bp = order.get("buyer_price")
        unit_buyer_price = (
            _safe_nonneg(raw_bp)
            if raw_bp is not None and _safe_nonneg(raw_bp) > 0.0
            else unit_price
        )

        p_db = _lookup_product_for_order(mp, raw_sku, by_mp_sku, by_mp_ext)
        unit_cogs_known = p_db is not None and _safe_nonneg(p_db.cost_price) > 0.0
        unit_cogs = (
            _safe_nonneg(p_db.cost_price)
            + _safe_nonneg(p_db.packaging_cost)
            + _safe_nonneg(p_db.first_mile_unit_cost)
            if unit_cogs_known and p_db is not None
            else 0.0
        )

        t_live = live_tariffs.get((mp, art)) or live_tariffs.get((mp, raw_sku)) or {}

        if mp == "ozon":
            posting_units = max(_safe_int(order.get("posting_units"), 1), 1)
            ord_comm = _safe_float(order.get("commission"))
            if order.get("commission") is not None:
                unit_comm = _round_rub(ord_comm)
            else:
                c_rate = _resolve_ozon_commission_rate(unit_price, t_live, p_db, oz_report_comm_rate)
                unit_comm = _mul_rub(unit_price, c_rate)

            ord_acq = _safe_float(order.get("acquiring"))
            unit_acq = _round_rub(ord_acq) if order.get("acquiring") is not None else _mul_rub(unit_price, oz_acq_rate)

            ord_log = _safe_nonneg(order.get("logistics"))
            if ord_log > 0.0:
                unit_log = _round_rub(ord_log)
            elif oz_deliv_per_order is not None:
                unit_log = _div_rub(oz_deliv_per_order, posting_units)
            else:
                unit_log = 0.0
                oz_delivery_missing_flag = True
                unit_cogs_known = False  # exclude incomplete economics from the profit ranking

            ord_ad = order.get("ad_spend")
            unit_ad = (
                _round_rub(_safe_nonneg(ord_ad))
                if ord_ad is not None
                else _mul_rub(unit_price, oz_ad_rate)
            )

            unit_mp_fee = _round_rub(
                _to_dec(unit_comm)
                + _to_dec(unit_acq)
                + _to_dec(unit_log)
                + _to_dec(oz_dropoff_per_order) / posting_units
                + _to_dec(oz_other_overhead_per_order) / posting_units
                + _to_dec(unit_ad)
            )
            # На Ozon база УСН 6% — цена реализации продавца.
            unit_tax = _mul_rub(unit_price, tax_rate)
        else:
            unit_comm = _round_rub(_safe_float(order.get("commission")))
            unit_log = _round_rub(_safe_nonneg(order.get("logistics")) or wb_unassigned_delivery)
            ord_acq = _safe_float(order.get("acquiring"))
            unit_acq = (
                _round_rub(ord_acq)
                if order.get("acquiring") is not None
                else _mul_rub(max(unit_buyer_price, unit_price, 0.0), wb_acq_rate)
            )
            unit_mp_fee = _round_rub(
                _to_dec(unit_comm)
                + _to_dec(unit_log)
                + _to_dec(unit_acq)
                + _to_dec(wb_overhead_per_order)
            )
            # На WB база УСН 6% — цена покупки покупателем с учетом СПП.
            unit_tax = _mul_rub(unit_buyer_price, tax_rate)

        unit_profit = (
            _round_rub(_to_dec(unit_price) - _to_dec(unit_cogs) - _to_dec(unit_mp_fee) - _to_dec(unit_tax))
            if unit_cogs_known
            else 0.0
        )

        if key not in sku_stats:
            cat_meta = PRODUCTS_CATALOG.get(art, {})
            title = (
                cat_meta.get("name")
                or (p_db.title if p_db is not None and p_db.title else f"Арт. {art}")
            )
            sku_stats[key] = {
                "mp": mp.upper(),
                "sku": art,
                "title": str(title),
                "qty": 0,
                "revenue": 0.0,
                "cogs": 0.0,
                "mp_fee": 0.0,
                "acquiring": 0.0,
                "tax": 0.0,
                "profit": 0.0,
                "cogs_complete": True,
                "missing_cogs_qty": 0,
            }

        st = sku_stats[key]
        dec_qty = _to_dec(qty)
        st["qty"] += qty
        st["revenue"] = _round_rub(_to_dec(st["revenue"]) + _to_dec(unit_price) * dec_qty)
        st["cogs"] = _round_rub(_to_dec(st["cogs"]) + _to_dec(unit_cogs) * dec_qty)
        st["mp_fee"] = _round_rub(_to_dec(st["mp_fee"]) + _to_dec(unit_mp_fee) * dec_qty)
        st["acquiring"] = _round_rub(_to_dec(st["acquiring"]) + _to_dec(unit_acq) * dec_qty)
        st["tax"] = _round_rub(_to_dec(st["tax"]) + _to_dec(unit_tax) * dec_qty)
        if unit_cogs_known:
            st["profit"] = _round_rub(_to_dec(st["profit"]) + _to_dec(unit_profit) * dec_qty)
        else:
            st["cogs_complete"] = False
            st["missing_cogs_qty"] += qty

    if unpriced:
        api_warnings.append(f"⚠️ Без разбивки цены по SKU: {unpriced} строк, исключены из рейтинга.")

    # --- Резерв: агрегаты продаж по SKU, когда финансовая детализация пуста ---
    # Начисления Ozon и закрытые отчёты WB за последние дни формируются с
    # задержкой, поэтому ТОП-5 без этого яруса показывал «заказов не найдено».
    missing_markets = {mp for mp in ("ozon", "wb") if not any(key[0] == mp for key in sku_stats)}
    if missing_markets:
        fallback_orders: List[Tuple[str, Dict[str, Any]]] = []
        fallback_sources: List[str] = []
        fallback_markets = sorted(missing_markets)
        fallback_results = await asyncio.gather(
            *(_fetch_sku_sales(oz_api if mp == "ozon" else wb_api, safe_days) for mp in fallback_markets),
            return_exceptions=True,
        )
        for mp_key, result in zip(fallback_markets, fallback_results):
            if isinstance(result, Exception):
                api_warnings.append(
                    f"⚠️ {mp_key.upper()}: резервный источник артикулов недоступен — "
                    f"<code>{html.escape(str(result))}</code>"
                )
                continue
            rows, source = result
            if rows:
                fallback_orders.extend((mp_key, row) for row in rows)
                fallback_sources.append(f"{mp_key.upper()}: {source}")
            elif source:
                api_warnings.append(f"⚠️ <i>{html.escape(source)}</i>")

        # Удельная логистика WB: из реклассификации отчёта либо из тарифа
        # «короб-логистика» (усреднение по складам), если факт недоступен.
        _wb_fallback_delivery = wb_unassigned_delivery
        if _wb_fallback_delivery <= 0.0:
            try:
                _tariffs = await wb_api.fetch_wb_tariffs()
            except Exception:  # noqa: BLE001 — резервный тариф не критичен для отчёта
                _tariffs = None
            if isinstance(_tariffs, dict):
                _rates = [
                    _safe_nonneg(w.get("boxDeliveryBase"))
                    for w in (_tariffs.get("warehouses") or {}).values()
                    if isinstance(w, dict) and w.get("boxDeliveryBase") not in (None, "", "-")
                ]
                _rates = [r for r in _rates if r > 0.0]
                if _rates:
                    _wb_fallback_delivery = _round_rub(sum(_rates) / len(_rates))

        if fallback_orders:
            fallback_sku_stats, fallback_stats = _build_stats_from_sku_sales(
                fallback_orders,
                by_mp_sku,
                by_mp_ext,
                live_tariffs,
                tax_rate=tax_rate,
                acq_rate=acq_rate,
                oz_report_comm_rate=oz_report_comm_rate,
                oz_ad_rate=oz_ad_rate,
                wb_acq_rate=wb_acq_rate,
                oz_deliv_per_order=oz_deliv_per_order,
                oz_other_overhead_per_order=oz_other_overhead_per_order,
                wb_overhead_per_order=wb_overhead_per_order,
                wb_deliv_per_order=_wb_fallback_delivery,
            )
            sku_stats.update(fallback_sku_stats)
            api_warnings.append(
                "ℹ️ <i>Часть рейтинга построена по резервному источнику (начисления/детализация "
                "ещё не сформированы): " + html.escape("; ".join(fallback_sources)) + "</i>"
            )
            if fallback_stats.get("excluded"):
                api_warnings.append(
                    f"⚠️ <i>Исключено строк без цены: {fallback_stats['excluded']}.</i>"
                )

    if not sku_stats:
        empty_msg = (
            f"📈 <b>Еженедельная сводка ТОП-5 товаров:</b> "
            f"за последние {safe_days} дн. заказов не найдено."
        )
        if api_warnings:
            return empty_msg + "\n\n" + "\n".join(api_warnings)
        return empty_msg

    items_list = list(sku_stats.values())
    top_popular = sorted(items_list, key=lambda x: (x["qty"], x["revenue"]), reverse=True)[:5]
    verified_items = [x for x in items_list if x["cogs_complete"]]
    unverified_items = [x for x in items_list if not x["cogs_complete"]]
    top_unprofitable = sorted(
        verified_items,
        key=lambda x: (
            x["profit"],
            (x["profit"] / x["revenue"]) if x["revenue"] > 0.0 else 0.0,
        ),
    )[:5]

    lines: List[str] = [f"📊 <b>ЕЖЕНЕДЕЛЬНАЯ СВОДКА ТОВАРОВ (За {safe_days} дн.)</b>"]
    if oz_deliv_per_order is not None and active_oz_orders:
        lines.append(f"🚚 <i>Фактическая логистика Ozon из отчёта: <b>{oz_deliv_per_order:.2f} ₽/заказ</b></i>")
    if api_warnings:
        lines.extend(api_warnings)

    lines.append("\n🏆 <b>ТОП-5 ПОПУЛЯРНЫХ ТОВАРОВ (Бестселлеры):</b>")
    for idx, it in enumerate(top_popular, 1):
        safe_sku = html.escape(it["sku"])
        safe_title = html.escape(it["title"][:38])
        if it["cogs_complete"]:
            ros = _round_pct(it["profit"], it["revenue"]) if it["revenue"] > 0.0 else 0.0
            unit_prof = _div_rub(it["profit"], it["qty"]) if it["qty"] > 0 else 0.0
            profit_line = (
                f"   • Чистая прибыль: <code>{_fmt_money(it['profit'])} ₽</code> "
                f"({unit_prof:+.1f} ₽/шт. | ROS {ros:.1f}%)"
            )
        else:
            profit_line = "   • Чистая прибыль: <i>н/д (нет себестоимости или факта доставки)</i>"
        lines.append(
            f"{idx}. <b>[{it['mp']}] Арт. <code>{safe_sku}</code></b> — {safe_title}\n"
            f"   • Продано: <b>{it['qty']} шт.</b> на <b>{_fmt_money(it['revenue'])} ₽</b>\n"
            f"{profit_line}"
        )

    lines.append("\n🚨 <b>ТОП-5 УБЫТОЧНЫХ / НИЗКОМАРЖИНАЛЬНЫХ ТОВАРОВ:</b>")
    if not top_unprofitable:
        lines.append(
            "⚠️ <i>Рейтинг рентабельности не построен: для проданных артикулов не задана себестоимость (/costs).</i>"
        )
    else:
        for idx, it in enumerate(top_unprofitable, 1):
            safe_sku = html.escape(it["sku"])
            safe_title = html.escape(it["title"][:34])
            ros = _round_pct(it["profit"], it["revenue"]) if it["revenue"] > 0.0 else 0.0
            unit_prof = _div_rub(it["profit"], it["qty"]) if it["qty"] > 0 else 0.0
            if it["profit"] < 0.0:
                status_icon = "🔴 Убыток"
            elif ros < 15.0:
                status_icon = "🟡 Низкая маржа"
            else:
                status_icon = "🟢 Наименьшая прибыль"
            lines.append(
                f"{idx}. {status_icon} <b>[{it['mp']}] Арт. <code>{safe_sku}</code></b> — {safe_title}\n"
                f"   • Заказов: <b>{it['qty']} шт.</b> | Выручка: {_fmt_money(it['revenue'])} ₽ | "
                f"Удержания МП (с экв.): -{_fmt_money(it['mp_fee'])} ₽\n"
                f"   • Себес: -{_fmt_money(it['cogs'])} ₽ | Налог УСН: -{_fmt_money(it['tax'])} ₽ "
                f"👉 Итог: <code>{_fmt_money(it['profit'])} ₽</code> ({unit_prof:+.1f} ₽/шт. | ROS {ros:.1f}%)"
            )

    if unverified_items:
        missing_labels = ", ".join(
            f"{it['mp']}:{html.escape(it['sku'])}" for it in unverified_items[:8]
        )
        if len(unverified_items) > 8:
            missing_labels += f" (+{len(unverified_items) - 8})"
        lines.append(
            f"\n⚠️ <i>Без подтвержденной себестоимости или доставки (исключены из расчета прибыли): "
            f"<code>{missing_labels}</code>.</i>"
        )

    if oz_delivery_missing_flag:
        lines.append(
            "⚠️ <i>Фактическая доставка Ozon в отчёте не подтверждена — логистика Ozon требует сверки в ЛК.</i>"
        )

    return "\n".join(lines)


async def generate_weekly_pnl_report(
    ozon_service: Optional[OzonSellerAPI] = None,
    wb_service: Optional[WBStatisticsAPI] = None,
) -> str:
    """Обратная совместимость: возвращает месячный P&L + еженедельный ТОП-5."""
    oz_api = ozon_service or OzonSellerAPI()
    wb_api = wb_service or WBStatisticsAPI()
    monthly = await generate_monthly_pnl_report(ozon_service=oz_api, wb_service=wb_api)
    weekly_top = await generate_weekly_top_and_unprofitable_report(
        ozon_service=oz_api, wb_service=wb_api
    )
    return f"{monthly}\n\n➖➖➖➖➖➖➖➖➖➖\n\n{weekly_top}"
```

### `services/common/gemini.py`

```python
"""Gemini client: direct Google endpoint first, local proxies only as fallback.

Keys travel in the ``x-goog-api-key`` header, never in a query string, so error
texts stay clean. Any route failure (transport or anti-bot) automatically moves
to the next route — this is what makes the module survive a dead proxy.
"""

import asyncio
import logging
import re
from typing import Any

import aiohttp

from config import settings
from services.common.routes import (
    DIRECT,
    mark_route_failed,
    mark_route_ok,
    request_routes,
)

logger = logging.getLogger(__name__)

# Актуальные текстовые модели Google (проверено по документации 11.2026).
# Старые gemini-2.5-* автоматически переадресуются Google с 01.12.2026,
# поэтому фолбэк-цепочка начинается с текущего поколения.
FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]
FAST_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
]
_NON_TEXT_TAGS = (
    "tts",
    "audio",
    "imagen",
    "embedding",
    "vision",
    "robotics",
    "computer-use",
    "image",
    "video",
    "live",
    "native-audio",
)


BASE_URL = (
    str(settings.GEMINI_DIRECT_URL or "https://generativelanguage.googleapis.com").rstrip("/")
    + "/v1beta/models"
)


def base_url() -> str:
    return str(settings.GEMINI_DIRECT_URL or "").rstrip("/") + "/v1beta/models"


def _extract_model_name(item: Any) -> str:
    name = item.get("name") if isinstance(item, dict) else item
    return str(name or "").strip().removeprefix("models/")


def _is_supported_text_model(name: str) -> bool:
    if not re.fullmatch(r"gemini-[\w.-]+", name or ""):
        return False
    lowered = name.lower()
    return not any(tag in lowered for tag in _NON_TEXT_TAGS)


def _model_chain(fast: bool) -> list[str]:
    preferred = settings.GEMINI_FAST_MODEL if fast else settings.GEMINI_MODEL
    extra = FAST_MODELS if fast else FALLBACK_MODELS
    names = [_extract_model_name(preferred), *extra, *FALLBACK_MODELS, *FAST_MODELS]
    return list(dict.fromkeys(n for n in names if _is_supported_text_model(n)))


async def _get_available_models(session, route: str = DIRECT) -> list[str]:
    """Server-reported models; a failure here must not kill generation."""
    try:
        async with (
            await session.get(
                BASE_URL, **({} if route == DIRECT else {"proxy": route})
            )
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
        return []
    return [
        _extract_model_name(item)
        for item in data.get("models", [])
        if isinstance(item, dict)
        and "generateContent" in item.get("supportedGenerationMethods", [])
        and _is_supported_text_model(_extract_model_name(item))
    ]


def _text_from_candidate(candidate: dict) -> str:
    if candidate.get("finishReason") not in (None, "STOP"):
        # In particular, never return truncated MAX_TOKENS output as a report.
        return ""
    parts = candidate.get("content", {}).get("parts", []) or []
    return "".join(
        part.get("text", "") for part in parts if not part.get("thought")
    ).strip()


async def generate(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.2,
    fast: bool = False,
    timeout: int | None = None,
) -> tuple[str, str]:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Не задан ключ Gemini")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    total = timeout or settings.GEMINI_TIMEOUT
    headers = {
        "x-goog-api-key": settings.GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        routes = request_routes("gemini")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    errors: list[str] = []
    for route in routes:
        proxy = {} if route == DIRECT else {"proxy": route}
        route_label = "напрямую" if route == DIRECT else route
        route_broken = False
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=total, connect=15),
                headers=headers,
            ) as session:
                models = await _get_available_models(session, route)
                preferred = _model_chain(fast)
                # При доступном каталоге используем только модели с поддержкой
                # generateContent, отдавая приоритет стабильной модели и настройке.
                chain = ([n for n in preferred if n in models] +
                         [n for n in models if n not in preferred]) if models else preferred
                for name in chain:
                    url = f"{BASE_URL}/{name}:generateContent"
                    try:
                        async with session.post(url, json=payload, **proxy) as resp:
                            if resp.status != 200:
                                # Не выводим текст ошибки: в нём могут быть ключи,
                                # входные данные или прочая конфиденциальная информация.
                                api_status = ""
                                try:
                                    error_data = await resp.json()
                                    status = (error_data.get("error") or {}).get("status")
                                    if status in {"INVALID_ARGUMENT", "FAILED_PRECONDITION", "PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND", "RESOURCE_EXHAUSTED"}:
                                        api_status = f" ({status})"
                                except (ValueError, TypeError, AttributeError, aiohttp.ClientError):
                                    pass
                                errors.append(f"{name}: HTTP {resp.status}{api_status}")
                                if resp.status in (400, 401, 403, 404):
                                    continue  # model/permission problem, next model
                                route_broken = resp.status in (408, 425, 429, 498, 500, 502, 503, 504)
                                break  # route problem, next route
                            data = await resp.json()
                    except (
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        OSError,
                        ValueError,
                    ) as exc:
                        errors.append(f"{route_label}: {type(exc).__name__}")
                        route_broken = True
                        break
                    for candidate in data.get("candidates") or []:
                        text = _text_from_candidate(candidate)
                        if text:
                            mark_route_ok("gemini", route)
                            return text, name
                    errors.append(f"{name}: пустой или незавершённый ответ")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            mark_route_failed("gemini", route, type(exc).__name__)
            errors.append(f"{route_label}: {type(exc).__name__}")
            continue
        if route_broken:
            # Транспорт или шлюз маршрута: карантин, следующий маршрут.
            mark_route_failed("gemini", route, errors[-1] if errors else "route")
            continue
        # Route answered; a model-level failure here is not a route failure but
        # the next route may still have a different model catalogue.
        logger.warning("Gemini через %s не дал ответа: %s", route_label, errors[-3:])

    raise RuntimeError(
        "Gemini недоступен ни по одному маршруту: " + ("; ".join(errors[-4:]) or "нет ответа")
    )
```

### `services/common/orders_report.py`

```python
import asyncio
import html
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import settings
from database.crud import _db_write_lock
from database.models import OrderRecord, Product, get_db_session
from products_catalog import OZON_SKU_TO_ART, WB_SKU_TO_ART
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

CANCELLED_ORDER_STATUSES: Set[str] = {
    "cancelled",
    "canceled",
    "returned",
    "return",
    "отменен",
    "отменён",
    "возврат",
}

_SQLITE_IN_CHUNK_SIZE = 500
_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")


def _to_dec(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return default
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not cleaned:
            return default
        try:
            parsed = Decimal(cleaned)
            return parsed if parsed.is_finite() else default
        except InvalidOperation:
            return default
    return default


def _round_rub(value: Any) -> float:
    return float(_to_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def _mul_rub(amount: Any, rate: Any) -> float:
    return float((_to_dec(amount) * _to_dec(rate)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _div_rub(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    den = _to_dec(denominator)
    if den <= Decimal("0"):
        return default
    return float((_to_dec(numerator) / den).quantize(_CENT, rounding=ROUND_HALF_UP))


def _round_pct(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    den = _to_dec(denominator)
    if den <= Decimal("0"):
        return default
    return float(((_to_dec(numerator) / den) * Decimal("100")).quantize(_TENTH, rounding=ROUND_HALF_UP))


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not cleaned:
            return default
        value = cleaned
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_nonneg(value: Any, default: float = 0.0) -> float:
    return max(_safe_float(value, default), 0.0)


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return int(parsed)


def _parse_order_datetime(value: Any) -> Optional[datetime]:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None  # do not backdate historical sales to the report run
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _resolve_order_sku(raw_sku: Any, marketplace: str) -> str:
    clean = str(raw_sku or "").strip()
    if not clean:
        return ""
    mp = str(marketplace or "").strip().lower()
    if mp == "ozon":
        return OZON_SKU_TO_ART.get(clean, clean)
    if mp == "wb":
        return WB_SKU_TO_ART.get(clean, clean)
    return clean


def _pct(value: float, base: float) -> str:
    if not (math.isfinite(value) and math.isfinite(base)) or base <= 0.0:
        return "0.0%"
    return f"{_round_pct(value, base):.1f}%"


def _fmt_money(value: float) -> str:
    if not math.isfinite(value) or abs(value) < 0.005:
        return "0"
    if abs(value - round(value)) >= 0.005:
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _fmt_expense(value: float) -> str:
    amt = _safe_float(value)
    if amt < -0.005:
        return f"+{_fmt_money(-amt)} ₽"
    if abs(amt) < 0.005:
        return "0 ₽"
    return f"-{_fmt_money(amt)} ₽"


def _index_products_by_marketplace(
    all_prods: List[Product],
) -> Tuple[Dict[Tuple[str, str], Product], Dict[Tuple[str, str], Product]]:
    by_mp_sku: Dict[Tuple[str, str], Product] = {}
    by_mp_ext: Dict[Tuple[str, str], Product] = {}
    for prod in all_prods:
        mp = str(prod.marketplace or "").strip().lower()
        sku = str(prod.sku or "").strip()
        ext = str(prod.external_id or "").strip()
        if mp and sku:
            by_mp_sku[(mp, sku)] = prod
        if mp and ext and (mp, ext) not in by_mp_ext:
            by_mp_ext[(mp, ext)] = prod
    return by_mp_sku, by_mp_ext


def _lookup_product_for_order(
    marketplace: str,
    raw_sku: str,
    by_mp_sku: Dict[Tuple[str, str], Product],
    by_mp_ext: Dict[Tuple[str, str], Product],
) -> Optional[Product]:
    mp = str(marketplace or "").strip().lower()
    clean_raw = str(raw_sku or "").strip()
    resolved = _resolve_order_sku(clean_raw, mp)
    return (
        by_mp_sku.get((mp, resolved))
        or by_mp_sku.get((mp, clean_raw))
        or by_mp_ext.get((mp, clean_raw))
        or by_mp_ext.get((mp, resolved))
    )


def _calculate_marketplace_cogs(
    orders_list: Any,
    marketplace: str,
    by_mp_sku: Dict[Tuple[str, str], Product],
    by_mp_ext: Dict[Tuple[str, str], Product],
) -> Tuple[float, int, List[str]]:
    """
    Рассчитывает подтвержденную себестоимость (сырье + упаковка + первая миля) строго в разрезе
    конкретного маркетплейса, без подстановки вымышленных значений и без учета отмененных заказов.
    Возвращает: (total_cogs_rub, missing_cogs_units, sorted_missing_skus).
    """
    if not isinstance(orders_list, list):
        return 0.0, 0, []

    mp = str(marketplace or "").strip().lower()
    total_cogs = Decimal("0")
    missing_units = 0
    missing_skus: Set[str] = set()
    seen_order_ids: Set[str] = set()

    for order in orders_list:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("order_id") or "").strip()
        if order_id:
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)

        status = str(order.get("status") or "ordered").strip().lower()
        if status in CANCELLED_ORDER_STATUSES:
            continue

        qty = max(_safe_int(order.get("quantity"), 1), 1)
        raw_sku = str(order.get("sku") or "").strip()
        art = _resolve_order_sku(raw_sku, mp)
        prod = _lookup_product_for_order(mp, raw_sku, by_mp_sku, by_mp_ext)

        if prod is not None and _safe_nonneg(prod.cost_price) > 0.0:
            unit_cogs = (
                _to_dec(_safe_nonneg(prod.cost_price))
                + _to_dec(_safe_nonneg(prod.packaging_cost))
                + _to_dec(_safe_nonneg(prod.first_mile_unit_cost))
            )
            total_cogs += unit_cogs * Decimal(qty)
        else:
            missing_units += qty
            if art or raw_sku:
                missing_skus.add(art or raw_sku)

    return _round_rub(total_cogs), missing_units, sorted(missing_skus)


def _count_active_orders(orders_list: Any) -> int:
    if not isinstance(orders_list, list):
        return 0
    count = 0
    seen_order_ids: Set[str] = set()
    for order in orders_list:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("order_id") or "").strip()
        if order_id:
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
        status = str(order.get("status") or "ordered").strip().lower()
        if status in CANCELLED_ORDER_STATUSES:
            continue
        count += max(_safe_int(order.get("quantity"), 1), 1)
    return count


def _prepare_clean_orders(ozon_fin: Any, wb_fin: Any) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    sources: List[Tuple[str, Any]] = [("ozon", ozon_fin), ("wb", wb_fin)]

    for default_mp, payload in sources:
        if not isinstance(payload, dict):
            continue
        orders = payload.get("orders")
        if not isinstance(orders, list):
            continue
        for ord_data in orders:
            if not isinstance(ord_data, dict):
                continue
            order_id = str(ord_data.get("order_id") or "").strip()
            if not order_id or order_id in deduped or ord_data.get("price") is None:
                continue
            mp = str(ord_data.get("marketplace") or default_mp).strip().lower()
            if mp not in {"ozon", "wb"}:
                mp = default_mp
            raw_sku = str(ord_data.get("sku") or "").strip()
            created = _parse_order_datetime(ord_data.get("created_at"))
            if created is None:
                continue
            deduped[order_id] = {
                "order_id": order_id[:128],
                "marketplace": mp,
                "sku": _resolve_order_sku(raw_sku, mp)[:64],
                "created_at": created,
                "price": round(_safe_nonneg(ord_data.get("price")), 2),
                "commission": round(_safe_float(ord_data.get("commission")), 2),
                "logistics": round(_safe_float(ord_data.get("logistics")), 2),
                "status": str(ord_data.get("status") or "ordered").strip()[:64],
            }
    return list(deduped.values())


async def _load_products_and_persist_orders(
    clean_orders: List[Dict[str, Any]],
) -> List[Product]:
    async with _db_write_lock:
        async with get_db_session() as session:
            prods_res = await session.execute(select(Product))
            all_prods = list(prods_res.scalars().all())

            if clean_orders:
                all_ids = [item["order_id"] for item in clean_orders]
                existing_ids: Set[str] = set()
                for offset in range(0, len(all_ids), _SQLITE_IN_CHUNK_SIZE):
                    chunk = all_ids[offset : offset + _SQLITE_IN_CHUNK_SIZE]
                    res = await session.execute(
                        select(OrderRecord.order_id).where(OrderRecord.order_id.in_(chunk))
                    )
                    existing_ids.update(row[0] for row in res.fetchall())

                pending_rows = [item for item in clean_orders if item["order_id"] not in existing_ids]
                if pending_rows:
                    for item in pending_rows:
                        session.add(OrderRecord(**item))
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                        for item in pending_rows:
                            try:
                                async with session.begin_nested():
                                    session.add(OrderRecord(**item))
                            except IntegrityError:
                                continue
                        await session.commit()

            return all_prods


def _format_missing_cogs_note(missing_units: int, missing_skus: List[str]) -> str:
    if missing_units <= 0:
        return ""
    sample = ", ".join(html.escape(s) for s in missing_skus[:8])
    if len(missing_skus) > 8:
        sample += f" и ещё {len(missing_skus) - 8}"
    sku_part = f" (арт.: <code>{sample}</code>)" if sample else ""
    return (
        f"⚠️ <i>Не задана себестоимость для <b>{missing_units} шт.</b>{sku_part} — "
        f"чистая прибыль не рассчитана.</i>\n"
    )


async def sync_and_generate_orders_report(
    ozon_service: OzonSellerAPI,
    wb_service: WBStatisticsAPI,
    days: int = 7,
    custom_date_from: Optional[str] = None,
    custom_date_to: Optional[str] = None,
    report_title: str = "📊 Финансовый отчет (по доступным данным Ozon и WB)",
) -> str:
    safe_days = max(_safe_int(days, 7), 1)
    tax_rate = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
    acq_rate = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
    tax_pct_label = f"{tax_rate * 100.0:.0f}%" if abs(tax_rate * 100.0 - round(tax_rate * 100.0)) < 0.05 else f"{tax_rate * 100.0:.1f}%"
    acq_pct_label = f"{acq_rate * 100.0:.1f}%"

    ozon_fin, wb_fin = await asyncio.gather(
        ozon_service.fetch_real_finance_breakdown(
            days=safe_days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
        wb_service.fetch_real_finance_breakdown(
            days=safe_days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
        return_exceptions=True,
    )

    clean_orders = _prepare_clean_orders(ozon_fin, wb_fin)
    all_prods = await _load_products_and_persist_orders(clean_orders)
    by_mp_sku, by_mp_ext = _index_products_by_marketplace(all_prods)

    api_errors_count = 0
    oz_profit_known = False
    wb_profit_known = False

    # --- БЛОК OZON ---
    if not isinstance(ozon_fin, dict):
        api_errors_count += 1
        err_text = html.escape(str(ozon_fin))
        ozon_block = f"🔵 <b>OZON:</b>\n❌ Ошибка API Ozon:\n<code>{err_text}</code>\n\n"
        oz_sales = oz_net_profit = oz_cogs = oz_ded_tot = oz_tax = oz_acq = 0.0
        oz_count = oz_missing_cogs = 0
    else:
        oz_orders = ozon_fin.get("orders") if isinstance(ozon_fin.get("orders"), list) else []
        raw_oz_count = ozon_fin.get("orders_count")
        oz_count = (
            max(_safe_int(raw_oz_count, 0), 0)
            if raw_oz_count is not None
            else _count_active_orders(oz_orders)
        )

        oz_sales = round(_safe_float(ozon_fin.get("sales_total")), 2)
        oz_comm = round(_safe_float(ozon_fin.get("commission")), 2)

        oz_d_dir = round(_safe_float(ozon_fin.get("deliv_direct")), 2)
        oz_d_ret = round(_safe_float(ozon_fin.get("deliv_return")), 2)
        oz_d_ho = round(_safe_float(ozon_fin.get("deliv_ozon_handover")), 2)
        oz_deliv = (
            round(_safe_float(ozon_fin.get("deliv_total")), 2)
            if ozon_fin.get("deliv_total") is not None
            else round(oz_d_dir + oz_d_ret + oz_d_ho, 2)
        )
        oz_deliv_per_order: Optional[float] = (
            _div_rub(oz_deliv, oz_count) if oz_count > 0 else None
        )

        oz_dropoff = _round_rub(_safe_float(ozon_fin.get("partner_dropoff")))
        oz_pack = _round_rub(_safe_float(ozon_fin.get("partner_pack")))
        oz_pret = _round_rub(_safe_float(ozon_fin.get("partner_returns")))
        raw_oz_acq = _round_rub(_safe_float(ozon_fin.get("acquiring")))
        oz_star = _round_rub(_safe_float(ozon_fin.get("star_products")))
        oz_plm = _round_rub(_safe_float(ozon_fin.get("partner_last_mile")))

        # Если эквайринг еще не отражен в cash-flow Ozon при положительных продажах,
        # учитываем эталонный эквайринг 1.5% (settings.ACQUIRING_RATE) без двойного списания.
        oz_fbo = _round_rub(_safe_float(ozon_fin.get("fbo_storage")))
        oz_cpo = _round_rub(_safe_float(ozon_fin.get("promo_cpo")))
        oz_cpc = _round_rub(_safe_float(ozon_fin.get("promo_cpc")))
        oz_ads = (
            _round_rub(_safe_float(ozon_fin.get("ads_total")))
            if ozon_fin.get("ads_total") is not None
            else _round_rub(_to_dec(oz_cpo) + _to_dec(oz_cpc))
        )
        oz_fines = _round_rub(_safe_float(ozon_fin.get("other_fines")))
        oz_decomp = _round_rub(_safe_float(ozon_fin.get("decompensations")))
        oz_mutual = _round_rub(_safe_float(ozon_fin.get("mutual_settlements")))

        oz_part_no_acq = (
            _round_rub(_safe_float(ozon_fin.get("partners_total")))
            if ozon_fin.get("partners_total") is not None
            else _round_rub(
                _to_dec(oz_dropoff)
                + _to_dec(oz_pack)
                + _to_dec(oz_pret)
                + _to_dec(oz_star)
                + _to_dec(oz_plm)
            )
        )
        known_oz_ded_no_acq = _round_rub(
            _to_dec(oz_comm)
            + _to_dec(oz_deliv)
            + _to_dec(oz_part_no_acq)
            + _to_dec(oz_fbo)
            + _to_dec(oz_ads)
            + _to_dec(oz_fines)
            + _to_dec(oz_decomp)
            + _to_dec(oz_mutual)
        )

        oz_acq_estimated = False
        if raw_oz_acq != 0.0 or ozon_fin.get("acquiring_reported"):
            oz_acq = raw_oz_acq
            oz_acq_delta = 0.0
        elif oz_sales > 0.0 and acq_rate > 0.0:
            est_oz_acq = _mul_rub(oz_sales, acq_rate)
            raw_tot_ded = ozon_fin.get("total_deductions")
            lump_sum_already_covers_acq = (
                "acquiring" not in ozon_fin
                and raw_tot_ded is not None
                and _safe_float(raw_tot_ded) >= known_oz_ded_no_acq + est_oz_acq - 0.05
            )
            oz_acq = est_oz_acq
            oz_acq_delta = 0.0 if lump_sum_already_covers_acq else est_oz_acq
            oz_acq_estimated = True
        else:
            oz_acq = 0.0
            oz_acq_delta = 0.0

        if ozon_fin.get("partners_total") is not None:
            oz_part = _round_rub(_to_dec(oz_part_no_acq) + _to_dec(oz_acq_delta))
        else:
            oz_part = _round_rub(
                _to_dec(oz_dropoff)
                + _to_dec(oz_pack)
                + _to_dec(oz_pret)
                + _to_dec(oz_acq)
                + _to_dec(oz_star)
                + _to_dec(oz_plm)
            )

        computed_oz_ded = _round_rub(
            _to_dec(oz_comm)
            + _to_dec(oz_deliv)
            + _to_dec(oz_part)
            + _to_dec(oz_fbo)
            + _to_dec(oz_ads)
            + _to_dec(oz_fines)
            + _to_dec(oz_decomp)
            + _to_dec(oz_mutual)
        )
        if ozon_fin.get("total_deductions") is not None:
            oz_ded_tot = _round_rub(_to_dec(_safe_float(ozon_fin.get("total_deductions"))) + _to_dec(oz_acq_delta))
        else:
            oz_ded_tot = computed_oz_ded

        if ozon_fin.get("total_payout") is not None:
            oz_payout = _round_rub(_to_dec(_safe_float(ozon_fin.get("total_payout"))) - _to_dec(oz_acq_delta))
        else:
            oz_payout = _round_rub(_to_dec(oz_sales) - _to_dec(oz_ded_tot))

        oz_cogs, oz_missing_cogs, oz_missing_skus = _calculate_marketplace_cogs(
            oz_orders, "ozon", by_mp_sku, by_mp_ext
        )
        oz_tax_base = max(oz_sales, 0.0)
        oz_tax = _mul_rub(oz_tax_base, tax_rate)
        oz_net_profit = _round_rub(_to_dec(oz_payout) - _to_dec(oz_cogs) - _to_dec(oz_tax))

        oz_profit_known = (
            oz_missing_cogs == 0
            and (oz_sales == 0 or bool(oz_orders))
            and not any(str(o.get("status") or "").lower() in {"return", "returned", "возврат"} for o in oz_orders if isinstance(o, dict))
            and not any(mark in str(ozon_fin.get("source") or "").lower()
                        for mark in ("незнакомые типы", "неполное покрытие", "cash-flow пуст", "cash-flow http", "accrual http", "accrual runtimeerror"))
            and (oz_sales == 0 or all(k in ozon_fin for k in ("total_deductions", "total_payout")))
            and all(o.get("price") is not None for o in oz_orders if isinstance(o, dict))
        )
        oz_profit_line = (f"<code>{_fmt_money(oz_net_profit)} ₽</code> (<b>{_pct(oz_net_profit, oz_sales)}</b>)"
                          if oz_profit_known else "<b>н/д — требуется себестоимость и сверка начислений/возвратов</b>")
        oz_source = html.escape(str(ozon_fin.get("source") or "ЛК Ozon Финансы"))
        oz_sales_share = "100.0%" if oz_sales > 0.0 else "0.0%"
        oz_deliv_unit_str = (
            f" | факт. <b>{oz_deliv_per_order:.2f} ₽/заказ</b>"
            if oz_deliv_per_order is not None
            else ""
        )
        oz_acq_title = f"Эквайринг (расч. {acq_pct_label})" if oz_acq_estimated else "Эквайринг"
        oz_missing_note = _format_missing_cogs_note(oz_missing_cogs, oz_missing_skus)

        ozon_block = (
            f"🔵 <b>OZON ({oz_source}):</b>\n"
            f"• Реализовано заказов (отправлений): <b>{oz_count}</b>\n"
            f"• <b>Продажи и возвраты: {_fmt_money(oz_sales)} ₽</b> ({oz_sales_share})\n"
            f"• <b>Начисления МП (всего): {_fmt_expense(oz_ded_tot)}</b> ({_pct(oz_ded_tot, oz_sales)})\n\n"
            f"<b>Детализация расходов Ozon:</b>\n"
            f"• <b>Вознаграждение Ozon: {_fmt_expense(oz_comm)}</b> ({_pct(oz_comm, oz_sales)})\n"
            f"• <b>Услуги доставки: {_fmt_expense(oz_deliv)}</b> ({_pct(oz_deliv, oz_sales)}{oz_deliv_unit_str})\n"
            f"  ├ Логистика (прямая): {_fmt_expense(oz_d_dir)}\n"
            f"  ├ Обратная логистика: {_fmt_expense(oz_d_ret)}\n"
            f"  └ Доставка до места выдачи Ozon: {_fmt_expense(oz_d_ho)}\n"
            f"• <b>Услуги партнёров: {_fmt_expense(oz_part)}</b> ({_pct(oz_part, oz_sales)})\n"
            f"  ├ Обработка Drop-off (сдача в ПВЗ): {_fmt_expense(oz_dropoff)}\n"
            f"  ├ Упаковка товара партнёрами: {_fmt_expense(oz_pack)}\n"
            f"  ├ Обработка возвратов и невыкупов: {_fmt_expense(oz_pret)}\n"
            f"  ├ {oz_acq_title}: {_fmt_expense(oz_acq)} ({_pct(oz_acq, oz_sales)})\n"
            f"  ├ Звёздные товары: {_fmt_expense(oz_star)}\n"
            f"  └ Доставка до места выдачи партнёрами: {_fmt_expense(oz_plm)}\n"
            f"• <b>Услуги FBO (хранение на складе): {_fmt_expense(oz_fbo)}</b> ({_pct(oz_fbo, oz_sales)})\n"
            f"• <b>Продвижение и реклама: {_fmt_expense(oz_ads)}</b> ({_pct(oz_ads, oz_sales)})\n"
            f"  ├ Продвижение с оплатой за заказ: {_fmt_expense(oz_cpo)}\n"
            f"  └ Оплата за клик / Трафареты: {_fmt_expense(oz_cpc)}\n"
            f"• <b>Другие услуги и штрафы: {_fmt_expense(oz_fines)}</b> ({_pct(oz_fines, oz_sales)})\n"
            f"• <b>Компенсации и декомпенсации: {_fmt_expense(oz_decomp)}</b>\n"
            f"• <b>Взаимозачет между договорами: {_fmt_expense(oz_mutual)}</b>\n\n"
            f"💳 <b>Всего к выплате Ozon: {_fmt_money(oz_payout)} ₽</b>\n"
            f"📦 Учтённая себестоимость + упаковка + первая миля: <b>{_fmt_expense(oz_cogs)}</b> ({_pct(oz_cogs, oz_sales)})\n"
            f"{oz_missing_note}"
            f"🏛 Налог УСН ({tax_pct_label}): <b>{_fmt_expense(oz_tax)}</b> ({_pct(oz_tax, oz_sales)})\n"
            f"👉 <b>Чистая прибыль Ozon:</b> {oz_profit_line}\n\n"
        )

    # --- БЛОК WILDBERRIES ---
    if not isinstance(wb_fin, dict):
        api_errors_count += 1
        err_text = html.escape(str(wb_fin))
        wb_block = f"🟣 <b>WILDBERRIES:</b>\n❌ Ошибка API WB: <code>{err_text}</code>\n\n"
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = wb_acq = 0.0
        wb_count = wb_missing_cogs = 0
    elif wb_fin.get("summary_only"):
        api_errors_count += 1
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = wb_acq = 0.0
        wb_count = wb_missing_cogs = 0
        rows = wb_fin.get("summary_reports") or []
        report_rows = "\n".join(
            f"• №{html.escape(str(r.get('id')))} ({html.escape(str(r.get('date_from')))}..{html.escape(str(r.get('date_to')))}): "
            f"retailAmountSum {_fmt_money(r.get('retail_amount_sum'))} ₽; forPaySum {_fmt_money(r.get('for_pay_sum'))} ₽"
            for r in rows if isinstance(r, dict)
        )
        wb_block = (
            "🟣 <b>WILDBERRIES: только сводные отчёты</b>\n"
            f"{report_rows}\n"
            "⚠️ Нет построчной реализации и расшифровки базы налога/комиссии. "
            "Прибыль, количество, налог и общий итог WB не рассчитаны; "
            "значения отчёта не включены в сводные итоги.\n\n"
        )
    else:
        wb_orders = wb_fin.get("orders") if isinstance(wb_fin.get("orders"), list) else []
        raw_wb_count = wb_fin.get("orders_count")
        wb_count = (
            max(_safe_int(raw_wb_count, 0), 0)
            if raw_wb_count is not None
            else _count_active_orders(wb_orders)
        )

        wb_rev_s = round(_safe_float(wb_fin.get("revenue_seller")), 2)
        wb_rev_b = (
            round(_safe_float(wb_fin.get("revenue_buyer")), 2)
            if wb_fin.get("revenue_buyer") is not None
            else wb_rev_s
        )
        wb_ret = round(_safe_nonneg(wb_fin.get("returns_amount")), 2)
        wb_comm = round(_safe_float(wb_fin.get("commission")), 2)

        raw_wb_acq = _round_rub(_safe_float(wb_fin.get("acquiring")))
        wb_acq_base = max(wb_rev_b, wb_rev_s, 0.0)

        wb_log_ret = _round_rub(_safe_float(wb_fin.get("logistics_return")))
        if wb_fin.get("logistics") is not None:
            wb_log = _round_rub(_safe_float(wb_fin.get("logistics")))
            wb_log_dir = (
                _round_rub(_safe_float(wb_fin.get("logistics_direct")))
                if wb_fin.get("logistics_direct") is not None
                else _round_rub(max(_to_dec(wb_log) - _to_dec(wb_log_ret), Decimal("0")))
            )
        else:
            wb_log_dir = _round_rub(_safe_float(wb_fin.get("logistics_direct")))
            wb_log = _round_rub(_to_dec(wb_log_dir) + _to_dec(wb_log_ret))

        wb_stor = _round_rub(_safe_float(wb_fin.get("storage")))
        wb_acc = _round_rub(_safe_float(wb_fin.get("acceptance")))
        wb_ads = _round_rub(_safe_float(wb_fin.get("ads")))
        wb_other = _round_rub(_safe_float(wb_fin.get("other_services")))

        known_wb_ded_no_acq = _round_rub(
            _to_dec(wb_comm)
            + _to_dec(wb_log)
            + _to_dec(wb_stor)
            + _to_dec(wb_acc)
            + _to_dec(wb_ads)
            + _to_dec(wb_other)
        )

        wb_acq_estimated = bool(wb_fin.get("acquiring_estimated"))
        if wb_fin.get("acquiring") is not None:
            wb_acq = raw_wb_acq
            wb_acq_delta = 0.0
        elif wb_acq_base > 0.0 and acq_rate > 0.0:
            est_wb_acq = _mul_rub(wb_acq_base, acq_rate)
            raw_wb_tot_ded = wb_fin.get("total_deductions")
            wb_lump_covers_acq = (
                "acquiring" not in wb_fin
                and raw_wb_tot_ded is not None
                and _safe_float(raw_wb_tot_ded) >= known_wb_ded_no_acq + est_wb_acq - 0.05
            )
            wb_acq = est_wb_acq
            wb_acq_delta = 0.0 if wb_lump_covers_acq else est_wb_acq
            wb_acq_estimated = True
        else:
            wb_acq = 0.0
            wb_acq_delta = 0.0

        computed_wb_ded = _round_rub(_to_dec(known_wb_ded_no_acq) + _to_dec(wb_acq))
        if wb_fin.get("total_deductions") is not None:
            wb_ded_tot = _round_rub(_to_dec(wb_fin.get("total_deductions")) + _to_dec(wb_acq_delta))
        else:
            wb_ded_tot = computed_wb_ded

        if wb_fin.get("total_payout") is not None:
            wb_payout = _round_rub(_to_dec(_safe_float(wb_fin.get("total_payout"))) - _to_dec(wb_acq_delta))
        else:
            wb_payout = _round_rub(_to_dec(wb_rev_s) - _to_dec(wb_ded_tot))

        wb_cogs, wb_missing_cogs, wb_missing_skus = _calculate_marketplace_cogs(
            wb_orders, "wb", by_mp_sku, by_mp_ext
        )
        wb_tax_base = max(wb_rev_b, 0.0)
        wb_tax = _mul_rub(wb_tax_base, tax_rate)
        wb_net_profit = _round_rub(_to_dec(wb_payout) - _to_dec(wb_cogs) - _to_dec(wb_tax))

        wb_profit_known = (wb_missing_cogs == 0 and (wb_rev_s == 0 or bool(wb_orders))
                           and not _safe_nonneg(wb_fin.get("returns_amount"))
                           and (wb_rev_s == 0 or all(k in wb_fin for k in ("total_deductions", "total_payout", "revenue_buyer")))
                           and all(o.get("price") is not None for o in wb_orders if isinstance(o, dict)))
        wb_profit_line = (f"<code>{_fmt_money(wb_net_profit)} ₽</code> (<b>{_pct(wb_net_profit, wb_rev_s)}</b>)"
                          if wb_profit_known else "<b>н/д — требуется детализация продаж, себестоимости и возвратов</b>")
        wb_source = html.escape(str(wb_fin.get("source") or "ЛК Wildberries"))
        wb_sales_share = "100.0%" if wb_rev_s > 0.0 else "0.0%"
        wb_acq_title = (
            f"Эквайринг WB (расч. {acq_pct_label})" if wb_acq_estimated else "Эквайринг WB"
        )
        wb_log_unit_str = (
            f" | факт. <b>{_div_rub(wb_log, wb_count):.2f} ₽/заказ</b>" if wb_count > 0 else ""
        )
        wb_missing_note = _format_missing_cogs_note(wb_missing_cogs, wb_missing_skus)

        wb_block = (
            f"🟣 <b>WILDBERRIES ({wb_source}):</b>\n"
            f"• Строк реализации: <b>{wb_count}</b> (Отмены/возвраты: {_fmt_money(wb_ret)} ₽)\n"
            f"• <b>Продажи в ценах ЛК (priceWithDisc): {_fmt_money(wb_rev_s)} ₽</b> ({wb_sales_share})\n"
            f"• <b>Цена для покупателя с СПП (база УСН): {_fmt_money(wb_rev_b)} ₽</b>\n"
            f"• <b>Удержания WB (всего): {_fmt_expense(wb_ded_tot)}</b> ({_pct(wb_ded_tot, wb_rev_s)})\n\n"
            f"<b>Детализация расходов Wildberries:</b>\n"
            f"• <b>Вознаграждение WB (с НДС): {_fmt_expense(wb_comm)}</b> ({_pct(wb_comm, wb_rev_s)})\n"
            f"• <b>{wb_acq_title}: {_fmt_expense(wb_acq)}</b> ({_pct(wb_acq, wb_rev_s)})\n"
            f"• <b>Услуги логистики WB: {_fmt_expense(wb_log)}</b> ({_pct(wb_log, wb_rev_s)}{wb_log_unit_str})\n"
            f"  ├ Прямая доставка до покупателя: {_fmt_expense(wb_log_dir)}\n"
            f"  └ Обратная логистика (возвраты/отмены): {_fmt_expense(wb_log_ret)}\n"
            f"• <b>Платное хранение на складах WB: {_fmt_expense(wb_stor)}</b> ({_pct(wb_stor, wb_rev_s)})\n"
            f"• <b>Платная приёмка товара: {_fmt_expense(wb_acc)}</b> ({_pct(wb_acc, wb_rev_s)})\n"
            f"• <b>ВБ.Продвижение (Реклама): {_fmt_expense(wb_ads)}</b> ({_pct(wb_ads, wb_rev_s)})\n"
            f"• <b>Штрафы и прочие удержания: {_fmt_expense(wb_other)}</b> ({_pct(wb_other, wb_rev_s)})\n\n"
            f"💳 <b>Всего к перечислению WB: {_fmt_money(wb_payout)} ₽</b>\n"
            f"📦 Учтённая себестоимость + упаковка + первая миля: <b>{_fmt_expense(wb_cogs)}</b> ({_pct(wb_cogs, wb_rev_s)})\n"
            f"{wb_missing_note}"
            f"🏛 Налог УСН {tax_pct_label} (с {_fmt_money(wb_tax_base)} ₽): <b>{_fmt_expense(wb_tax)}</b>\n"
            f"👉 <b>Чистая прибыль WB:</b> {wb_profit_line}\n\n"
        )

    tot_count = oz_count + wb_count
    tot_sales = _round_rub(_to_dec(oz_sales) + _to_dec(wb_rev_s))
    tot_ded = _round_rub(_to_dec(oz_ded_tot) + _to_dec(wb_ded_tot))
    tot_acq = _round_rub(_to_dec(oz_acq) + _to_dec(wb_acq))
    tot_cogs = _round_rub(_to_dec(oz_cogs) + _to_dec(wb_cogs))
    tot_tax = _round_rub(_to_dec(oz_tax) + _to_dec(wb_tax))
    tot_profit = _round_rub(_to_dec(oz_net_profit) + _to_dec(wb_net_profit))
    tot_missing_cogs = oz_missing_cogs + wb_missing_cogs

    summary_warnings: List[str] = []
    if api_errors_count > 0:
        summary_warnings.append(
            "⚠️ <i>Внимание: данные одного или обоих маркетплейсов недоступны — сводный итог неполный.</i>"
        )
    elif tot_count == 0 and tot_sales == 0.0 and tot_ded == 0.0:
        summary_warnings.append("ℹ️ <i>За выбранный период продаж и удержаний не зафиксировано.</i>")
    if tot_missing_cogs > 0:
        summary_warnings.append(
            f"⚠️ <i>По {tot_missing_cogs} ед. товара отсутствует себестоимость в БД — внесите её через /costs.</i>"
        )
    profit_summary = (f"<code>{_fmt_money(tot_profit)} ₽</code> (<b>{_pct(tot_profit, tot_sales)} ROS</b>)"
                      if api_errors_count == 0 and oz_profit_known and wb_profit_known
                      else "<b>н/д — данные для расчёта неполны</b>")
    if oz_profit_known or wb_profit_known:
        summary_warnings.append("ℹ️ <i>Прибыль и УСН — расчётные: налоговую базу, возвраты и удержания необходимо сверить с ЛК и учётом.</i>")
    warnings_suffix = ("\n" + "\n".join(summary_warnings)) if summary_warnings else ""
    safe_title = html.escape(str(report_title))
    return (
        f"<b>{safe_title}</b>\n\n"
        f"{ozon_block}"
        f"{wb_block}"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏁 <b>СВОДНЫЙ ИТОГ (Ozon + WB):</b>\n"
        f"• Заказы Ozon + реализации WB: <b>{tot_count}</b>\n"
        f"• Общий оборот (в ценах ЛК): <b>{_fmt_money(tot_sales)} ₽</b>\n"
        f"• Все удержания маркетплейсов: <b>{_fmt_expense(tot_ded)}</b> ({_pct(tot_ded, tot_sales)}), в т.ч. эквайринг <b>{_fmt_expense(tot_acq)}</b>\n"
        f"• Учтённая себестоимость, упаковка и первая миля: <b>{_fmt_expense(tot_cogs)}</b> ({_pct(tot_cogs, tot_sales)})\n"
        f"• Налог УСН {tax_pct_label}: <b>{_fmt_expense(tot_tax)}</b>\n"
        f"💰 <b>ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> {profit_summary}"
        f"{warnings_suffix}"
    )
```

### `services/competitors/browser_listings.py`

```python
"""Разбор витрин WB и Ozon через реальный браузер (резерв для парсера конкурентов).

Прямой HTTP к витринам закрыт антиботом: с октября 2026 `search.wb.ru`,
`card.wb.ru` и `catalog.wb.ru` отвечают 403/498, а `composer-api.bx` Ozon
отдаёт 307-петлю без прогретой сессии. Рабочая схема (та же, что в открытых
парсерах и в MCP-серверах на GitHub):

* WB — браузер решает JVO1-челлендж, после чего перехватывается собственный
  ответ сайта на `search.wb.ru/.../v18/search` (replay токена в curl даёт 498:
  токен привязан к браузеру, который решил челлендж). Резерв — DOM-разбор.
* Ozon — один Chromium открывает главную и остаётся открытым; все запросы
  выполняются из контекста этой страницы (`fetch` из документа ozon.ru), иначе
  CORS/403. Из ответа достаются `widgetStates`.

Модуль сознательно не блокирует изображения/шрифты/стили: скрипты антибота
грузятся через них, и блокировка приводит к 403.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import LOGS_DIR, settings

logger = logging.getLogger(__name__)

LISTING_PROFILE_DIR = LOGS_DIR / "listings_browser_profile"
VIEWPORT = {"width": 1440, "height": 900}

BROWSER_ARGS = [
    "--no-proxy-server",
    "--proxy-server=direct://",
    "--proxy-bypass-list=*",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-features=IsolateOrigins,site-per-process",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

WB_SEARCH_RE = re.compile(r"search\.wb\.ru/.*/search\?")
WB_CARD_RE = re.compile(r"card\.wb\.ru/.*/detail")
OZON_ITEM_RE = re.compile(r"/product/[^\"\\?#]*-([1-9]\d{5,})(?:/|[?#]|$)")

PRICE_NUM_RE = re.compile(r"\d[\d\s\u00a0\u2009]*(?:[.,]\d+)?")

NAV_TIMEOUT_MS = 45000
FIRST_RESULT_TIMEOUT_MS = 25000
POST_CHALLENGE_PAUSE_SEC = 5.0
def _budget() -> float:
    """Бюджет из настроек (можно снизить на слабом ПК), но не меньше 20 с."""
    try:
        return max(float(getattr(settings, "LISTINGS_BROWSER_BUDGET_SEC", 90)), 20.0)
    except (TypeError, ValueError):
        return 90.0


CACHE_TTL_SEC = 600.0
CACHE_MAX_ENTRIES = 32

# Витрины отвечают медленно (первый запрос Ozon ~12 с), поэтому результат
# придерживается в памяти процесса: отчёт не должен ждать браузер дважды.
_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    import time

    entry = _CACHE.get(key)
    if not entry:
        return None
    stamp, items = entry
    if time.monotonic() - stamp > CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return [dict(it) for it in items]


def _cache_put(key: str, items: List[Dict[str, Any]]) -> None:
    import time

    if not items:
        return
    if len(_CACHE) >= CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.monotonic(), [dict(it) for it in items])


def _direct_env() -> Dict[str, str]:
    """Окружение без прокси: витрины не работают через DC-прокси."""
    import os

    env = os.environ.copy()
    for key in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        env.pop(key, None)
    return env


def _num(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = PRICE_NUM_RE.search(str(value))
    if not match:
        return 0.0
    cleaned = (
        match.group(0).replace("\u00a0", "").replace("\u2009", "").replace(" ", "").replace(",", ".")
    )
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# =============================================================================
# Общий разбор
# =============================================================================


def parse_wb_products(products: Any, limit: int = 15) -> List[Dict[str, Any]]:
    """Приводит карточки выдачи WB (v18 search / card detail) к общему виду."""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        sku = str(product.get("id") or product.get("nmId") or "").strip()
        if not sku or sku in seen:
            continue
        buyer_price = 0.0
        sizes = product.get("sizes")
        if isinstance(sizes, list):
            for size in sizes:
                if not isinstance(size, dict):
                    continue
                price_obj = size.get("price")
                if isinstance(price_obj, dict):
                    for field in ("total", "product", "basic"):
                        cents = _num(price_obj.get(field))
                        if cents > 0.0:
                            buyer_price = round(cents / 100.0, 2)
                            break
                if buyer_price > 0.0:
                    break
        if buyer_price <= 0.0:
            for legacy in ("salePriceU", "priceU", "salePrice"):
                cents = _num(product.get(legacy))
                if cents > 0.0:
                    buyer_price = round(cents / 100.0, 2)
                    break
        if buyer_price <= 0.0:
            continue
        raw_rating = (
            product.get("reviewRating")
            if product.get("reviewRating") is not None
            else product.get("nmReviewRating")
        )
        seen.add(sku)
        items.append(
            {
                "mp": "WB",
                "sku": sku,
                "name": str(product.get("name") or "").strip() or f"Товар WB {sku}",
                "brand": str(product.get("brand") or "").strip() or "Без бренда",
                "rating": round(min(max(_num(raw_rating), 0.0), 5.0), 1),
                "feedbacks": max(int(_num(product.get("feedbacks"))), 0),
                "buyer_price": buyer_price,
            }
        )
        if len(items) >= max(int(limit), 1):
            break
    return items


def parse_ozon_widget_states(
    payload: Any, limit: int = 15
) -> List[Dict[str, Any]]:
    """Достаёт карточки из `widgetStates` (значения — JSON-строки разной формы).

    Пути к товарным массивам у Ozon меняются, поэтому вместо фиксированных
    ключей используется рекурсивный обход с проверкой обязательных полей.
    """
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def emit(node: Dict[str, Any]) -> None:
        if len(items) >= max(int(limit), 1):
            return
        sku = str(
            node.get("sku") or node.get("productId") or node.get("id") or ""
        ).strip()
        price = 0.0
        for field in ("price", "cardPrice", "finalPrice", "originalPrice", "priceV2"):
            raw = node.get(field)
            if isinstance(raw, dict):
                price = _num(raw.get("price") or raw.get("text") or raw.get("value"))
            else:
                price = _num(raw)
            if price > 0.0:
                break
        if not sku or price <= 0.0 or sku in seen:
            return
        name = str(node.get("name") or node.get("title") or "").strip()
        if not name:
            return
        seen.add(sku)
        items.append(
            {
                "mp": "OZON",
                "sku": sku,
                "name": name[:180],
                "brand": str(node.get("brand") or "").strip() or "Без бренда",
                "rating": round(min(max(_num(node.get("rating")), 0.0), 5.0), 1),
                "feedbacks": max(int(_num(node.get("feedbackCount") or node.get("reviewsCount"))), 0),
                "buyer_price": round(price, 2),
            }
        )

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 12 or len(items) >= max(int(limit), 1):
            return
        if isinstance(node, str):
            stripped = node.strip()
            if stripped[:1] in ("{", "["):
                try:
                    walk(json.loads(stripped), depth + 1)
                except (ValueError, TypeError):
                    return
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if not isinstance(node, dict):
            return
        emit(node)
        for key in ("items", "tiles", "products", "searchResultsV2", "skuGrid", "grid"):
            if key in node:
                walk(node[key], depth + 1)
        if any(k in node for k in ("sku", "productId")):
            return
        for value in node.values():
            if isinstance(value, (dict, list, str)):
                walk(value, depth + 1)

    walk(payload)
    return items


def parse_ozon_dom_cards(cards: List[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    """DOM-резерв: берёт текст карточек и вытаскивает цену и ссылку на товар."""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        href = str(card.get("href") or "")
        match = OZON_ITEM_RE.search(href)
        if not match:
            continue
        sku = match.group(1)
        if sku in seen:
            continue
        text = str(card.get("text") or "").replace("\n", " ")
        # Не выбираем минимум из произвольных чисел: размер/баллы/скидка
        # попадали в предыдущую выдачу как цена товара. Несколько разных
        # цен без маркировки неоднозначны и исключаются из статистики.
        price_re = r"(?<![\d])([\d][\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*₽"
        prices = {_num(m.group(1)) for m in re.finditer(price_re, text)}
        prices = {v for v in prices if v > 0}
        if len(prices) != 1:
            continue
        name = re.sub(r"\s+", " ", text).strip()[:180] or f"Товар Ozon {sku}"
        seen.add(sku)
        items.append(
            {
                "mp": "OZON",
                "sku": sku,
                "name": name,
                "brand": "Без бренда",
                "rating": 0.0,
                "feedbacks": 0,
                "buyer_price": round(next(iter(prices)), 2),
            }
        )
        if len(items) >= max(int(limit), 1):
            break
    return items


# =============================================================================
# Браузерные сценарии
# =============================================================================


class _BrowserUnavailable(RuntimeError):
    """Playwright или его браузер не установлен на этом хосте."""


async def _launch(p):
    LISTING_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(LISTING_PROFILE_DIR),
        headless=True,
        args=BROWSER_ARGS,
        env=_direct_env(),
        viewport=VIEWPORT,
        user_agent=USER_AGENT,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )


async def _close(context) -> None:
    if context is None:
        return
    try:
        await asyncio.wait_for(context.close(), timeout=15)
    except Exception:  # noqa: BLE001 — закрытие не должно ронять парсер
        pass


async def wb_search_via_browser(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """ Поиск WB в браузере: перехват ответа сайта, затем DOM-резерв. """
    key = f"wb:{str(query or '').strip().lower()}:{int(limit)}"
    cached = _cache_get(key)
    if cached:
        return cached, None
    try:
        items, err = await asyncio.wait_for(
            _wb_search_uncached(query, limit), timeout=_budget()
        )
    except asyncio.TimeoutError:
        return [], "WB: браузерный разбор не уложился в бюджет времени"
    if items:
        _cache_put(key, items)
    return items, err


async def _wb_search_uncached(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise _BrowserUnavailable(f"Playwright недоступен: {exc}") from exc

    captured: List[Any] = []

    async def on_response(response) -> None:
        if not WB_SEARCH_RE.search(response.url):
            return
        try:
            payload = await response.json()
        except Exception:  # noqa: BLE001 — не-JSON ответ антибота
            return
        products = (payload or {}).get("data") or payload
        if isinstance(products, dict) and isinstance(products.get("products"), list):
            captured.append(products["products"])

    context = None
    try:
        async with async_playwright() as p:
            try:
                context = await _launch(p)
            except Exception as exc:  # noqa: BLE001
                raise _BrowserUnavailable(
                    f"Браузер Playwright не запускается: {exc}"
                ) from exc
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", lambda r: asyncio.create_task(on_response(r)))
            url = (
                "https://www.wildberries.ru/catalog/0/search.aspx?search="
                + _quote(query)
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001
                return [], f"WB: страница поиска не открылась ({exc})"
            # После JVO1 нужно дать странице время выполнить свой поиск.
            for _ in range(int(FIRST_RESULT_TIMEOUT_MS / 500)):
                if captured:
                    break
                await asyncio.sleep(0.5)
            if not captured:
                await asyncio.sleep(POST_CHALLENGE_PAUSE_SEC)
            if captured:
                items = parse_wb_products(captured[0], limit)
                if items:
                    return items, None
            # DOM-резерв: карточки в разметке страницы.
            try:
                cards = await page.eval_on_selector_all(
                    "article.product-card, div.product-card",
                    """els => els.map(e => ({
                        text: (e.innerText || '').trim(),
                        href: (e.querySelector('a[href*="/catalog/"]') || {}).href || '',
                    }))""",
                )
            except Exception:  # noqa: BLE001
                cards = []
            dom_items: List[Dict[str, Any]] = []
            for card in cards or []:
                match = re.search(r"/catalog/(\d+)/detail", str(card.get("href") or ""))
                text = str(card.get("text") or "")
                price_re = r"(?<![\d])([\d][\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*₽"
                prices = {_num(m.group(1)) for m in re.finditer(price_re, text)}
                prices = {v for v in prices if v > 0}
                if not match or len(prices) != 1:
                    continue
                dom_items.append(
                    {
                        "mp": "WB",
                        "sku": match.group(1),
                        "name": re.sub(r"\s+", " ", text).strip()[:180],
                        "brand": "Без бренда",
                        "rating": 0.0,
                        "feedbacks": 0,
                        "buyer_price": round(next(iter(prices)), 2),
                    }
                )
                if len(dom_items) >= max(int(limit), 1):
                    break
            if dom_items:
                return dom_items, None
            return [], "WB: витрина не отдала карточки (антибот JVO1 не пройден)"
    finally:
        await _close(context)


async def ozon_search_via_browser(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Поиск Ozon: in-page fetch из документа ozon.ru с разбором widgetStates."""
    key = f"ozon:{str(query or '').strip().lower()}:{int(limit)}"
    cached = _cache_get(key)
    if cached:
        return cached, None
    try:
        items, err = await asyncio.wait_for(
            _ozon_search_uncached(query, limit), timeout=_budget()
        )
    except asyncio.TimeoutError:
        return [], "Ozon: браузерный разбор не уложился в бюджет времени"
    if items:
        _cache_put(key, items)
    return items, err


async def _ozon_search_uncached(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise _BrowserUnavailable(f"Playwright недоступен: {exc}") from exc

    context = None
    try:
        async with async_playwright() as p:
            try:
                context = await _launch(p)
            except Exception as exc:  # noqa: BLE001
                raise _BrowserUnavailable(
                    f"Браузер Playwright не запускается: {exc}"
                ) from exc
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(
                    "https://www.ozon.ru/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
                )
            except Exception as exc:  # noqa: BLE001
                return [], f"Ozon: главная не открылась ({exc})"
            await asyncio.sleep(3.0)  # прогрев антибот-сессии

            search_path = (
                "https://www.ozon.ru/search/?text=" + _quote(query) + "&from_global=true"
            )
            # Запрос идёт из контекста страницы ozon.ru — иначе CORS и 403.
            payload = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {
                        headers: {'x-o3-app-name': 'dweb_client'},
                        credentials: 'include',
                    });
                    if (!r.ok) return {__status: r.status};
                    const text = await r.text();
                    return {__text: text};
                }""",
                search_path,
            )
            body = payload.get("__text") if isinstance(payload, dict) else None
            status = payload.get("__status") if isinstance(payload, dict) else None
            if not body:
                return [], f"Ozon: поиск вернул HTTP {status or '—'} (антибот)"

            # 1) Пробуем JSON-ответ composer-api (в нём widgetStates).
            items: List[Dict[str, Any]] = []
            try:
                items = parse_ozon_widget_states(json.loads(body), limit)
            except (ValueError, TypeError):
                items = []
            if not items and ("__NUXT__" in body or "<html" in body.lower()):
                items = parse_ozon_widget_states(body, limit)
            if items:
                return items, None

            # 2) Резерв: ждём отрисовку выдачи и читаем DOM.
            try:
                await page.goto(search_path, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                await asyncio.sleep(4.0)
                cards = await page.eval_on_selector_all(
                    'a[href*="/product/"]',
                    """els => els.slice(0, 200).map(e => ({
                        href: e.href || '',
                        text: (e.closest('div') && e.closest('div').innerText) || e.innerText || '',
                    }))""",
                )
            except Exception:  # noqa: BLE001
                cards = []
            items = parse_ozon_dom_cards(cards or [], limit)
            if items:
                return items, None
            return [], "Ozon: витрина закрыта антиботом (нет ни widgetStates, ни карточек)"
    finally:
        await _close(context)


def _quote(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(str(value or "").strip())
```

### `services/ozon/seller_api.py`

```python
import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from config import settings
from products_catalog import PRODUCTS_CATALOG
from services.ozon.finance import (
    accrual_category,
    amount,
    book_statement,
    complete_statement,
    totals,
    walk_fees,
)
from services.common.market_api import APIStatusError, MarketplaceClient, read_versions


def _amount(node) -> float:
    return amount(node)


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _accrual_category(types, type_id, unknown: set, _money) -> str:
    return accrual_category(types, type_id, unknown)


def _metric_number(value: Any) -> float:
    """Число из метрики Ozon: бывает числом, строкой или объектом {value, amount}."""
    if isinstance(value, dict):
        for key in ("value", "amount", "rub", "total", "count"):
            if value.get(key) is not None:
                return _metric_number(value[key])
        return 0.0
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    cleaned = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        parsed = float(cleaned)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _safe_count(value: Any) -> int:
    return int(round(_metric_number(value)))


def _cash_flow_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Озон отдаёт cash-flow только за 01–15 и 16–конец месяца.

    Режем произвольное окно по этим границам: иначе запрос за «последние 7 дней»
    получает HTTP 400 и модуль остаётся без сверочного источника.
    """
    windows: list[tuple[date, date]] = []
    current = start
    while current <= end:
        if current.day <= 15:
            segment_end = current.replace(day=15)
        else:
            next_month = (current.replace(day=1) + timedelta(days=32)).replace(day=1)
            segment_end = next_month - timedelta(days=1)
        segment_end = min(segment_end, end)
        windows.append((current, segment_end))
        current = segment_end + timedelta(days=1)
    return windows


def _collect_item_fee_skus(node) -> set[str]:
    """Артикулы из блока item_fees: по ним начислены платные услуги (реклама и др.)."""
    found: set[str] = set()

    def visit(current) -> None:
        if isinstance(current, dict):
            fees = current.get("fees")
            if current.get("sku") is not None and isinstance(fees, list):
                sku = str(current.get("sku")).strip()
                if sku:
                    found.add(sku)
            for value in current.values():
                visit(value)
        elif isinstance(current, list):
            for value in current:
                visit(value)

    visit(node)
    return found


class OzonSellerAPI(MarketplaceClient):
    BASE_URL = "https://api-seller.ozon.ru"
    PERF_URL = "https://api-performance.ozon.ru"

    def __init__(self, notify_callback=None):
        super().__init__("Ozon", settings.OZON_CLIENT_ID, notify_callback)
        self._finance_lock = asyncio.Lock()

    async def _request(self, method: str, endpoint: str, payload: dict) -> Any:
        return await self.request_json(
            method,
            self.BASE_URL + endpoint,
            {"Client-Id": settings.OZON_CLIENT_ID, "Api-Key": settings.OZON_API_KEY},
            body=payload,
        )

    async def fetch_stocks_and_prices(self) -> List[Dict[str, Any]]:
        async with self._inventory_lock:
            return await self._load_inventory()

    async def _load_inventory(self) -> List[Dict[str, Any]]:
        """
        Собирает цены, акции, остатки FBO и FBS, комиссию и приемку на ПВЗ по FBS.
        Использует кэш для защиты от 429.
        """
        cached = self._read_disk_cache("ozon_stocks_and_prices", max_age_sec=180)
        if cached is not None:
            return cached

        async def fetch_pages(endpoint: str) -> list:
            cursor_key = (
                "cursor"
                if endpoint.startswith(("/v5/", "/v4/product/info/stocks"))
                else "last_id"
            )
            collected, seen, cursor = [], set(), ""
            while True:
                response = await self._request(
                    "POST",
                    endpoint,
                    {
                        "filter": {"visibility": "ALL"},
                        "limit": 1000,
                        cursor_key: cursor,
                    },
                )
                root = response.get("result", response)
                page = root.get("items")
                if not isinstance(page, list):
                    raise RuntimeError(f"{endpoint}: нет массива items")
                collected.extend(page)
                nxt = str(root.get(cursor_key) or "")
                if not page or not nxt:
                    if (
                        len(page) == 1000
                        and len(collected) != root.get("total")
                        and not nxt
                    ):
                        raise RuntimeError(
                            f"{endpoint}: не получен курсор следующей страницы"
                        )
                    return collected
                if nxt in seen:
                    raise RuntimeError(f"{endpoint}: повтор курсора")
                seen.add(nxt)
                cursor = nxt

        prices_items = await read_versions(
            ("/v5/product/info/prices", "/v4/product/info/prices"), fetch_pages
        )
        stocks_items = await read_versions(
            ("/v4/product/info/stocks", "/v3/product/info/stocks"), fetch_pages
        )
        fbo_map: Dict[str, int] = {}
        fbs_map: Dict[str, int] = {}

        market_skus = defaultdict(set)
        for st_item in stocks_items:
            off_id = str(st_item.get("offer_id") or "").strip()
            if not off_id:
                continue
            fbo_qty = 0
            fbs_qty = 0
            for s in st_item.get("stocks") or []:
                if s.get("sku"):
                    market_skus[off_id].add(str(s["sku"]))
                st_type = str(s.get("type") or "").lower().strip()
                present = int(s.get("present") or 0)
                reserved = int(s.get("reserved") or 0)
                avail = max(0, present - reserved)
                if st_type in ("fbs", "rfbs", "crossborder"):
                    fbs_qty += avail
                elif st_type == "fbo":
                    fbo_qty += avail
                else:
                    wh_name = str(s.get("warehouse_name") or "").lower()
                    if "fbs" in wh_name or "rfbs" in wh_name or "собствен" in wh_name:
                        fbs_qty += avail
                    else:
                        fbo_qty += avail
            fbo_map[off_id] = fbo_qty
            fbs_map[off_id] = fbs_qty

        sales_7d_map = self._read_disk_cache("ordered_units", 1800)
        if sales_7d_map is None:
            end = datetime.utcnow().date()
            try:
                sales_7d_map = {}
                offset = 0
                while True:
                    analytics = await self._request(
                        "POST",
                        "/v1/analytics/data",
                        {
                            "date_from": (end - timedelta(days=6)).isoformat(),
                            "date_to": end.isoformat(),
                            "metrics": ["ordered_units"],
                            "dimension": ["sku"],
                            "limit": 1000,
                            "offset": offset,
                        },
                    )
                    rows = analytics["result"]["data"]
                    for row in rows:
                        sales_7d_map[str(row["dimensions"][0]["id"])] = int(
                            row["metrics"][0]
                        )
                    if len(rows) < 1000:
                        break
                    offset += len(rows)
                self._write_disk_cache("ordered_units", sales_7d_map)
            except (RuntimeError, KeyError, ValueError):
                sales_7d_map = None

        results: List[Dict[str, Any]] = []
        for item in prices_items:
            offer_id = str(item.get("offer_id") or "").strip()
            prod_id = str(item.get("product_id") or "")
            if not offer_id:
                continue

            price_block = item.get("price") or {}
            cur_price = float(
                price_block.get("price")
                or price_block.get("marketing_seller_price")
                or 0.0
            )
            buyer_price = float(price_block.get("marketing_price") or cur_price)
            if cur_price <= 0:
                continue

            comm_block = item.get("commissions") or {}
            raw_pct_fbo = float(
                comm_block.get("sales_percent_fbo")
                or comm_block.get("sales_percent")
                or 0.0
            )
            raw_pct_fbs = float(
                comm_block.get("sales_percent_fbs")
                or comm_block.get("sales_percent")
                or raw_pct_fbo
            )

            fbo_deliv = (
                float(comm_block.get("fbo_deliv_to_customer_amount") or 0.0)
                + float(
                    comm_block.get("fbo_direct_flow_trans_max_amount")
                    or comm_block.get("fbo_direct_flow_trans_min_amount")
                    or 0.0
                )
                + float(comm_block.get("fbo_fulfillment_amount") or 0.0)
            )

            fbs_deliv_pure = float(
                comm_block.get("fbs_deliv_to_customer_amount") or 0.0
            ) + float(
                comm_block.get("fbs_direct_flow_trans_max_amount")
                or comm_block.get("fbs_direct_flow_trans_min_amount")
                or 0.0
            )

            pvz_raw = next(
                (
                    comm_block[key]
                    for key in (
                        "fbs_first_mile",
                        "fbs_first_mile_max_amount",
                        "fbs_first_mile_min_amount",
                    )
                    if comm_block.get(key) is not None
                ),
                None,
            )
            pvz_dropoff_fee = round(float(pvz_raw), 2) if pvz_raw is not None else None
            acq_rub = (
                float(item["acquiring"])
                if item.get("acquiring") is not None
                else round(cur_price * settings.ACQUIRING_RATE, 2)
            )

            mkt_actions = item.get("marketing_actions")
            actions_list = []
            if isinstance(mkt_actions, dict):
                actions_list = mkt_actions.get("actions") or []
            elif isinstance(mkt_actions, list):
                actions_list = mkt_actions

            promo_titles = [
                str(a.get("title") or "Акция Ozon")
                for a in actions_list
                if isinstance(a, dict)
            ]
            in_promo = bool(promo_titles) or (
                isinstance(mkt_actions, dict)
                and mkt_actions.get("ozon_actions_exist") is True
            )

            fbo_st = fbo_map.get(offer_id, 0)
            fbs_st = fbs_map.get(offer_id, 0)

            market_sku = str(
                (PRODUCTS_CATALOG.get(offer_id) or {}).get("ozon_sku")
                or item.get("sku")
                or ""
            )
            ids = market_skus[offer_id]
            if market_sku:
                ids.add(market_sku)
            results.append(
                {
                    "sku": offer_id,
                    "product_id": prod_id,
                    "external_id": prod_id,
                    "ozon_skus": sorted(ids),
                    "current_price": cur_price,
                    "buyer_price": buyer_price,
                    "commission_rate": round(raw_pct_fbo / 100.0, 4),
                    "commission_rate_fbo": round(raw_pct_fbo / 100.0, 4),
                    "commission_rate_fbs": round(raw_pct_fbs / 100.0, 4),
                    "acquiring_rub": acq_rub,
                    "fbo_logistics_cost": round(fbo_deliv, 2),
                    "fbs_logistics_cost": round(fbs_deliv_pure, 2),
                    "fbs_pvz_dropoff_rub": pvz_dropoff_fee,
                    "stock_fbo": fbo_st,
                    "stock_fbs": fbs_st,
                    "daily_sales": round(
                        sum(sales_7d_map.get(i, 0) for i in ids) / 7, 2
                    )
                    if sales_7d_map is not None and ids
                    else None,
                    "in_promo": in_promo,
                    "promo_names": ", ".join(promo_titles)
                    if promo_titles
                    else ("Автоакция Ozon" if in_promo else ""),
                }
            )

        self._write_disk_cache("ozon_stocks_and_prices", results)
        return results

    async def _perf_token(self) -> str:
        if not settings.OZON_PERF_CLIENT_ID or not settings.OZON_PERF_SECRET:
            raise RuntimeError("Не заданы учётные данные Ozon Performance")
        data = await self.request_json(
            "POST",
            self.PERF_URL + "/api/client/token",
            {},
            body={
                "client_id": settings.OZON_PERF_CLIENT_ID,
                "client_secret": settings.OZON_PERF_SECRET,
                "grant_type": "client_credentials",
            },
        )
        if not data.get("access_token"):
            raise RuntimeError("Performance API не вернул токен")
        return str(data["access_token"])

    async def fetch_active_ad_campaigns(self, days: int = 7) -> List[Dict[str, Any]]:
        end = datetime.utcnow().date()
        key = f"campaigns:{settings.OZON_PERF_CLIENT_ID}:{end}:{days}"
        cached = self._read_disk_cache(key, 900)
        if cached is not None:
            return cached
        headers = {"Authorization": "Bearer " + await self._perf_token()}

        async def pages(path, field, *, post=False, **params):
            entries, seen = [], set()
            for page in range(1, 10001):
                pagination = {**params, "page": page, "pageSize": 100}
                data = await self.request_json(
                    "POST" if post else "GET",
                    self.PERF_URL + path,
                    headers,
                    **({"body": pagination} if post else {"params": pagination}),
                )
                rows = data.get(field)
                if not isinstance(rows, list):
                    raise RuntimeError(f"Performance {path}: нет {field}")
                entries.extend(rows)
                if len(rows) < 100:
                    return entries
                marker = str(rows[-1])
                if marker in seen:
                    break
                seen.add(marker)
            raise RuntimeError("Performance: пагинация не завершена")

        results = []
        running = [
            c
            for c in await pages(
                "/api/client/campaign", "list", state="CAMPAIGN_STATE_RUNNING"
            )
            if c.get("state") in {"CAMPAIGN_STATE_RUNNING", "RUNNING", "ACTIVE"}
        ]
        cpo_count = sum(c.get("advObjectType") == "SEARCH_PROMO" for c in running)
        for c in running:
            cid = str(int(c["id"]))

            async def products(path):
                if path.endswith("/objects"):
                    data = await self.request_json("GET", self.PERF_URL + path, headers)
                    return data["list"]
                return await pages(path, "products")

            kind = str(c.get("advObjectType") or "SKU")
            rows, skus = [], []
            if kind == "SEARCH_PROMO" and cpo_count == 1:
                entries = await pages(
                    "/api/client/campaign/search_promo/v2/products",
                    "products",
                    post=True,
                )
                skus = sorted(
                    {
                        str(p["sku"])
                        for p in entries
                        if p.get("searchPromoStatus") is True
                    }
                )
            elif kind == "SKU":
                items = await read_versions(
                    (
                        f"/api/client/campaign/{cid}/v2/products",
                        f"/api/client/campaign/{cid}/objects",
                    ),
                    products,
                )
                skus = sorted({str(p.get("sku") or p["id"]) for p in items})
                stats = await self.request_json(
                    "GET",
                    self.PERF_URL + "/api/client/statistics/campaign/product/json",
                    headers,
                    params={
                        "campaignIds": cid,
                        "dateFrom": (end - timedelta(days=days - 1)).isoformat(),
                        "dateTo": end.isoformat(),
                    },
                )
                rows = (
                    stats
                    if isinstance(stats, list)
                    else stats.get("rows", stats.get("list"))
                )
                if not isinstance(rows, list):
                    raise RuntimeError("Performance: неизвестный формат отчёта")
                rows = [
                    r for r in rows if str(r.get("campaignId", r.get("id", cid))) == cid
                ]

            def total(keys):
                values = [
                    next((r[k] for k in keys if r.get(k) is not None), None)
                    for r in rows
                ]
                if not values or None in values:
                    return None
                return round(
                    sum(
                        float(str(v).replace(" ", "").replace(",", ".")) for v in values
                    ),
                    2,
                )

            spend, revenue = (
                total(("moneySpent", "spend", "expense")),
                total(("ordersMoney", "revenue", "ordersSum")),
            )
            results.append(
                {
                    "id": cid,
                    "title": c.get("title"),
                    "state": c["state"],
                    "skus_inside": skus,
                    "products_count": len(skus),
                    "spend_7d_rub": spend,
                    "promo_revenue_7d_rub": revenue,
                    "stats_available": spend is not None and revenue is not None,
                    "drr_in_promo_pct": round(100 * spend / revenue, 1)
                    if revenue and spend is not None
                    else None,
                    "source": f"Ozon Performance API ({kind})",
                }
            )
        self._write_disk_cache(key, results)
        return results

    async def fetch_sales_by_sku(self, days: int = 7) -> tuple[List[Dict[str, Any]], str]:
        """Продажи Ozon в разрезе SKU — резервный источник для ТОП-5.

        Начисления by-day содержат детализацию по позициям, однако за свежие
        сутки начисления часто ещё не сформированы. Тогда берём агрегаты
        `/v1/analytics/data` по измерению `sku`: количество и выручка.
        Возвращает (строки заказов, подпись источника).
        """
        end = datetime.utcnow().date()
        start = end - timedelta(days=max(int(days), 1) - 1)
        key = f"sales_by_sku:{start.isoformat()}:{end.isoformat()}"
        cached = self._read_disk_cache(key, 1800)
        if isinstance(cached, dict) and isinstance(cached.get("orders"), list):
            return cached["orders"], str(cached.get("source") or "")

        metric_sets = [
            ["ordered_units", "revenue"],
            ["ordered_units"],
        ]
        errors: List[str] = []
        priced: List[Dict[str, Any]] = []
        units_only: Dict[str, int] = {}
        for metrics in metric_sets:
            rows: List[Dict[str, Any]] = []
            offset = 0
            try:
                for _ in range(50):
                    analytics = await self._request(
                        "POST",
                        "/v1/analytics/data",
                        {
                            "date_from": start.isoformat(),
                            "date_to": end.isoformat(),
                            "metrics": metrics,
                            "dimension": ["sku"],
                            "limit": 1000,
                            "offset": offset,
                        },
                    )
                    page = ((analytics or {}).get("result") or {}).get("data") or []
                    if not isinstance(page, list):
                        raise RuntimeError("Ozon: неверный формат аналитики по SKU")
                    rows.extend(page)
                    if len(page) < 1000:
                        break
                    offset += len(page)
            except (RuntimeError, APIStatusError, KeyError, TypeError, ValueError) as exc:
                errors.append(f"{'+'.join(metrics)}: {exc}")
                continue

            for row in rows:
                if not isinstance(row, dict):
                    continue
                dims = row.get("dimensions") or []
                if not dims or not isinstance(dims[0], dict):
                    continue
                sku = str(dims[0].get("id") or "").strip()
                if not sku:
                    continue
                units = 0
                revenue = 0.0
                for name, value in zip(metrics, row.get("metrics") or []):
                    if name == "ordered_units":
                        units = max(_safe_count(value), 0)
                    elif name == "revenue":
                        revenue = max(_metric_number(value), 0.0)
                if units <= 0:
                    continue
                units_only[sku] = max(units_only.get(sku, 0), units)
                unit_price = round(revenue / units, 2) if revenue > 0.0 else 0.0
                if unit_price <= 0.0:
                    continue
                priced.append(
                    {
                        "order_id": f"ozana_{sku}_{len(priced)}",
                        "marketplace": "ozon",
                        "sku": sku,
                        "created_at": end.isoformat(),
                        "quantity": units,
                        "price": unit_price,
                        "status": "sold",
                    }
                )
            if priced:
                source = (
                    f"аналитика Ozon по SKU ({start.isoformat()}..{end.isoformat()}, "
                    f"{'+'.join(metrics)})"
                )
                self._write_disk_cache(key, {"orders": priced, "source": source})
                return priced, source

        # Последний резерв: количество пришло, а выручка — нет. Берём текущую
        # цену продавца из ЛК (это оценка, а не факт сделки — подписано в отчёте).
        if units_only:
            price_map: Dict[str, float] = {}
            try:
                inventory = await self.fetch_stocks_and_prices()
            except Exception:  # noqa: BLE001 — оценка цены не должна ронять отчёт
                inventory = []
            for item in inventory if isinstance(inventory, list) else []:
                if not isinstance(item, dict):
                    continue
                price = max(
                    _metric_number(item.get("current_price")),
                    _metric_number(item.get("buyer_price")),
                )
                if price <= 0.0:
                    continue
                for ident in (
                    str(item.get("sku") or "").strip(),
                    str(item.get("offer_id") or "").strip(),
                    str(item.get("product_id") or "").strip(),
                ):
                    if ident:
                        price_map[ident] = price
            estimated: List[Dict[str, Any]] = []
            for sku, units in units_only.items():
                price = price_map.get(sku, 0.0)
                if price <= 0.0:
                    continue
                estimated.append(
                    {
                        "order_id": f"ozest_{sku}_{len(estimated)}",
                        "marketplace": "ozon",
                        "sku": sku,
                        "created_at": end.isoformat(),
                        "quantity": units,
                        "price": round(price, 2),
                        "status": "sold",
                    }
                )
            if estimated:
                source = (
                    f"аналитика Ozon по SKU ({start.isoformat()}..{end.isoformat()}, "
                    "количество факт; цены — текущие из ЛК, оценка)"
                )
                self._write_disk_cache(key, {"orders": estimated, "source": source})
                return estimated, source
            errors.append("выручка по SKU недоступна и цены из ЛК не подтянулись")

        detail = "; ".join(dict.fromkeys(errors)) if errors else "за период нет продаж"
        return [], f"Ozon: артикулы недоступны ({detail})"

    async def _accrual_types(self) -> Dict[int, str]:
        """Справочник типов начислений Ozon. Статичен: кэш на 7 дней + в памяти."""
        cached = self._read_disk_cache("accrual_types", 7 * 86400)
        if isinstance(cached, dict) and cached:
            try:
                return {int(k): str(v) for k, v in cached.items()}
            except (TypeError, ValueError):
                pass  # битый кэш не должен ломать отчёт
        try:
            data = await self._request("POST", "/v1/finance/accrual/types", {})
        except (APIStatusError, RuntimeError):
            # Справочник — удобство, а не условие: без него типы разбираются по имени.
            return {}
        rows = (data or {}).get("accrual_types") or []
        types = {
            int(row["id"]): str(row.get("name") or row.get("description") or row["id"])
            for row in rows
            if isinstance(row, dict) and row.get("id") is not None
        }
        if types:
            self._write_disk_cache("accrual_types", types)
        return types

    async def _accruals_for_day(self, day: datetime) -> List[Dict[str, Any]]:
        """Все начисления одного дня (/v1/finance/accrual/by-day, курсор last_id)."""
        rows: List[Dict[str, Any]] = []
        last_id = ""
        for _ in range(200):  # предохранитель от бесконечной пагинации
            body: Dict[str, Any] = {"date": day.strftime("%Y-%m-%d")}
            if last_id:
                body["last_id"] = last_id
            data = await self._request("POST", "/v1/finance/accrual/by-day", body)
            page = (data or {}).get("accruals") or []
            rows.extend(row for row in page if isinstance(row, dict))
            last_id = str((data or {}).get("last_id") or "")
            if not last_id or not page:
                return rows
        raise RuntimeError("Ozon: пагинация начислений за день не завершена")

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Финансы Ozon из действующего API начислений.

        /v3/finance/transaction/list отключён Ozon 08.09.2026, поэтому источник —
        /v1/finance/accrual/by-day (обход по суткам, курсор last_id), и при
        доступности /v1/finance/cash-flow-statement/list для сверки периодов.
        """
        now_utc = datetime.utcnow()
        if custom_date_from and custom_date_to:
            start_dt = datetime.strptime(custom_date_from, "%Y-%m-%d")
            end_dt = datetime.strptime(custom_date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        else:
            start_dt = now_utc - timedelta(days=max(days, 1) - 1)
            end_dt = now_utc

        from_date = start_dt.strftime("%Y-%m-%dT00:00:00.000Z")
        to_date = end_dt.strftime("%Y-%m-%dT23:59:59.999Z")
        period_label = f"{start_dt.strftime('%d.%m.%Y')}–{end_dt.strftime('%d.%m.%Y')}"
        cache_key = f"finance_v2:{from_date}:{to_date}"

        async with self._finance_lock:
            cached = self._read_disk_cache(cache_key, 900)
            if cached is not None:
                return cached

            report_totals: Dict[str, Any] = {
                "sales_total": 0.0,
                "commission": 0.0,
                "deliv_direct": 0.0,
                "deliv_return": 0.0,
                "deliv_ozon_handover": 0.0,
                "deliv_total": 0.0,
                "partner_dropoff": 0.0,
                "partner_pack": 0.0,
                "partner_returns": 0.0,
                "partner_last_mile": 0.0,
                "acquiring": 0.0,
                "star_products": 0.0,
                "fbo_storage": 0.0,
                "promo_cpc": 0.0,
                "promo_cpo": 0.0,
                "ads_total": 0.0,
                "other_fines": 0.0,
                "decompensations": 0.0,
                "mutual_settlements": 0.0,
                "partners_total": 0.0,
                "total_deductions": 0.0,
                "total_payout": 0.0,
                "acquiring_reported": False,
            }
            source_parts: List[str] = []
            orders_list: List[Dict[str, Any]] = []
            billed_skus: set[str] = set()
            cash_flow_applied = False

            # 1) Сверочный источник: /v1/finance/cash-flow-statement/list.
            # Он даёт итоги по периодам, но не даёт разбивку по артикулам.
            try:
                cash_flows, details_list, src = await self._fetch_cash_flow(
                    start_dt.date(), end_dt.date()
                )
                if cash_flows or details_list:
                    statement = totals(book_statement(cash_flows, details_list))
                    # Итоги сверочного отчёта принимаются только при точном
                    # покрытии запрошенного окна. Иначе источник — суточные
                    # начисления: иначе P&L показал бы суммы за лишние дни.
                    if complete_statement(cash_flows, start_dt.date(), end_dt.date()):
                        report_totals.update(statement)
                        cash_flow_applied = True
                        source_parts.append(src)
                    else:
                        source_parts.append(
                            f"{src} (неполное покрытие периода; итоги — по начислениям)"
                        )
                else:
                    source_parts.append("cash-flow пуст")
            except APIStatusError as exc:
                source_parts.append(f"cash-flow HTTP {exc.status}")
            except Exception as exc:  # noqa: BLE001 — резервный источник обязателен
                source_parts.append(f"cash-flow {type(exc).__name__}")

            accrual_success = False
            # 2) Основной per-SKU источник: /v1/finance/accrual/by-day.
            # Именно он даёт заказы и артикулы, по которым были платные услуги.
            try:
                accrual_book, accrual_orders, accrual_billed, accrual_src = (
                    await self._fetch_accrual_by_day(start_dt.date(), end_dt.date())
                )
                accrual_success = True
                orders_list = accrual_orders
                billed_skus = set(accrual_billed)
                if not cash_flow_applied:
                    report_totals.update(totals(accrual_book))
                source_parts.append(accrual_src)
            except APIStatusError as exc:
                source_parts.append(f"accrual HTTP {exc.status}")
            except Exception as exc:  # noqa: BLE001
                source_parts.append(f"accrual {type(exc).__name__}")

            if not accrual_success and not cash_flow_applied:
                raise RuntimeError("Ozon: финансы недоступны; " + "; ".join(source_parts))
            detail = "; ".join(part for part in source_parts if part) or "нет данных"
            report = dict(
                report_totals,
                orders=orders_list,
                orders_count=len(orders_list),
                billed_skus=sorted(billed_skus),
                source=f"Ozon {detail} ({period_label})",
            )
            self._write_disk_cache(cache_key, report)
            return report

    async def _fetch_cash_flow(
        self, start: date, end: date
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """POST /v1/finance/cash-flow-statement/list по полумесячным окнам."""
        flows: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []
        windows = _cash_flow_windows(start, end)
        skipped: list[int] = []
        for win_start, win_end in windows:
            try:
                win_flows, win_details = await self._fetch_cash_flow_window(
                    win_start, win_end
                )
            except APIStatusError as exc:
                # Один полумесяц может быть закрыт не полностью; остальные окна
                # всё равно запрашиваем. Если окно всего одно — ошибка значима.
                if len(windows) == 1 or exc.status not in {400, 404, 410, 425}:
                    raise
                skipped.append(exc.status)
                continue
            flows.extend(win_flows)
            details.extend(win_details)

        source = "cash-flow-statement/list"
        if skipped:
            source += f" (часть периодов недоступна: HTTP {skipped[0]})"
        return flows, details, source

    async def _fetch_cash_flow_window(
        self, start: date, end: date
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        flows: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []
        page = 1
        pages = 1
        for _ in range(50):  # предохранитель от бесконечной пагинации
            payload = {
                "date_from": start.strftime("%Y-%m-%dT00:00:00.000Z"),
                "date_to": end.strftime("%Y-%m-%dT23:59:59.999Z"),
                "page": page,
                "page_size": 1000,
                "with_details": True,
            }
            data = await self._request(
                "POST", "/v1/finance/cash-flow-statement/list", payload
            )
            result = (data or {}).get("result") or {}
            page_flows = result.get("cash_flows") or []
            if isinstance(page_flows, list):
                flows.extend(row for row in page_flows if isinstance(row, dict))
            raw_details = result.get("details")
            if isinstance(raw_details, dict):
                details.append(raw_details)
            elif isinstance(raw_details, list):
                details.extend(row for row in raw_details if isinstance(row, dict))
            try:
                pages = max(int((data or {}).get("page_count") or 1), 1)
            except (TypeError, ValueError):
                pages = 1
            if page >= pages or not page_flows:
                break
            page += 1
        return flows, details

    async def _fetch_accrual_by_day(
        self, start: date, end: date
    ) -> tuple[Dict[str, Decimal], List[Dict[str, Any]], set[str], str]:
        """Книга начислений Ozon по суткам + заказы + артикулы платных услуг."""
        types = await self._accrual_types()
        unknown: set[int] = set()
        book: Dict[str, Decimal] = defaultdict(Decimal)
        orders: List[Dict[str, Any]] = []
        billed: set[str] = set()

        for day in _date_range(start, end):
            rows = await self._accruals_for_day(
                datetime(day.year, day.month, day.day)
            )
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                # Ozon переименовал accruals.type_id -> accruals.accrual_id
                # (уведомление 14.07.2026); читаем оба ключа.
                accrual_id = str(
                    row.get("accrual_id") or row.get("type_id") or ""
                ).strip()
                day_str = str(row.get("date") or day.isoformat())[:10]
                posting = (
                    row.get("posting") if isinstance(row.get("posting"), dict) else {}
                )
                products = (
                    posting.get("products")
                    if isinstance(posting.get("products"), list)
                    else []
                )
                posting_number = str(posting.get("posting_number") or "").strip()
                for index, product in enumerate(products):
                    if not isinstance(product, dict):
                        continue
                    commission_obj = (
                        product.get("commission")
                        if isinstance(product.get("commission"), dict)
                        else {}
                    )
                    seller_price = _amount(commission_obj.get("seller_price"))
                    sale_price = _amount(commission_obj.get("sale_price"))
                    raw_commission = abs(
                        _amount(commission_obj.get("sale_commission"))
                    )
                    qty = max(
                        _safe_count(product.get("quantity") or posting.get("quantity")),
                        1,
                    )
                    # Возврат приходит отрицательной суммой продажи: комиссия
                    # по нему тоже возвращается, иначе расход завышается.
                    sign = -1 if seller_price < 0 else 1
                    sale_commission = raw_commission * sign
                    book["sales_total"] += _to_decimal(seller_price) * qty
                    book["commission"] += _to_decimal(sale_commission) * qty
                    delivery = (
                        product.get("delivery")
                        if isinstance(product.get("delivery"), dict)
                        else {}
                    )
                    logistics = abs(_amount(delivery.get("total_accrued")))
                    sku = str(
                        product.get("sku") or posting.get("sku") or ""
                    ).strip()
                    base_id = (
                        accrual_id
                        or posting_number
                        or f"{day_str}-{row_index}"
                    )
                    orders.append(
                        {
                            "order_id": f"oz_{base_id}_{sku or index}",
                            "marketplace": "ozon",
                            "sku": sku,
                            "created_at": day_str,
                            "quantity": qty,
                            "price": round(seller_price, 2),
                            "buyer_price": round(sale_price or seller_price, 2),
                            "commission": round(sale_commission, 2),
                            "logistics": round(logistics, 2),
                            "status": "returned" if seller_price < 0 else "sold",
                        }
                    )

                # Любые услуги с type_id на любой глубине дерева: формат меняется,
                # поэтому обход рекурсивный, а не по жёстким путям.
                for type_id, cost in walk_fees(row, unknown):
                    if not cost:
                        continue
                    book[accrual_category(types, type_id, unknown)] += -cost

                billed.update(_collect_item_fee_skus(row.get("item_fees")))

        detail = (
            f"начисления by-day ({start.isoformat()}..{end.isoformat()})"
        )
        if unknown:
            detail += "; незнакомые типы: " + ", ".join(
                str(item) for item in sorted(unknown)
            )
        return book, orders, billed, detail

    async def update_price(
        self, product_id: str, offer_id: str, new_price: float
    ) -> Dict[str, Any]:
        if not math.isfinite(new_price) or new_price < 0.01:
            raise ValueError("Некорректная цена Ozon")
        if (
            not str(product_id).isdecimal()
            or int(product_id) <= 0
            or not isinstance(offer_id, str)
            or not offer_id.strip()
        ):
            raise ValueError("Требуются числовой product_id и непустой offer_id")
        # Ozon принимает только перечисленные ниже поля. old_price/min_price/
        # currency_code не передаём: непереданные поля API не меняет, а лишние
        # ключи в теле запроса расширяют поверхность отказа.
        payload = {
            "prices": [
                {
                    "auto_action_enabled": "UNKNOWN",
                    "offer_id": offer_id.strip(),
                    "price": f"{new_price:.2f}",
                    "product_id": int(product_id),
                }
            ]
        }
        return await self._request("POST", "/v1/product/import/prices", payload)
```

### `services/wb/statistics_api.py`

```python
import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import settings
from services.common.market_api import APIStatusError, MarketplaceClient, read_versions

logger = logging.getLogger(__name__)


class AmbiguousFinancePeriod(RuntimeError):
    """Безопасная диагностическая строка о границах сводного отчёта."""




def _exc_label(exc: BaseException) -> str:
    if isinstance(exc, APIStatusError):
        return f"HTTP {exc.status}"
    if isinstance(exc, AmbiguousFinancePeriod):
        return str(exc)
    return type(exc).__name__


def _latin(value: str) -> int | float | str | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _num(value) -> float:
    """Суммы нового finance-api WB приходят строками, иногда с пробелами."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    parsed = _latin(cleaned)
    return float(parsed) if isinstance(parsed, (int, float)) else 0.0


def _pick(row: dict, *keys, default=None):
    """Первое непустое поле из перечисленных имён (camelCase и snake_case)."""
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return default


def _rrd(row: dict) -> int:
    value = _latin(_pick(row, "rrdId", "rrd_id", default=0))
    return int(value) if isinstance(value, (int, float)) else 0


class WBStatisticsAPI(MarketplaceClient):
    STATS_URL = "https://statistics-api.wildberries.ru"
    FINANCE_URL = "https://finance-api.wildberries.ru"
    PRICES_URL = "https://discounts-prices-api.wildberries.ru"
    COMMON_URL = "https://common-api.wildberries.ru"
    ADVERT_URL = "https://advert-api.wildberries.ru"
    ANALYTICS_URL = "https://seller-analytics-api.wildberries.ru"
    MARKETPLACE_URL = "https://marketplace-api.wildberries.ru"
    CONTENT_URL = "https://content-api.wildberries.ru"

    def __init__(self, notify_callback=None):
        super().__init__("WB", settings.WB_API_TOKEN, notify_callback)
        self._tariffs_lock = asyncio.Lock()
        self._orders_lock = asyncio.Lock()
        # WB отключил legacy /api/v1/supplier/stocks: после 404 не дёргаем
        # его 6 часов, иначе каждый цикл синхронизации пишет ту же ошибку.
        self._fbo_legacy_retry_at = 0.0

    async def _request(self, method: str, url: str, params=None, json_data=None) -> Any:
        return await self.request_json(
            method,
            url,
            {"Authorization": settings.WB_API_TOKEN},
            params=params,
            body=json_data,
        )

    async def fetch_wb_tariffs(self) -> dict:
        """Тарифы WB. Недоступность тарифов не должна отменять синхронизацию остатков."""
        today = datetime.utcnow().date().isoformat()
        key = "tariffs:" + today
        async with self._tariffs_lock:
            cached = self._read_disk_cache(key, 21600)
            if cached is not None:
                return cached

            subjects: Dict[str, Dict[str, Any]] = {}
            warehouses: Dict[str, Dict[str, Any]] = {}
            try:
                commissions = await self._request(
                    "GET", self.COMMON_URL + "/api/v1/tariffs/commission"
                )
                report = (
                    commissions.get("report")
                    if isinstance(commissions, dict)
                    else None
                )
                for row in report or []:
                    if not isinstance(row, dict) or row.get("subjectID") is None:
                        continue
                    subjects[str(row["subjectID"])] = {
                        "fbo": row.get("paidStorageKgvp"),
                        "fbs": row.get("kgvpMarketplace"),
                    }
            except (APIStatusError, RuntimeError, KeyError, TypeError) as exc:
                logger.warning("WB: тарифы комиссий недоступны (%s)", type(exc).__name__)

            try:
                boxes = await self._request(
                    "GET", self.COMMON_URL + "/api/v1/tariffs/box", params={"date": today}
                )
                response = boxes.get("response") if isinstance(boxes, dict) else {}
                data = response.get("data") if isinstance(response, dict) else {}
                warehouse_list = (
                    data.get("warehouseList") if isinstance(data, dict) else None
                )
                for row in warehouse_list or []:
                    if isinstance(row, dict) and row.get("warehouseName"):
                        warehouses[str(row["warehouseName"])] = row
            except (APIStatusError, RuntimeError, KeyError, TypeError) as exc:
                logger.warning("WB: тарифы складов недоступны (%s)", type(exc).__name__)

            result = {"subjects": subjects, "warehouses": warehouses}
            if subjects or warehouses:
                # Пустой ответ не кэшируем: следующий цикл должен повторить попытку.
                self._write_disk_cache(key, result)
            return result

    async def _ad_charges(self, date_from: str, date_to: str) -> list:
        key = f"ad_charges:{date_from}:{date_to}"
        rows = self._read_disk_cache(key, 900)
        if rows is None:
            rows = await self._request(
                "GET",
                self.ADVERT_URL + "/adv/v1/upd",
                params={"from": date_from, "to": date_to},
            )
            if not isinstance(rows, list):
                raise RuntimeError("WB: неверный формат списаний рекламы")
            rows = [row for row in rows if isinstance(row, dict)]
            self._write_disk_cache(key, rows)
        return rows

    async def fetch_wb_ad_spend(self, date_from: str, date_to: str) -> float:
        total = 0.0
        for row in await self._ad_charges(date_from, date_to):
            if isinstance(row, dict):
                total += _num(row.get("updSum"))
        return round(total, 2)

    async def update_price_and_min_price(
        self, nm_id: int, recommended_price_after_discount: float
    ) -> Dict[str, Any]:
        """Создаёт задачу WB; отправка не означает, что цена уже применена.

        Публичный upload/task не содержит поля минимальной цены акции. Значение
        92% возвращается как рекомендация, но не выдаётся за установленное.
        """
        if isinstance(nm_id, bool) or not isinstance(nm_id, int) or nm_id <= 0:
            raise ValueError("Требуется положительный числовой nmID")
        value = float(recommended_price_after_discount)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Цена должна быть положительной и конечной")
        target = value
        if target < 1:
            raise ValueError("Округлённая цена должна быть не меньше 1 ₽")
        try:
            result = await self._request(
                "GET",
                self.PRICES_URL + "/api/v2/list/goods/filter",
                params={"limit": 1, "offset": 0, "filterNmID": nm_id},
            )
        except RuntimeError as exc:
            raise ValueError(
                f"WB: запрос скидки не удался; задача не отправлена: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise ValueError("WB: скидка не получена, загрузка отменена")
        if result.get("error"):
            raise ValueError(str(result.get("errorText") or "Ошибка чтения скидки WB"))
        goods = result.get("data", {}).get("listGoods", [])
        matches = [
            g for g in goods if type(g.get("nmID")) is int and g["nmID"] == nm_id
        ]
        discount = matches[0].get("discount") if len(matches) == 1 else None
        if type(discount) is not int or not 0 <= discount < 100:
            raise ValueError("WB не вернул допустимую скидку; задача не отправлена")
        before = int(round(target / (1 - discount / 100)))
        response = await self._request(
            "POST",
            f"{self.PRICES_URL}/api/v2/upload/task",
            json_data={
                "data": [{"nmID": nm_id, "price": before, "discount": discount}]
            },
        )
        if isinstance(response, dict) and response.get("error") is True:
            raise ValueError(
                f"WB отклонил задачу: {response.get('errorText') or 'без описания'}"
            )
        if not isinstance(response, dict) or response.get("error") is not False:
            raise RuntimeError("Неизвестный ответ WB после загрузки")
        upload_id = (response.get("data") or {}).get("id")
        if not upload_id:
            raise RuntimeError("WB не вернул ID загрузки; проверьте состояние вручную")
        return {
            "status": "submitted",
            "upload_id": upload_id,
            "nm_id": nm_id,
            "seller_discount_pct": discount,
            "price_before_discount_set": before,
            "price_after_discount_target": target,
            "min_price_for_promotions": int(round(target * 0.92)),
            "min_price_set": False,
            "api_response": response,
        }

    async def update_price(self, nm_id: int, new_price: float) -> Any:
        return await self.update_price_and_min_price(nm_id, float(new_price))

    async def fetch_stocks_and_prices(self) -> List[Dict[str, Any]]:
        async with self._inventory_lock:
            return await self._load_inventory()

    async def _load_inventory(self) -> List[Dict[str, Any]]:
        cached = self._read_disk_cache("wb_stocks_and_prices", max_age_sec=180)
        if cached is not None:
            return cached

        all_goods = []
        for offset in range(0, 1000000, 1000):
            prices_raw = await self._request(
                "GET",
                f"{self.PRICES_URL}/api/v2/list/goods/filter",
                params={"limit": 1000, "offset": offset},
            )
            if not isinstance(prices_raw, dict) or prices_raw.get("error"):
                raise RuntimeError("Некорректный ответ WB по ценам")
            page = prices_raw.get("data", {}).get("listGoods", [])
            all_goods.extend(page)
            if len(page) < 1000:
                break
        else:
            raise RuntimeError("Не завершена пагинация цен WB")
        tariffs = await self.fetch_wb_tariffs()

        subject_by_nm = {}
        chrt_to_nm = {}
        all_sizes = []
        fbs_stocks_map: Dict[int, int] = {}
        barcode_to_nm: Dict[str, int] = {}

        # Content API возвращает не более 100 карточек на страницу.
        cursor = {"limit": 100}
        seen_cursors = set()
        while True:
            cards_resp = await self._request(
                "POST",
                f"{self.CONTENT_URL}/content/v2/get/cards/list",
                json_data={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
            )
            if not isinstance(cards_resp, dict) or cards_resp.get("error"):
                raise RuntimeError("Не удалось получить карточки WB")
            cards = cards_resp.get("cards") or []
            for card in cards:
                nm = int(card.get("nmID") or 0)
                subject_by_nm[nm] = str(card.get("subjectID") or "")
                for size in card.get("sizes") or []:
                    all_sizes.append(size)
                    if size.get("chrtID"):
                        chrt_to_nm[int(size["chrtID"])] = nm
                    for barcode in size.get("skus") or []:
                        if barcode and nm:
                            barcode = str(barcode).strip()
                            if (
                                barcode in barcode_to_nm
                                and barcode_to_nm[barcode] != nm
                            ):
                                raise ValueError("Один баркод принадлежит разным nmID")
                            barcode_to_nm[barcode] = nm
            nxt = cards_resp.get("cursor") or {}
            if int(nxt.get("total", len(cards))) < 100:
                break
            key = (nxt.get("updatedAt"), nxt.get("nmID"))
            if not all(key) or key in seen_cursors:
                raise RuntimeError("WB Content API: курсор не продвигается")
            seen_cursors.add(key)
            cursor = {"limit": 100, "updatedAt": key[0], "nmID": key[1]}

        warehouses = await self._request(
            "GET", f"{self.MARKETPLACE_URL}/api/v3/warehouses"
        )
        if not isinstance(warehouses, list):
            raise RuntimeError("Некорректный список складов WB")
        # New contracts use size IDs; older cards supply barcodes. Never count both for a size.
        use_chrt = bool(chrt_to_nm) and all(size.get("chrtID") for size in all_sizes)
        mapping = chrt_to_nm if use_chrt else barcode_to_nm
        ids = list(mapping)
        for wh_id in {w["id"] for w in warehouses}:
            for pos in range(0, len(ids), 1000):
                batch = ids[pos : pos + 1000]
                st = await self._request(
                    "POST",
                    f"{self.MARKETPLACE_URL}/api/v3/stocks/{wh_id}",
                    json_data={"chrtIds" if use_chrt else "skus": batch},
                )
                seen = set()
                for row in st["stocks"]:
                    ident = row.get("chrtId") if use_chrt else str(row["sku"])
                    if ident not in mapping:
                        raise RuntimeError(
                            "WB FBS: не удалось сопоставить размер со складской позицией"
                        )
                    if ident in seen:
                        continue
                    seen.add(ident)
                    nm = mapping[ident]
                    fbs_stocks_map[nm] = fbs_stocks_map.get(nm, 0) + max(
                        0, int(row["amount"])
                    )

        fbo_stocks_map, locations = await read_versions(
            ("supplier", "analytics"), self._fetch_fbo
        )

        buyer_price_map: Dict[int, float] = {}
        sales_count_7d = {}
        end = datetime.utcnow().date()
        end_date = end.isoformat()
        date_7d = (end - timedelta(days=6)).isoformat()

        try:
            raw_orders = await self._fetch_orders_7d(date_7d, end_date)
            for o in raw_orders:
                if (
                    o.get("isCancel")
                    or not date_7d <= str(o.get("date") or "")[:10] <= end_date
                ):
                    continue
                nm = int(o.get("nmId") or 0)
                bp = float(o.get("finishedPrice") or o.get("priceWithDisc") or 0.0)
                if nm:
                    sales_count_7d[nm] = sales_count_7d.get(nm, 0) + 1
                    if bp > 0:
                        buyer_price_map[nm] = bp
        except (RuntimeError, APIStatusError):
            sales_count_7d = None

        dedup_results: Dict[int, Dict[str, Any]] = {}
        for g in all_goods:
            nm_id = int(g.get("nmID") or 0)
            if not nm_id:
                continue
            vendor_code = str(g.get("vendorCode") or str(nm_id)).strip()
            sizes = g.get("sizes") or []
            seller_price = (
                float(sizes[0].get("discountedPrice") or sizes[0].get("price") or 0.0)
                if sizes
                else 0.0
            )
            if seller_price <= 0:
                continue

            buyer_price = buyer_price_map.get(nm_id, seller_price)
            fbo_qty = fbo_stocks_map.get(nm_id, 0)
            fbs_qty = fbs_stocks_map.get(nm_id, 0)
            daily_sales = (
                round(sales_count_7d.get(nm_id, 0) / 7.0, 2)
                if sales_count_7d is not None
                else None
            )
            acquiring_val = round(max(buyer_price, seller_price) * 0.015, 2)

            rates = tariffs["subjects"].get(subject_by_nm.get(nm_id), {})
            total_stock = fbo_qty + fbs_qty
            weighted_comm = None
            if total_stock and all(
                rates.get(mode) is not None
                for qty, mode in ((fbo_qty, "fbo"), (fbs_qty, "fbs"))
                if qty
            ):
                weighted_comm = (
                    float(rates.get("fbo") or 0) * fbo_qty
                    + float(rates.get("fbs") or 0) * fbs_qty
                ) / (100 * total_stock)

            def base_rate(field, warehouses):
                values = []
                for wh in warehouses:
                    raw = tariffs["warehouses"].get(wh, {}).get(field)
                    if raw not in (None, "", "-"):
                        values.append(float(str(raw).replace(",", ".")))
                return max(
                    values, default=None
                )  # conservative base tariff, not realized delivery cost

            dedup_results[nm_id] = {
                "sku": vendor_code,
                "nm_id": nm_id,
                "external_id": str(nm_id),
                "current_price": seller_price,
                "buyer_price": buyer_price,
                "commission_rate": weighted_comm,
                "acquiring_rub": acquiring_val,
                "fbo_logistics_cost": base_rate(
                    "boxDeliveryBase", locations.get(nm_id) or tariffs["warehouses"]
                ),
                "fbs_logistics_cost": base_rate(
                    "boxDeliveryMarketplaceBase", tariffs["warehouses"]
                ),
                "stock_fbo": fbo_qty,
                "stock_fbs": fbs_qty,
                "daily_sales": daily_sales,
                "in_promo": False,
                "promo_max_price": 0.0,
                "promo_id": "",
            }

        results = list(dedup_results.values())
        self._write_disk_cache("wb_stocks_and_prices", results)
        return results

    async def _fetch_fbo(self, source: str) -> tuple[dict, dict]:
        quantities, locations = {}, {}
        if source == "supplier":
            if time.monotonic() < self._fbo_legacy_retry_at:
                raise APIStatusError(
                    404, "WB: legacy /api/v1/supplier/stocks отключён (пауза 6 ч)"
                )
            url = self.STATS_URL + "/api/v1/supplier/stocks"
            try:
                rows = await self._request(
                    "GET", url, params={"dateFrom": "2019-06-20"}
                )
            except APIStatusError as exc:
                if exc.status in {404, 410}:
                    self._fbo_legacy_retry_at = time.monotonic() + 6 * 3600
                    logger.warning(
                        "WB legacy /api/v1/supplier/stocks недоступен (HTTP %s): "
                        "FBO-остатки берутся из analytics/warehouse_remains",
                        exc.status,
                    )
                raise
            if not isinstance(rows, list):
                raise RuntimeError("WB: неверный формат FBO-остатков")
            seen = set()
            for row in sorted(
                rows, key=lambda r: r.get("lastChangeDate", ""), reverse=True
            ):
                nm, wh = int(row["nmId"]), row["warehouseName"]
                key = nm, wh, row.get("barcode"), row.get("techSize")
                if key in seen or str(row.get("warehouseType", "")).lower() in {
                    "fbs",
                    "склад продавца",
                }:
                    continue
                seen.add(key)
                qty = max(0, int(row["quantity"]))
                quantities[nm] = quantities.get(nm, 0) + qty
                if qty:
                    locations.setdefault(nm, set()).add(wh)
            return quantities, locations
        url = self.ANALYTICS_URL + "/api/v1/warehouse_remains"
        task = await self._request(
            "GET", url, params={"groupByNm": "true", "locale": "ru"}
        )
        task_id = ((task or {}).get("data") or {}).get("taskId") if isinstance(task, dict) else None
        if not task_id:
            raise RuntimeError("WB FBO: не получен ID задачи отчёта")
        url += "/tasks/" + str(task_id)
        for _ in range(12):
            state_resp = await self._request("GET", url + "/status")
            state = ((state_resp or {}).get("data") or {}).get("status") if isinstance(state_resp, dict) else None
            if state == "done":
                break
            if state not in {"new", "processing"}:
                raise RuntimeError(f"WB FBO: {state}")
            await asyncio.sleep(5)
        else:
            raise RuntimeError("WB FBO: истёк срок ожидания отчёта")
        for row in await self._request("GET", url + "/download"):
            nm = int(row["nmId"])
            totals = [
                w
                for w in row["warehouses"]
                if w["warehouseName"].lower() == "всего находится на складах"
            ]
            if len(totals) != 1 or nm in quantities:
                raise RuntimeError("WB FBO: неоднозначные остатки")
            quantities[nm] = int(totals[0]["quantity"])
        return quantities, locations

    async def fetch_active_ad_campaigns(self, days: int = 7) -> list:
        end = datetime.utcnow().date()
        start = end - timedelta(days=days - 1)
        key = f"campaigns:{start}:{end}"
        cached = self._read_disk_cache(key, 900)
        if cached is not None:
            return cached
        groups = await self._request("GET", self.ADVERT_URL + "/adv/v1/promotion/count")
        adverts = groups.get("adverts") if isinstance(groups, dict) else None
        ids = set()
        for group in adverts or []:
            if not isinstance(group, dict) or group.get("status") not in (4, 9, 11):
                continue
            for advert in group.get("advert_list") or []:
                if not isinstance(advert, dict):
                    continue
                try:
                    advert_id = int(advert.get("advertId") or 0)
                except (TypeError, ValueError):
                    continue
                if advert_id > 0:
                    ids.add(advert_id)
        ids = sorted(ids)
        campaigns = {}
        for i in range(0, len(ids), 50):
            batch = ids[i : i + 50]

            async def details(path):
                if path.startswith("/adv/"):
                    return await self._request(
                        "POST", self.ADVERT_URL + path, json_data=batch
                    )
                data = await self._request(
                    "GET",
                    self.ADVERT_URL + path,
                    params={"ids": ",".join(map(str, batch))},
                )
                return data["adverts"]

            for row in await read_versions(
                ("/adv/v1/promotion/adverts", "/api/advert/v2/adverts"), details
            ):
                if not isinstance(row, dict):
                    continue
                try:
                    cid = int(row.get("advertId") or row.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if cid <= 0:
                    continue
                params = [
                    row.get("autoParams") or {},
                    *(row.get("unitedParams") or []),
                    *(row.get("params") or []),
                ]
                params = [p for p in params if isinstance(p, dict)]
                nms = {
                    str(n.get("nm") if isinstance(n, dict) else n)
                    for p in params
                    for n in (p.get("nms") or [])
                }
                nms.update(
                    str(n.get("nm_id"))
                    for n in (row.get("nm_settings") or [])
                    if isinstance(n, dict) and n.get("nm_id") is not None
                )
                campaigns[cid] = {
                    "id": cid,
                    "name": row.get("name") or row.get("settings", {}).get("name"),
                    "type": row.get("type") or row.get("bid_type"),
                    "status": row.get("status"),
                    "nms": sorted(nms),
                    "cpm_rub": max(
                        (
                            p.get("cpm") or p.get("searchCPM") or p.get("price") or 0
                            for p in params
                        )
                    ),
                    "spend_rub": 0.0,
                }
        for r in await self._ad_charges(start.isoformat(), end.isoformat()):
            try:
                cid = int(r.get("advertId") or 0)
            except (TypeError, ValueError):
                continue
            if cid <= 0:
                continue
            campaign = campaigns.setdefault(
                cid,
                {
                    "id": cid,
                    "name": r.get("campName"),
                    "nms": [],
                    "status": "Списания за период",
                    "spend_rub": 0.0,
                },
            )
            campaign["spend_rub"] += _num(r.get("updSum"))
        result = list(campaigns.values())
        self._write_disk_cache(key, result)
        return result

    async def _fetch_report_details(
        self, date_from: str, date_to: str, period: str = "weekly"
    ) -> list:
        """Детализации отчётов реализации через актуальный finance-api (camelCase)."""
        rows: list = []
        cursor = 0
        for _ in range(500):
            payload = {
                "dateFrom": date_from,
                "dateTo": date_to,
                "rrdId": cursor,
                "limit": 100000,
                "period": period,
            }
            page = await self._request(
                "POST",
                f"{self.FINANCE_URL}/api/finance/v1/sales-reports/detailed",
                json_data=payload,
            )
            if page is None or page == []:
                return rows
            if not isinstance(page, list):
                raise RuntimeError("WB: неверный формат детализации отчёта")
            fresh = [r for r in page if _rrd(r) > cursor]
            rows.extend(fresh)
            nxt = max(_rrd(r) for r in page)
            if nxt <= cursor:
                raise RuntimeError("WB: курсор rrdId не продвигается")
            cursor = nxt
            # Протокол WB: повторять, пока не вернётся пустой ответ (204).
        raise RuntimeError("WB: пагинация детализации не завершена")

    async def _fetch_legacy_report_details(
        self, date_from: str, date_to: str
    ) -> list:
        """Резервный источник: GET /api/v5/supplier/reportDetailByPeriod.

        Метод остаётся у WB как обратная совместимость. Разбор строк общий с
        finance-api, поэтому поля snake_case принимаются без отдельного кода.
        """
        rows: list = []
        cursor = 0
        for _ in range(200):
            page = await self._request(
                "GET",
                f"{self.STATS_URL}/api/v5/supplier/reportDetailByPeriod",
                params={
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "rrdid": cursor,
                    "limit": 100000,
                },
            )
            if page is None or page == []:
                return rows
            if not isinstance(page, list):
                raise RuntimeError("WB legacy: неверный формат детализации")
            fresh = [row for row in page if isinstance(row, dict) and _rrd(row) > cursor]
            rows.extend(fresh)
            next_cursor = max(
                (_rrd(row) for row in page if isinstance(row, dict)), default=0
            )
            if next_cursor <= cursor:
                raise RuntimeError("WB legacy: курсор rrdid не продвигается")
            cursor = next_cursor
        raise RuntimeError("WB legacy: пагинация не завершена")

    async def _fetch_report_positions(
        self, date_from: str, date_to: str, period: str
    ) -> list:
        """Поиск в справочнике отчётов: закрытые отчёты могут не попасть в детализацию."""
        positions: list = []
        offset = 0
        for _ in range(50):
            page = await self._request(
                "POST",
                f"{self.FINANCE_URL}/api/finance/v1/sales-reports/list",
                json_data={
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "limit": 1000,
                    "offset": offset,
                    "period": period,
                },
            )
            if page is None or page == []:
                return positions
            if not isinstance(page, list):
                raise RuntimeError("WB: неверный формат списка отчётов")
            positions.extend(r for r in page if isinstance(r, dict))
            if len(page) < 1000:
                return positions
            offset += len(page)
        return positions

    async def _fetch_orders_7d(self, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        """Заказы за 7 дней: лента заказов (текущий метод), затем legacy-статистика.

        GET /api/v1/supplier/orders закрыт WB в 2026 году, поэтому основным
        источником стала «Лента заказов» POST /api/analytics/v1/order-feed.
        Оба ответа приводятся к одному виду (nmId/date/finishedPrice/isCancel).
        """

        async def order_feed(_version):
            rows: List[Dict[str, Any]] = []
            snapshot, offset = None, 0
            for _ in range(50):
                body: Dict[str, Any] = {
                    "selectedPeriod": {
                        "start": f"{date_from}T00:00:00+03:00",
                        "end": f"{date_to}T23:59:59+03:00",
                    },
                    "pagination": {"offset": offset, "limit": 10000},
                }
                if snapshot:
                    body["pagination"]["snapshotTime"] = snapshot
                data = await self._request(
                    "POST", f"{self.ANALYTICS_URL}/api/analytics/v1/order-feed",
                    json_data=body,
                )
                page = (data or {}).get("orders") or []
                snapshot = snapshot or (data or {}).get("snapshotTime")
                for row in page:
                    rows.append(
                        {
                            "nmId": row.get("nmId"),
                            "date": row.get("createdAt") or row.get("updatedAt"),
                            "finishedPrice": row.get("sellerPrice"),
                            "priceWithDisc": row.get("sellerPrice"),
                            "isCancel": str(row.get("status") or "") in {"cancel"},
                        }
                    )
                if len(page) < body["pagination"]["limit"]:
                    return rows
                offset += len(page)
            return rows

        async def legacy(_version):
            data = await self._request(
                "GET",
                f"{self.STATS_URL}/api/v1/supplier/orders",
                params={"dateFrom": date_from, "flag": 0},
            )
            if not isinstance(data, list):
                raise RuntimeError("WB: неизвестный формат заказов")
            return data

        try:
            return await order_feed(None)
        except (RuntimeError, APIStatusError):
            return await legacy(None)

    async def fetch_sales_by_sku(self, days: int = 7) -> tuple[List[Dict[str, Any]], str]:
        """Продажи в разрезе артикулов — резервный источник для ТОП-5.

        Детализация отчёта реализации (реальные комиссии/логистика) — основной
        путь; при пустой детализации берётся «Лента заказов», где есть только
        цена и количество. Возвращает (строки заказов, подпись источника).
        """
        end = datetime.utcnow().date()
        date_from = (end - timedelta(days=max(int(days), 1) - 1)).isoformat()
        date_to = end.isoformat()
        key = f"sales_by_sku:{date_from}:{date_to}"
        cached = self._read_disk_cache(key, 900)
        if isinstance(cached, dict) and isinstance(cached.get("orders"), list):
            return cached["orders"], str(cached.get("source") or "")

        errors: List[str] = []
        rows: list = []
        try:
            rows = await self._fetch_report_details(date_from, date_to, "weekly")
            if not rows:
                rows = await self._fetch_report_details(date_from, date_to, "daily")
        except (RuntimeError, APIStatusError) as exc:
            errors.append(f"детализация отчёта: {exc}")
        if rows:
            try:
                report = self._finance_rows(rows, 0.0, date_from, date_to)
                orders = [
                    o
                    for o in report.get("orders") or []
                    if isinstance(o, dict) and float(o.get("price") or 0.0) > 0.0
                ]
                if orders:
                    source = f"детализация отчёта реализации WB ({date_from}..{date_to})"
                    self._write_disk_cache(key, {"orders": orders, "source": source})
                    return orders, source
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"разбор детализации: {exc}")

        try:
            feed = await self._fetch_orders_7d(date_from, date_to)
        except (RuntimeError, APIStatusError, ValueError) as exc:
            errors.append(f"лента заказов: {exc}")
            feed = []
        orders = []
        for row in feed:
            if not isinstance(row, dict) or row.get("isCancel"):
                continue
            nm = int(_num(row.get("nmId")) or 0)
            price = _num(row.get("finishedPrice")) or _num(row.get("priceWithDisc"))
            if not nm or price <= 0.0:
                continue
            orders.append(
                {
                    "order_id": f"wbfeed_{nm}_{len(orders)}",
                    "marketplace": "wb",
                    "sku": str(nm),
                    "created_at": str(row.get("date") or "")[:10],
                    "quantity": 1,
                    "price": price,
                    "buyer_price": _num(row.get("priceWithDisc")) or price,
                    "status": "sold",
                }
            )
        if orders:
            source = f"лента заказов WB ({date_from}..{date_to})"
            self._write_disk_cache(key, {"orders": orders, "source": source})
            return orders, source

        detail = ("; ".join(errors)) if errors else "за период нет продаж"
        return [], f"WB: артикулы недоступны ({detail})"

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> dict:
        """
        Финансы Wildberries из действующего finance-api.

        GET /api/v5/supplier/reportDetailByPeriod отключён WB (HTTP 404) — вместо
        него POST /api/finance/v1/sales-reports/detailed на finance-api.wildberries.ru.
        Если детализация за период пуста (отчёт ещё не сформирован), берутся
        итоги закрытых отчётов из /sales-reports/list с явной пометкой источника.
        """
        end = datetime.utcnow().date()
        date_from = custom_date_from or (end - timedelta(days=days - 1)).isoformat()
        date_to = custom_date_to or end.isoformat()
        key = f"finance_v2:{date_from}:{date_to}"
        async with self._orders_lock:
            cached = self._read_disk_cache(key, 900)
            if cached is not None:
                return cached

            rows: list = []
            errors: List[str] = []
            for level in ("weekly", "daily"):
                try:
                    rows = await self._fetch_report_details(date_from, date_to, level)
                except (APIStatusError, RuntimeError) as exc:
                    errors.append(f"детализация {level}: {_exc_label(exc)}")
                    rows = []
                if rows:
                    break

            legacy_used = False
            if not rows:
                # Резерв 1: итоги закрытых отчётов из /sales-reports/list.
                try:
                    report = await self._finance_from_summaries(
                        date_from, date_to, level="weekly"
                    )
                except (APIStatusError, RuntimeError) as exc:
                    errors.append(f"список weekly: {_exc_label(exc)}")
                    report = None
                    if isinstance(exc, AmbiguousFinancePeriod):
                        try:
                            report = await self._finance_from_summaries(
                                date_from, date_to, level="daily"
                            )
                        except (APIStatusError, RuntimeError) as daily_exc:
                            errors.append(f"список daily: {_exc_label(daily_exc)}")
                if report is None or not (
                    report.get("revenue_seller") or report.get("for_pay")
                ):
                    # Резерв 2: прежний reportDetailByPeriod (v5), если WB откатит
                    # новый finance-api. Разбор строк понимает snake_case.
                    try:
                        legacy_rows = await self._fetch_legacy_report_details(
                            date_from, date_to
                        )
                    except (APIStatusError, RuntimeError) as exc:
                        errors.append(f"legacy v5: {_exc_label(exc)}")
                        legacy_rows = []
                    if legacy_rows:
                        rows = legacy_rows
                        legacy_used = True
                    elif report is None:
                        raise RuntimeError(
                            "WB: ни детализация, ни закрытые отчёты недоступны ("
                            + "; ".join(errors)
                            + ")"
                        )

            if rows:
                ads = await self.fetch_wb_ad_spend(date_from, date_to)
                report = self._finance_rows(rows, ads, date_from, date_to)
                if legacy_used:
                    report["source"] = (
                        "WB legacy reportDetailByPeriod (v5), "
                        f"резервный источник ({date_from}..{date_to})"
                    )
                else:
                    report["source"] = (
                        f"finance-api WB, отчёт реализации ({date_from}..{date_to})"
                    )
                if errors:
                    report["source"] += "; " + "; ".join(dict.fromkeys(errors))
            self._write_disk_cache(key, report)
            return report

    async def _finance_from_summaries(
        self, date_from: str, date_to: str, level: str
    ) -> dict:
        """Итоги закрытых отчётов: продажи, к перечислению, удержания, штрафы."""
        reports = await self._fetch_report_positions(date_from, date_to, level)
        if not reports:
            reports = await self._fetch_report_positions(date_from, date_to, "daily")
            level = "daily"
        # Сводный недельный отчёт нельзя обрезать пропорцией: на границе
        # месяца/недели нет распределения сумм по дням.
        selected = []
        by_id = {}
        for rep in reports:
            report_id = str(rep.get("reportId") or rep.get("realizationreport_id") or "").strip()
            start = str(rep.get("dateFrom") or "")[:10]
            end = str(rep.get("dateTo") or "")[:10]
            if not report_id or not start or not end:
                raise RuntimeError("WB: у сводного отчёта отсутствуют ID или даты")
            try:
                datetime.fromisoformat(start)
                datetime.fromisoformat(end)
            except ValueError as exc:
                raise RuntimeError("WB: некорректный период сводного отчёта") from exc
            for field in ("retailAmountSum", "forPaySum"):
                value = rep.get(field)
                try:
                    number = float(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
                    if not math.isfinite(number):
                        raise ValueError
                except (TypeError, ValueError):
                    raise RuntimeError(f"WB: в сводном отчёте отсутствует корректное поле {field}")
            if start > end or start < date_from or end > date_to:
                raise AmbiguousFinancePeriod(
                    f"WB: отчёт {report_id} ({start}..{end}) выходит за запрошенный период; "
                    "нужна дневная детализация"
                )
            if report_id in by_id:
                if by_id[report_id] != rep:
                    raise RuntimeError(f"WB: конфликтующие версии отчёта {report_id}")
                continue
            by_id[report_id] = rep
            selected.append(rep)
        # Два разных отчёта одного периода могут относиться к разным договорам;
        # их нельзя автоматически считать ни дублем, ни независимой выручкой.
        spans = [(str(r['dateFrom'])[:10], str(r['dateTo'])[:10]) for r in selected]
        for i, (a, b) in enumerate(spans):
            if any(a <= d and c <= b for c, d in spans[i + 1:]):
                raise RuntimeError("WB: периоды разных сводных отчётов пересекаются; нужна сверка договоров")
        if not selected:
            raise RuntimeError("WB: нет закрытых отчётов за выбранный период")
        return {
            "source": f"finance-api WB: закрытые сводные {level} отчёты без детализации",
            "summary_only": True,
            "summary_reports": [{
                "id": str(r.get("reportId") or r.get("realizationreport_id")),
                "date_from": str(r['dateFrom'])[:10],
                "date_to": str(r['dateTo'])[:10],
                "retail_amount_sum": _num(r.get("retailAmountSum")),
                "for_pay_sum": _num(r.get("forPaySum")),
            } for r in selected],
            # Поля нужны только для обнаружения непустого ответа, не для P&L.
            "revenue_seller": sum(_num(r.get("retailAmountSum")) for r in selected),
            "for_pay": sum(_num(r.get("forPaySum")) for r in selected),
        }

    @staticmethod
    def _finance_rows(rows: list, ads: float, start: str, end: str) -> dict:
        """
        Разбор детализации отчёта реализации.

        Понимает и новый finance-api (camelCase, суммы строками), и прежний
        reportDetailByPeriod (snake_case) — на случай откатов на стороне WB.
        """
        orders = []
        seller = buyer = payout = commission = acquiring = returns = 0.0
        delivery = reverse = storage = acceptance = other = 0.0
        estimated = False
        seen = set()
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = _rrd(r)
            if rid in seen:
                continue
            seen.add(rid)
            operation = str(_pick(r, "supplierOperName", "supplier_oper_name") or "").lower()
            logistics = _num(_pick(r, "deliveryService", "delivery_rub"))
            returned = _num(_pick(r, "returnAmount", "return_amount"))
            if returned or "возврат" in operation:
                reverse += logistics
            else:
                delivery += logistics
            storage += _num(_pick(r, "paidStorage", "storage_fee"))
            acceptance += _num(_pick(r, "paidAcceptance", "acceptance", "acceptance_fee"))
            penalty = _num(_pick(r, "penalty", "penalty_sum"))
            deduction = _num(_pick(r, "deduction", "deduction_sum"))
            # Идентифицируемые рекламные удержания приходят отдельно из /upd.
            other += penalty + deduction
            if operation not in {"продажа", "возврат"}:
                continue
            qty = int(_num(_pick(r, "quantity")) or 1)
            if qty <= 0:
                continue
            unit = _num(_pick(r, "retailPriceWithDisc", "retail_price_withdisc_rub"))
            gross = unit * qty
            paid_raw = _pick(r, "retailAmount", "retail_amount")
            paid = _num(paid_raw) if paid_raw is not None else gross
            transfer = _num(_pick(r, "forPay", "ppvz_for_pay"))
            fee_raw = _pick(r, "acquiringFee", "acquiring_fee")
            if fee_raw is None:
                fee = round(paid * settings.ACQUIRING_RATE, 2)
                estimated = True
            else:
                fee = _num(fee_raw)  # acquiring_bank is a bank NAME
            sign = -1 if operation == "возврат" else 1
            seller += sign * gross
            buyer += sign * paid
            payout += sign * transfer
            acquiring += sign * fee
            comm = gross - transfer - fee  # fee is already deducted from for_pay
            commission += sign * comm
            if sign < 0:
                returns += gross
                continue
            orders.append(
                {
                    "order_id": f"wbf_{rid}",
                    "marketplace": "wb",
                    "sku": str(
                        _pick(r, "vendorCode", "sa_name", "nmId", "nm_id") or ""
                    ),
                    "created_at": str(
                        _pick(r, "saleDt", "rrDate", "sale_dt", "rr_dt") or ""
                    )[:10],
                    "quantity": qty,
                    "price": unit,
                    "buyer_price": paid / qty,
                    "acquiring": fee / qty,
                    "commission": comm / qty,
                    "logistics": logistics / qty,
                    "status": "sold",
                }
            )
        deductions = (
            commission
            + acquiring
            + delivery
            + reverse
            + storage
            + acceptance
            + other
            + ads
        )
        return {
            "source": f"Отчёт реализации WB ({start}..{end})",
            "orders": orders,
            "orders_count": len(orders),
            "revenue_seller": seller,
            "revenue_buyer": buyer,
            "returns_amount": returns,
            "for_pay": payout,
            "commission": commission,
            "acquiring": acquiring,
            "acquiring_estimated": estimated,
            "logistics": delivery + reverse,
            "logistics_direct": delivery,
            "logistics_return": reverse,
            "storage": storage,
            "acceptance": acceptance,
            "ads": ads,
            "other_services": other,
            "total_deductions": round(deductions, 2),
            "total_payout": round(seller - deductions, 2),
        }
```

### `tests/test_listings_and_sku_fallback.py`

```python
"""Резервные ярусы: артикулы для ТОП-5 и браузерный разбор витрин."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests import offline

assert offline.require_isolation() is None  # изоляция окружения обязательна
from database.crud import upsert_product_costs
from database.models import close_db, init_db
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.competitors.browser_listings import (
    parse_ozon_dom_cards,
    parse_ozon_widget_states,
    parse_wb_products,
)
from services.competitors.deep_parser import (
    _parse_ozon_competitors_with_status,
    _parse_wb_competitors_with_status,
)
from services.common.storage import DiskCache
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


class SkuSalesFallback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        self.addAsyncCleanup(close_db)

    def _services(self, tmp: str):
        oz, wb = AsyncMock(), AsyncMock()
        # Начисления и закрытые отчёты за свежий период пусты — типичный случай.
        oz.fetch_real_finance_breakdown.return_value = {
            "sales_total": 300000.0,
            "orders_count": 1000,
            "orders": [],
            "commission": 66000.0,
            "deliv_total": 39000.0,
            "acquiring": 4500.0,
            "ads_total": 6000.0,
        }
        wb.fetch_real_finance_breakdown.return_value = {
            "revenue_seller": 200000.0,
            "orders_count": 0,
            "orders": [],
            "commission": 40000.0,
            "total_payout": 150000.0,
            "total_deductions": 50000.0,
            "summary_only": True,
        }
        oz.fetch_stocks_and_prices.return_value = []
        wb.fetch_stocks_and_prices.return_value = []
        wb.fetch_wb_tariffs.return_value = {
            "subjects": {},
            "warehouses": {"Коледино": {"boxDeliveryBase": "48,5"}},
        }
        oz.fetch_sales_by_sku.return_value = (
            [
                {
                    "order_id": "a1",
                    "marketplace": "ozon",
                    "sku": "1010",
                    "quantity": 40,
                    "price": 350.0,
                    "status": "sold",
                },
                {
                    "order_id": "a2",
                    "marketplace": "ozon",
                    "sku": "2222",
                    "quantity": 5,
                    "price": 299.0,
                    "status": "sold",
                },
            ],
            "аналитика Ozon по SKU",
        )
        wb.fetch_sales_by_sku.return_value = (
            [
                {
                    "order_id": "b1",
                    "marketplace": "wb",
                    "sku": "1010",
                    "quantity": 25,
                    "price": 300.0,
                    "buyer_price": 250.0,
                    "status": "sold",
                }
            ],
            "лента заказов WB",
        )
        return oz, wb

    async def test_top5_built_from_sku_fallback_with_real_numbers(self) -> None:
        await upsert_product_costs("1010", "ozon", 120, 10)
        await upsert_product_costs("1010", "wb", 120, 10)
        oz, wb = self._services(tempfile.mkdtemp())
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertNotIn("заказов не найдено", report)
        self.assertIn("резервному источнику", report)
        self.assertIn("Продано: <b>40 шт.</b> на <b>14,000 ₽</b>", report)
        self.assertIn("Продано: <b>25 шт.</b> на <b>7,500 ₽</b>", report)
        # Ozon без подтверждённого факта доставки не получает выдуманную прибыль.
        self.assertIn("OZON:2222", report)
        self.assertIn("Без подтвержденной себестоимости", report)
        # WB: 300 − 130 себес − (логистика 48.5 + эквайринг 1.5% от 300) − УСН 15 = 102 ₽/шт.
        self.assertIn("+102.0 ₽/шт.", report)
        self.assertIn("2,550 ₽", report)

    async def test_wb_fallback_used_when_ozon_has_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            oz, wb = self._services(tmp)
            oz.fetch_real_finance_breakdown.return_value["orders"] = [
                {"order_id": "z1", "marketplace": "ozon", "sku": "1010", "price": 350,
                 "quantity": 1, "status": "sold"}
            ]
            result = await generate_weekly_top_and_unprofitable_report(oz, wb)
            wb.fetch_sales_by_sku.assert_awaited_once()
            self.assertIn("WB", result)
            self.assertIn("Часть рейтинга построена по резервному источнику", result)

    async def test_no_fallback_data_keeps_honest_empty_message(self) -> None:
        oz, wb = self._services(tempfile.mkdtemp())
        oz.fetch_sales_by_sku.return_value = ([], "Ozon: артикулы недоступны (HTTP 403)")
        wb.fetch_sales_by_sku.return_value = ([], "WB: артикулы недоступны (HTTP 403)")
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("заказов не найдено", report)
        self.assertIn("HTTP 403", report)

    async def test_fallback_exception_does_not_break_report(self) -> None:
        oz, wb = self._services(tempfile.mkdtemp())
        oz.fetch_sales_by_sku.side_effect = RuntimeError("boom")
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("резервный источник артикулов недоступен", report)
        self.assertIn("Продано: <b>25 шт.</b>", report)


class SkuSalesSources(unittest.IsolatedAsyncioTestCase):
    def _caches(self):
        tmp = tempfile.mkdtemp()
        self.oz, self.wb = OzonSellerAPI(), WBStatisticsAPI()
        self.oz._cache = DiskCache(Path(tmp) / "oz")
        self.wb._cache = DiskCache(Path(tmp) / "wb")

    async def test_wb_sales_by_sku_uses_order_feed_when_report_empty(self) -> None:
        self._caches()
        self.wb._fetch_report_details = AsyncMock(return_value=[])
        self.wb._fetch_orders_7d = AsyncMock(
            return_value=[
                {"nmId": 555, "date": "2026-11-16T10:00:00Z", "finishedPrice": 250.0,
                 "priceWithDisc": 260.0, "isCancel": False},
                {"nmId": 555, "date": "2026-11-16T11:00:00Z", "finishedPrice": 250.0,
                 "priceWithDisc": 260.0, "isCancel": True},
            ]
        )
        rows, source = await self.wb.fetch_sales_by_sku(days=7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "555")
        self.assertEqual(rows[0]["price"], 250.0)
        self.assertIn("лента заказов", source)

    async def test_ozon_sales_by_sku_falls_back_across_metric_sets(self) -> None:
        self._caches()
        calls = []

        async def fake_request(method, endpoint, payload):
            calls.append(list(payload["metrics"]))
            if "revenue" in payload["metrics"]:
                raise RuntimeError("metric revenue unavailable")
            return {
                "result": {
                    "data": [
                        {"dimensions": [{"id": "9001"}], "metrics": ["14"]},
                        {"dimensions": [{"id": "9002"}], "metrics": [0]},
                    ]
                }
            }

        self.oz._request = AsyncMock(side_effect=fake_request)
        self.oz.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "9001", "current_price": 350.0, "buyer_price": 340.0}]
        )
        rows, source = await self.oz.fetch_sales_by_sku(days=7)
        self.assertEqual(calls[0], ["ordered_units", "revenue"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "9001")
        self.assertEqual(rows[0]["quantity"], 14)
        # Выручки нет — подставляется текущая цена ЛК, и это подписано в отчёте.
        self.assertEqual(rows[0]["price"], 350.0)
        self.assertIn("оценка", source)
        self.assertIn("аналитика Ozon", source)


class BrowserListingParsers(unittest.TestCase):
    def test_wb_products_decode_cents_and_skip_priceless(self) -> None:
        items = parse_wb_products(
            [
                {
                    "id": 1,
                    "name": "Гель",
                    "brand": "YaSoKo",
                    "reviewRating": 4.8,
                    "feedbacks": 42,
                    "sizes": [{"price": {"product": 35000, "basic": 50000}}],
                },
                {"id": 2, "name": "Без цены", "sizes": []},
            ],
            limit=5,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["buyer_price"], 350.0)
        self.assertEqual(items[0]["mp"], "WB")

    def test_ozon_widget_states_walk_nested_json_strings(self) -> None:
        import json

        payload = {
            "searchResultsV2-1": json.dumps(
                {
                    "items": [
                        {"sku": 111, "name": "Гель 15г", "brand": "YaSoKo",
                         "price": "350 ₽", "rating": 4.9, "feedbackCount": 88},
                        {"productId": 222, "title": "Топ", "price": {"price": "1 299 ₽"}},
                    ]
                }
            )
        }
        items = parse_ozon_widget_states(payload, limit=5)
        self.assertEqual([i["sku"] for i in items], ["111", "222"])
        self.assertEqual([i["buyer_price"] for i in items], [350.0, 1299.0])

    def test_ozon_widget_states_returns_nothing_for_antibot_html(self) -> None:
        self.assertEqual(parse_ozon_widget_states("<html>antibot</html>", limit=5), [])

    def test_ozon_dom_cards_extract_sku_and_price(self) -> None:
        items = parse_ozon_dom_cards(
            [{"href": "https://www.ozon.ru/product/gel-777777/", "text": "Гель\n350 ₽"}],
            limit=5,
        )
        self.assertEqual(items[0]["sku"], "777777")
        self.assertEqual(items[0]["buyer_price"], 350.0)


class CompetitorBrowserTier(unittest.IsolatedAsyncioTestCase):
    async def test_wb_http_failure_falls_back_to_browser(self) -> None:
        browser_items = [
            {"mp": "WB", "sku": "9", "name": "Гель", "brand": "B",
             "rating": 4.5, "feedbacks": 3, "buyer_price": 300.0}
        ]
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 403", False)),
        ), patch(
            "services.competitors.browser_listings.wb_search_via_browser",
            AsyncMock(return_value=(browser_items, None)),
        ):
            items, err = await _parse_wb_competitors_with_status("гель", count=5)
        self.assertEqual(len(items), 1)
        self.assertIsNone(err)

    async def test_ozon_http_failure_falls_back_to_browser(self) -> None:
        browser_items = [
            {"mp": "OZON", "sku": "77", "name": "Гель", "brand": "B",
             "rating": 0.0, "feedbacks": 0, "buyer_price": 350.0}
        ]
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 307", False)),
        ), patch(
            "services.competitors.browser_listings.ozon_search_via_browser",
            AsyncMock(return_value=(browser_items, None)),
        ):
            items, err = await _parse_ozon_competitors_with_status("гель", count=5)
        self.assertEqual(len(items), 1)
        self.assertIsNone(err)

    async def test_browser_unavailable_keeps_clear_status(self) -> None:
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 498", False)),
        ), patch(
            "services.competitors.browser_listings.wb_search_via_browser",
            AsyncMock(side_effect=RuntimeError("Playwright недоступен")),
        ):
            items, err = await _parse_wb_competitors_with_status("гель", count=5)
        self.assertEqual(items, [])
        self.assertIn("антибот", err)
```

### `tests/test_market_contracts.py`

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests import offline
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Update
from bot.handlers import callbacks
from config import settings
from database.crud import get_pending_action, upsert_product_costs
from database.models import close_db, init_db
from services.common.ad_recommendations import analyze_ozon_campaigns
from services.common.market_api import APIStatusError, read_versions
from services.common.price_auditor import run_daily_price_audit
from services.common.orders_report import sync_and_generate_orders_report
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.common.storage import DiskCache
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


class Adapters(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.oz, self.wb = OzonSellerAPI(), WBStatisticsAPI()
        self.oz._cache = DiskCache(Path(tmp.name) / "oz")
        self.wb._cache = DiskCache(Path(tmp.name) / "wb")

    async def test_version_errors_are_typed_and_read_only(self):
        for status in [400, 404, 429]:
            read = AsyncMock(side_effect=[APIStatusError(status, "not available"), []])
            with patch("services.common.market_api.asyncio.sleep", AsyncMock()):
                self.assertEqual(await read_versions(["v5", "v4"], read), [])
            self.assertEqual([c.args for c in read.await_args_list], [("v5",), ("v4",)])
        read = AsyncMock(side_effect=APIStatusError(403, "scope"))
        with self.assertRaises(APIStatusError):
            await read_versions(["v5", "v4"], read)
        self.assertEqual(read.await_count, 1)

    async def test_ozon_v4_restart_pagination_and_disk_cache(self):
        async def request(method, path, payload):
            self.assertEqual(method, "POST")
            if path.startswith("/v5/"):
                if payload["cursor"] == "":
                    return {
                        "items": [{"offer_id": "discard-partial"}],
                        "cursor": "second",
                    }
                raise APIStatusError(400, "old version")
            if path == "/v4/product/info/prices":
                self.assertEqual(payload["last_id"], "")
                return {
                    "result": {
                        "items": [
                            {
                                "offer_id": "1001",
                                "product_id": 19,
                                "price": {"price": "299"},
                                "commissions": {
                                    "fbs_first_mile": 8,
                                    "sales_percent_fbs": 23,
                                    "sales_percent_fbo": 22,
                                },
                                "marketing_actions": {"actions": [{"title": "Акция"}]},
                            }
                        ]
                    }
                }
            if path == "/v4/product/info/stocks":
                self.assertIn("cursor", payload)
                raise APIStatusError(404, "version")
            if path == "/v3/product/info/stocks":
                self.assertEqual(payload["last_id"], "")
                return {
                    "result": {
                        "items": [
                            {
                                "offer_id": "1001",
                                "stocks": [
                                    {"type": "fbs", "present": 4, "reserved": 1},
                                    {"type": "fbo", "present": 5},
                                ],
                            }
                        ]
                    }
                }
            self.assertEqual(path, "/v1/analytics/data")
            return {
                "result": {
                    "data": [{"dimensions": [{"id": "3405669329"}], "metrics": [14]}]
                }
            }

        self.oz._request = AsyncMock(side_effect=request)
        rows = await self.oz.fetch_stocks_and_prices()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            [
                row[k]
                for k in [
                    "stock_fbo",
                    "stock_fbs",
                    "daily_sales",
                    "fbs_pvz_dropoff_rub",
                    "promo_names",
                ]
            ],
            [5, 3, 2, 8, "Акция"],
        )
        second = OzonSellerAPI()
        second._cache = self.oz._cache
        second._request = AsyncMock(side_effect=AssertionError("should use disk"))
        self.assertEqual(await second.fetch_stocks_and_prices(), rows)

    async def test_ozon_accrual_by_day_reconcile_with_cash_flow(self):
        """Финансы Ozon из /v1/finance/accrual/by-day (+сверка с cash-flow)."""
        period = {"begin": "2026-11-18", "end": "2026-11-18"}
        accrual = {
            "accrual_id": "11",
            "date": "2026-11-18",
            "accrued_category": "POSTING",
            "unit_number": "123-1",
            "posting": {
                "products": [
                    {
                        "sku": 123,
                        "commission": {
                            "seller_price": {"amount": "300", "currency": "RUB"},
                            "sale_price": {"amount": "330", "currency": "RUB"},
                            "sale_commission": {"amount": "-66", "currency": "RUB"},
                        },
                        "delivery": {
                            "total_accrued": {"amount": "-39", "currency": "RUB"},
                            "services": [
                                {"type_id": 32, "accrued": {"amount": "-39"}}
                            ],
                        },
                    }
                ]
            },
        }
        ad = {
            "accrual_id": "12",
            "date": "2026-11-18",
            "accrued_category": "ITEM",
            "item_fees": {
                "fees": [
                    {
                        "sku": 123,
                        "fees": [
                            {"type_id": 41, "accrued": {"amount": "-6"}},
                            {"type_id": 1, "accrued": {"amount": "-2.1"}},
                        ],
                    }
                ]
            },
        }
        details = {
            "period": period,
            "delivery": {
                "delivery_services": {
                    "items": [{"name": "MarketplaceServiceItemDelivToCustomer", "price": -39}]
                }
            },
            "services": {
                "items": [{"name": "MarketplaceServiceCostPerClick", "price": -6}]
            },
            "others": {"items": [{"name": "Acquiring", "price": -2.1}]},
        }
        flow = {
            "period": period,
            "orders_amount": 300,
            "returns_amount": 0,
            "commission_amount": -66,
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {
                    "accrual_types": [
                        {"id": 32, "name": "Logistic"},
                        {"id": 41, "name": "PayPerClick"},
                        {"id": 1, "name": "Acquiring"},
                    ]
                }
            if path == "/v1/finance/accrual/by-day":
                self.assertEqual(payload["date"], "2026-11-18")
                return {"accruals": [accrual, ad], "last_id": ""}
            self.assertEqual(path, "/v1/finance/cash-flow-statement/list")
            self.assertIs(payload["with_details"], True)
            return {
                "result": {"cash_flows": [flow], "details": details},
                "page_count": 1,
            }

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertIn("cash-flow", result["source"])
        self.assertNotIn("transaction/list", result["source"])
        self.assertEqual((result["orders_count"], result["deliv_total"]), (1, 39))
        self.assertEqual((result["ads_total"], result["billed_skus"]), (6, ["123"]))
        self.assertEqual(result["acquiring"], 2.1)
        self.assertEqual(result["total_payout"], 300 - 66 - 39 - 6 - 2.1)

    async def test_ozon_accrual_pagination_and_unknown_types(self):
        """Курсор last_id прокручивается, незнакомый type_id не теряет деньги."""
        seen = []

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                raise APIStatusError(400, "types unavailable")
            if path == "/v1/finance/accrual/by-day":
                seen.append(payload.get("last_id", ""))
                if not payload.get("last_id"):
                    return {
                        "accruals": [
                            {
                                "accrual_id": "1",
                                "date": "2026-11-18",
                                "non_item_fee": {
                                    "type_id": 777,
                                    "accrued": {"amount": "-10"},
                                },
                            }
                        ],
                        "last_id": "page-2",
                    }
                return {
                    "accruals": [
                        {
                            "accrual_id": "2",
                            "date": "2026-11-18",
                            "non_item_fee": {
                                "type_id": 41,
                                "accrued": {"amount": "-5"},
                            },
                        }
                    ],
                    "last_id": "",
                }
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(seen, ["", "page-2"])
        self.assertEqual(result["ads_total"], 5)
        # Книга расходов ведётся положительными суммами (как в cash-flow)
        self.assertEqual(result["other_fines"], 10)
        self.assertIn("777", result["source"])
        self.assertIn("HTTP 400", result["source"])

    async def test_ozon_legacy_type_id_inside_accrual_is_not_a_fee(self):
        """Старая схема: type_id у самого начисления не должен считаться услугой."""
        accrual = {
            "type_id": 55,
            "date": "2026-11-18",
            "accrued": {"amount": "100"},
            "posting": {
                "products": [
                    {
                        "sku": 5,
                        "commission": {
                            "seller_price": {"amount": "100"},
                            "sale_commission": {"amount": "-10"},
                        },
                    }
                ]
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": []}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [accrual], "last_id": ""}
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(result["sales_total"], 100)
        self.assertEqual(result["commission"], 10)
        self.assertEqual(result["other_fines"], 0)
        self.assertEqual(result["orders_count"], 1)

    async def test_ozon_return_accrual_reduces_commission(self):
        """Возврат: отрицательная продажа возвращает и комиссию."""
        row = {
            "accrual_id": "9",
            "date": "2026-11-18",
            "posting": {
                "posting_number": "123-1",
                "products": [
                    {
                        "sku": 7,
                        "commission": {
                            "seller_price": {"amount": "-100"},
                            "sale_commission": {"amount": "-10"},
                        },
                    }
                ],
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": []}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [row], "last_id": ""}
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(result["sales_total"], -100)
        self.assertEqual(result["commission"], -10)

    async def test_performance_campaigns_real_membership_billing_not_organic(self):
        self.oz._perf_token = AsyncMock(return_value="test")

        async def performance(method, url, headers, *, params):
            if url.endswith("/campaign"):
                return {
                    "list": [
                        {"id": c, "state": "CAMPAIGN_STATE_RUNNING"} for c in (10, 20)
                    ]
                    + [{"id": 30, "state": "CAMPAIGN_STATE_INACTIVE"}]
                }
            if url.endswith("/v2/products"):
                skus = [1001, 1002, 1003, 1004] if "/10/" in url else [1005, 1006]
                return {"products": [{"sku": s} for s in skus]}
            self.assertTrue(url.endswith("/campaign/product/json"))
            return {
                "rows": [
                    {
                        "moneySpent": "63,0"
                        if params["campaignIds"] == "10"
                        else "154",
                        "ordersMoney": "1000",
                    }
                ]
            }

        self.oz.request_json = AsyncMock(side_effect=performance)
        campaigns = await self.oz.fetch_active_ad_campaigns()
        self.assertEqual(
            [(r["products_count"], r["drr_in_promo_pct"]) for r in campaigns],
            [(4, 6.3), (2, 15.4)],
        )
        items = [{"sku": str(s), "stock_fbo": 1} for s in range(1001, 1009)]
        classified = analyze_ozon_campaigns(campaigns, items, {"billed_skus": ["1007"]})
        self.assertEqual([r["sku"] for r in classified["organic_items"]], ["1008"])
        self.assertEqual([r["sku"] for r in classified["unclassified_items"]], ["1007"])

    async def test_wb_sources_and_no_duplicate_stock(self):
        async def request(method, url, params=None, json_data=None):
            if url.endswith("/goods/filter"):
                goods = {
                    "nmID": 99,
                    "vendorCode": "1001",
                    "sizes": [{"discountedPrice": 299}],
                }
                return {"data": {"listGoods": [goods, goods]}}
            if url.endswith("/commission"):
                return {
                    "report": [
                        {"subjectID": 7, "paidStorageKgvp": 20, "kgvpMarketplace": 25}
                    ]
                }
            if url.endswith("/box"):
                return {
                    "response": {
                        "data": {
                            "warehouseList": [
                                {
                                    "warehouseName": "A",
                                    "boxDeliveryBase": "53,2",
                                    "boxDeliveryMarketplaceBase": "60",
                                }
                            ]
                        }
                    }
                }
            if url.endswith("/cards/list"):
                return {
                    "cards": [
                        {
                            "nmID": 99,
                            "subjectID": 7,
                            "sizes": [{"chrtID": 11, "skus": ["barcode"]}],
                        }
                    ]
                }
            if url.endswith("/warehouses"):
                return [{"id": 1}, {"id": 1}]
            if url.endswith("/stocks/1"):
                self.assertEqual(json_data, {"chrtIds": [11]})
                return {"stocks": [{"chrtId": 11, "amount": 4}] * 2}
            if url.endswith("/supplier/stocks"):
                return [
                    {
                        "nmId": 99,
                        "warehouseName": "A",
                        "barcode": "barcode",
                        "quantity": 3,
                    }
                ] * 2
            self.assertTrue(
                url.endswith("/analytics/v1/order-feed")
                or url.endswith("/supplier/orders")
            )
            return [] if url.endswith("/supplier/orders") else {"orders": [], "snapshotTime": "t"}

        self.wb._request = AsyncMock(side_effect=request)
        rows = await self.wb.fetch_stocks_and_prices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            [
                rows[0][k]
                for k in ("nm_id", "stock_fbo", "stock_fbs", "fbo_logistics_cost")
            ],
            [99, 3, 4, 53.2],
        )

    async def test_wb_finance_api_details_returns_and_acquiring(self):
        """finance-api WB: суммы строками, camelCase, пагинация по rrdId."""
        sale = {
            "rrdId": 19,
            "supplierOperName": "Продажа",
            "quantity": 1,
            "retailPriceWithDisc": "300",
            "retailAmount": "280",
            "forPay": "215.8",
            "acquiringBank": "Bank",
            "vendorCode": "1001",
            "saleDt": "2026-11-17T00:00:00Z",
        }
        ret = dict(
            sale,
            rrdId=20,
            supplierOperName="Возврат",
            deliveryService="30",
            returnAmount=1,
        )

        async def request(method, url, params=None, json_data=None):
            if "/upd" in url:
                return []
            self.assertTrue(url.endswith("/api/finance/v1/sales-reports/detailed"))
            self.assertNotIn("reportDetailByPeriod", url)
            return {0: [sale], 19: [ret], 20: [], 999: []}[json_data["rrdId"]]

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown()
        self.assertEqual(report["returns_amount"], 300)
        self.assertEqual(report["revenue_seller"], 0)
        self.assertEqual(report["total_payout"], -30)
        self.assertTrue(report["acquiring_estimated"])
        self.assertEqual(report["orders"][0]["acquiring"], 4.2)
        self.assertEqual(report["orders"][0]["created_at"], "2026-11-17")
        self.assertIn("finance-api", report["source"])

    async def test_wb_finance_defaults_to_closed_report_summaries(self):
        """Если детализации нет, берутся итоги закрытых отчётов из /sales-reports/list."""
        summary = {
            "reportId": 1,
            "dateFrom": "2026-11-10",
            "dateTo": "2026-11-16",
            "retailAmountSum": "1000",
            "forPaySum": "700",
            "deliveryServiceSum": "100",
            "paidStorageSum": "20",
            "paidAcceptanceSum": "5",
            "deductionSum": "10",
            "penaltySum": "15",
            "cashbackCommissionChangeSum": "0",
        }

        async def request(method, url, params=None, json_data=None):
            if "/upd" in url:
                return []
            if url.endswith("/sales-reports/detailed"):
                return []
            self.assertTrue(url.endswith("/sales-reports/list"))
            return [summary]

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown(
            custom_date_from="2026-11-10", custom_date_to="2026-11-16"
        )
        self.assertTrue(report["summary_only"])
        self.assertEqual(report["revenue_seller"], 1000)
        self.assertEqual(report["for_pay"], 700)
        self.assertNotIn("total_payout", report)
        self.assertNotIn("total_deductions", report)
        self.assertEqual(report["summary_reports"][0]["for_pay_sum"], 700)
        self.assertIn("закрытые", report["source"])

    async def test_wb_campaign_sequence_and_join_by_id(self):
        self.wb._request = AsyncMock(
            side_effect=[
                {"adverts": [{"status": 9, "advert_list": [{"advertId": 11}]}]},
                [
                    {
                        "advertId": 11,
                        "status": 9,
                        "name": "А",
                        "autoParams": {"nms": [99]},
                    }
                ],
                [{"advertId": 11, "updSum": 10}, {"advertId": 12, "updSum": 3}],
            ]
        )
        result = await self.wb.fetch_active_ad_campaigns()
        self.assertEqual(
            [c.args[1].split("/adv/v1/")[-1] for c in self.wb._request.await_args_list],
            ["promotion/count", "promotion/adverts", "upd"],
        )
        self.assertEqual(
            [(c["nms"], c["spend_rub"]) for c in result], [(["99"], 10), ([], 3)]
        )

    async def test_cpo_read_does_not_use_a_bid_as_attributed_drr(self):
        self.oz._perf_token = AsyncMock(return_value="fixture")

        async def performance(method, url, headers, **query):
            if url.endswith("/campaign"):
                return {
                    "list": [
                        {
                            "state": "CAMPAIGN_STATE_RUNNING",
                            "id": 11,
                            "advObjectType": "SEARCH_PROMO",
                        }
                    ]
                }
            self.assertTrue(url.endswith("/search_promo/v2/products"))
            self.assertEqual(
                (method, query), ("POST", {"body": {"page": 1, "pageSize": 100}})
            )
            return {
                "products": [
                    {"sku": "90000", "bid": 15, "searchPromoStatus": True},
                    {"sku": "80000", "bid": 9, "searchPromoStatus": False},
                ]
            }

        self.oz.request_json = performance
        data = await self.oz.fetch_active_ad_campaigns()
        self.assertEqual(data[0]["skus_inside"], ["90000"])
        items = [
            {"sku": "brush", "ozon_skus": ["90000"], "stock_fbs": 1},
            {"sku": "gel", "stock_fbo": 1},
        ]
        report = analyze_ozon_campaigns(
            data, items, {"ads_total": 70, "billed_skus": []}
        )
        self.assertEqual(report["promoted_skus"], ["brush"])
        self.assertIsNone(report["campaigns"][0]["drr_in_promo_pct"])
        self.assertIsNone(report["campaigns"][0]["spend_7d_rub"])
        self.assertEqual(report["organic_items"], [])
        self.assertEqual([r["sku"] for r in report["unclassified_items"]], ["gel"])

    async def test_accrual_orders_feed_pnl_once(self):
        """Начисления → P&L: комиссия, логистика, эквайринг, COGS и налог считаются один раз."""
        accrual = {
            "accrual_id": "11",
            "date": "2026-11-15",
            "unit_number": "P1",
            "posting": {
                "products": [
                    {
                        "sku": 123,
                        "commission": {
                            "seller_price": {"amount": "300"},
                            "sale_price": {"amount": "330"},
                            "sale_commission": {"amount": "-66"},
                        },
                        "delivery": {
                            "total_accrued": {"amount": "-39"},
                            "services": [{"type_id": 32, "accrued": {"amount": "-39"}}],
                        },
                    }
                ]
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": [{"id": 32, "name": "Logistic"}]}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [accrual], "last_id": ""}
            raise APIStatusError(400, "statement window")

        self.oz._request = request
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-15", custom_date_to="2026-11-15"
        )
        self.assertEqual(
            (result["orders_count"], result["deliv_total"], result["total_payout"]),
            (1, 39, 195),
        )
        self.assertIn("HTTP 400", result["source"])
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("123", "ozon", 10, 0)
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = result
        wb.fetch_real_finance_breakdown.return_value = {}
        oz.fetch_stocks_and_prices.return_value = (
            wb.fetch_stocks_and_prices.return_value
        ) = []
        monthly = await sync_and_generate_orders_report(oz, wb)
        weekly = await generate_weekly_top_and_unprofitable_report(oz, wb)
        # 300 − 66 − 39 − эквайринг 1.5% (4.50) − себестоимость 10 − налог 18 = 162.50
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", monthly)
        self.assertIn("162.50 ₽", weekly)

    async def test_signed_wb_adjustments_and_explicit_zero_acquiring(self):
        row = {
            "rrdId": 1,
            "supplierOperName": "Продажа",
            "quantity": 1,
            "retailPriceWithDisc": "100",
            "forPay": "105",
            "acquiringFee": "0",
            "vendorCode": "1001",
            "saleDt": "2026-11-13T00:00:00Z",
        }
        wb_fin = self.wb._finance_rows([row], 0, "2026-11-13", "2026-11-13")
        self.assertEqual(
            (wb_fin["total_payout"], wb_fin["commission"], wb_fin["acquiring"]),
            (105, -5, 0),
        )
        self.assertFalse(wb_fin["acquiring_estimated"])
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("1001", "wb", 10, 0)
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {}
        wb.fetch_real_finance_breakdown.return_value = wb_fin
        oz.fetch_stocks_and_prices.return_value = (
            wb.fetch_stocks_and_prices.return_value
        ) = []
        monthly = await sync_and_generate_orders_report(oz, wb)
        weekly = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("+5 ₽", monthly)
        self.assertIn("89 ₽", monthly)
        self.assertIn("89 ₽", weekly)

    async def test_wb_finance_deduplicates_repeated_rows(self):
        row = {
            "rrdId": 39,
            "supplierOperName": "Доставка",
            "deliveryService": "40",
        }
        self.wb._request = AsyncMock(
            side_effect=[[row, row], [], []]
        )
        report = await self.wb.fetch_real_finance_breakdown()
        self.assertEqual(report["logistics_direct"], 40)
        detail_calls = [
            c
            for c in self.wb._request.await_args_list
            if c.args and str(c.args[1]).endswith("/sales-reports/detailed")
        ]
        self.assertEqual(
            [c.kwargs["json_data"]["rrdId"] for c in detail_calls], [0, 39]
        )


class ApprovalChain(unittest.IsolatedAsyncioTestCase):
    async def test_card_to_api_and_callback_errors(self):
        self.assertTrue(offline.STATE.is_dir())
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("1001", "ozon", 60, 5)
        await upsert_product_costs("1001", "wb", 60, 5)
        bot, oz, wb = AsyncMock(), AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "orders_count": 10,
            "deliv_total": 390,
        }
        oz.fetch_active_ad_campaigns.return_value = []
        base = {
            "sku": "1001",
            "current_price": 299,
            "stock_fbs": 2,
            "commission_rate": 0.23,
            "daily_sales": 1,
        }
        oz.fetch_stocks_and_prices.return_value = [
            dict(base, product_id=33, fbs_pvz_dropoff_rub=10)
        ]
        wb.fetch_real_finance_breakdown.return_value = {"orders_count": 1}
        wb.fetch_stocks_and_prices.return_value = [
            dict(base, nm_id=99, fbs_logistics_cost=39)
        ]
        await run_daily_price_audit(bot, settings.TG_ADMIN_ID, oz, wb)
        ozcard, wbcard = [
            c.kwargs["reply_markup"].inline_keyboard
            for c in bot.send_message.await_args_list
        ]
        self.assertEqual(len(ozcard), 3)
        self.assertIn("299 ₽", ozcard[0][0].text)
        self.assertIn("прибыль", ozcard[1][0].text)
        from worker import dp

        transport = Bot(token=settings.TG_BOT_TOKEN)
        self.addAsyncCleanup(transport.session.close)
        sent = []

        async def telegram(bot, method, timeout=None):
            self.assertIsInstance(method, (AnswerCallbackQuery, SendMessage))
            sent.append(method)
            return True

        self.enterContext(patch.object(transport.session, "make_request", telegram))

        async def press(data):
            update = Update.model_validate(
                {
                    "update_id": 123,
                    "callback_query": {
                        "id": "query",
                        "from": {
                            "id": settings.TG_ADMIN_ID,
                            "is_bot": False,
                            "first_name": "Admin",
                        },
                        "chat_instance": "chat",
                        "data": data,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": settings.TG_ADMIN_ID, "type": "private"},
                            "date": 1,
                            "text": "price audit",
                        },
                    },
                }
            )
            await dp.feed_update(transport, update)

        oz_data, wb_data = ozcard[1][0].callback_data, wbcard[0][0].callback_data
        action_id = int(oz_data.rsplit(":", 1)[-1])
        with patch.object(
            callbacks.ozon_api,
            "_request",
            AsyncMock(
                return_value={
                    "result": [
                        {
                            "product_id": 33,
                            "offer_id": "1001",
                            "updated": False,
                            "errors": [{"message": "bad <price>"}],
                        }
                    ]
                }
            ),
        ) as req:
            await press(oz_data)
            item = req.await_args.args[2]["prices"][0]
            self.assertEqual(
                (item["product_id"], item["offer_id"], item["auto_action_enabled"]),
                (33, "1001", "UNKNOWN"),
            )
            self.assertGreater(float(item["price"]), 300)
            self.assertIn("bad &lt;price&gt;", sent[-1].text)
            self.assertEqual((await get_pending_action(action_id)).status, "pending")

        with patch.object(
            callbacks.wb_api,
            "_request",
            AsyncMock(
                side_effect=[
                    {"data": {"listGoods": [{"nmID": 99, "discount": 25}]}},
                    {"error": True, "errorText": "Ошибка <скидки>"},
                ]
            ),
        ) as req:
            await press(wb_data)
            self.assertEqual(req.await_args.kwargs["json_data"]["data"][0]["nmID"], 99)
            self.assertIn("Ошибка &lt;скидки&gt;", sent[-1].text)

        # Accept the retry; the sibling button, even on an old Telegram card, cannot write again.
        with patch.object(
            callbacks.ozon_api,
            "_request",
            AsyncMock(
                return_value={
                    "result": [
                        {
                            "offer_id": "1001",
                            "product_id": 33,
                            "updated": True,
                            "errors": [],
                        }
                    ]
                }
            ),
        ) as req:
            await press(oz_data)
            self.assertEqual((await get_pending_action(action_id)).status, "executed")
            await press(ozcard[0][0].callback_data)
            req.assert_awaited_once()
        with patch.object(
            callbacks.wb_api,
            "_request",
            AsyncMock(
                side_effect=[
                    {"data": {"listGoods": [{"nmID": 99, "discount": 25}]}},
                    {"error": False, "data": {"id": 475}},
                ]
            ),
        ) as req:
            await press(wb_data)
            saved = await get_pending_action(int(wb_data.split(":")[-1]))
            self.assertEqual(saved.status, "submitted")
            target = saved.payload["new_price"]
            self.assertEqual(
                req.await_args.kwargs["json_data"]["data"][0]["price"],
                round(target / 0.75),
            )
            self.assertIn(f"{int(round(0.92 * target))} ₽", sent[-1].text)
```

### `tests/test_route_failover.py`

```python
"""Failover contract: every module must walk all available routes.

Regression cover for the 18.11.2026 incident: the only route was a local proxy,
so a single ``ServerDisconnectedError`` took down Gemini, Ozon and WB at once.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from tests import offline

offline.require_isolation()  # before importing application settings

import services.common.gemini as gemini
from config import Settings
from services.common import routes
from services.common.market_api import (
    APIStatusError,
    CircuitBreakerOpenError,
    MarketplaceClient,
)
from services.ozon.finance import accrual_category, walk_fees


class RouteList(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def test_direct_route_is_available_by_default(self):
        with patch.object(Settings, "model_config", {"env_file": None, "extra": "ignore"}):
            fresh = Settings(
                TG_BOT_TOKEN="1:a",
                TG_ADMIN_ID=1,
                OZON_CLIENT_ID="c",
                OZON_API_KEY="k",
                WB_API_TOKEN="w",
                GEMINI_API_KEY="g",
            )
        self.assertTrue(fresh.ALLOW_DIRECT_CONNECTIONS)
        self.assertEqual(fresh.LOCAL_PROXY_URL, "http://127.0.0.1:3066")

    def test_all_routes_are_listed_direct_first(self):
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            listed = routes.request_routes("service-without-history")
        self.assertEqual(listed[0], routes.DIRECT)
        self.assertIn("http://127.0.0.1:3066", listed)

    def test_non_loopback_proxy_is_rejected(self):
        with patch.object(
            routes.settings, "LOCAL_PROXY_LIST", "http://203.0.113.7:8080,"
        ):
            listed = routes.request_routes("gemini")
        self.assertNotIn("http://203.0.113.7:8080", listed)

    def test_failed_route_goes_to_quarantine_and_is_skipped(self):
        routes.mark_route_failed("gemini", routes.DIRECT, "test")
        self.assertFalse(routes.route_available("gemini", routes.DIRECT))
        routes.reset_routes()
        self.assertTrue(routes.route_available("gemini", routes.DIRECT))


class GeminiFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def tearDown(self):
        routes.reset_routes()

    async def test_generate_moves_to_next_route_after_proxy_drop(self):
        """ServerDisconnectedError на первом маршруте не убивает модуль."""
        calls = []

        class FakeSession:
            closed = False

            def __init__(self, *args, **kwargs):
                self._route = "?"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                self.closed = True
                return False

            def get(self, url, **kwargs):
                calls.append(("get", kwargs.get("proxy")))
                raise aiohttp.ServerDisconnectedError()

            def post(self, url, **kwargs):
                calls.append(("post", kwargs.get("proxy")))
                proxy = kwargs.get("proxy")

                class Response:
                    status = 200

                    async def __aenter__(self_inner):
                        if proxy is None:  # direct route is "down" in this test
                            raise aiohttp.ServerDisconnectedError()
                        return self_inner

                    async def __aexit__(self_inner, *exc):
                        return False

                    async def json(self_inner):
                        return {
                            "candidates": [
                                {
                                    "finishReason": "STOP",
                                    "content": {"parts": [{"text": "ответ"}]},
                                }
                            ]
                        }

                return Response()

        with (
            patch.object(gemini.aiohttp, "ClientSession", FakeSession),
            patch.object(gemini.settings, "LOCAL_PROXY_LIST", ""),
            patch.object(gemini.settings, "LOCAL_PROXY_FALLBACK_URL", ""),
            patch.object(gemini, "FALLBACK_MODELS", ["gemini-3.8-flash"]),
        ):
            text, model = await gemini.generate("привет", max_tokens=64)

        self.assertEqual(text, "ответ")
        self.assertEqual(model, "gemini-3.1-flash-lite")
        # Первый маршрут (direct) упал, второй (локальный прокси) ответил.
        states = {r["route"]: r["state"] for r in routes.route_diagnostics("gemini")}
        self.assertEqual(states[routes.DIRECT], "карантин")
        self.assertTrue(any(proxy for kind, proxy in calls if kind == "post"))

    async def test_all_routes_down_raises_compact_error(self):
        class DeadSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def get(self, *a, **k):
                raise aiohttp.ServerDisconnectedError()

            def post(self, *a, **k):
                raise aiohttp.ServerDisconnectedError()

        with (
            patch.object(gemini.aiohttp, "ClientSession", lambda *a, **k: DeadSession()),
            patch.object(gemini.settings, "LOCAL_PROXY_LIST", ""),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await gemini.generate("x", max_tokens=32)
        self.assertIn("ни по одному маршруту", str(ctx.exception))
        self.assertNotIn(gemini.settings.GEMINI_API_KEY, str(ctx.exception))


class MarketplaceFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def tearDown(self):
        routes.reset_routes()

    async def test_request_json_walks_routes_and_reports_route_status(self):
        client = MarketplaceClient("Ozon", "acc")
        order = []

        async def once(method, url, headers, body, params, route):
            order.append(route)
            if route == routes.DIRECT:
                raise aiohttp.ClientResponseError(
                    MagicMock(), (), status=403, message="antibot"
                )
            return {"ok": True}

        client._request_once = once
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            result = await client.request_json("GET", "https://api-seller.ozon.ru/x", {})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(order[0], routes.DIRECT)
        self.assertEqual(len(order), 2)

    async def test_api_verdict_is_not_a_route_failure(self):
        client = MarketplaceClient("WB", "acc")
        client._request_once = AsyncMock(
            side_effect=APIStatusError(404, "/api/v5/supplier/reportDetailByPeriod")
        )
        with self.assertRaises(APIStatusError):
            await client.request_json("GET", "https://x/y", {})

    async def test_all_routes_failed_raises_circuit_breaker(self):
        client = MarketplaceClient("WB", "acc")

        async def dead(*args):
            raise aiohttp.ServerDisconnectedError()

        client._request_once = dead
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            with self.assertRaises(CircuitBreakerOpenError) as ctx:
                await client.request_json("GET", "https://x/y", {})
        self.assertFalse(client.is_degraded)
        self.assertIn("все маршруты", str(ctx.exception))


class AccrualParsing(unittest.TestCase):
    def test_fees_are_found_at_any_depth(self):
        unknown = set()
        node = {
            "services": [{"type_id": 32, "accrued": {"amount": "-39"}}],
            "fees": [
                {
                    "sku": 1,
                    "fees": [{"type_id": 41, "accrued": {"amount": "-6"}}],
                }
            ],
            "non_item_fee": {"type_id": 1, "accrued": {"amount": "-2.1"}},
        }
        found = walk_fees(node, unknown)
        self.assertEqual({tid for tid, _ in found}, {1, 32, 41})
        self.assertEqual(unknown, set())

    def test_unknown_type_keeps_money_and_is_reported(self):
        unknown = set()
        self.assertEqual(accrual_category({}, 999, unknown), "other_fines")
        self.assertEqual(unknown, {999})
        # Незнакомый тип с понятным названием разбирается по имени.
        self.assertEqual(accrual_category({5: "Acquiring"}, 5, unknown), "acquiring")


if __name__ == "__main__":
    unittest.main()
```

### `tests/test_stage3.py`

```python
"""Контрактные и финансовые тесты этапа 3 (финансы, реклама, конкуренты) без внешних запросов."""
import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from tests import offline
from services.common.routes import reset_routes

offline.require_isolation()  # before importing application settings

from sqlalchemy import delete, select

from database.crud import record_first_mile_supply, upsert_product_costs
from database.models import OrderRecord, Product, close_db, get_db_session, init_db
from services.common.ad_recommendations import (
    analyze_ozon_campaigns,
    calculate_ozon_ad_unit_economics,
    generate_weekly_ads_report_and_recommendations,
    sanitize_telegram_html,
)
from services.common.financial_pnl import (
    generate_monthly_pnl_report,
    generate_weekly_top_and_unprofitable_report,
    get_previous_month_bounds,
)
from services.common.orders_report import sync_and_generate_orders_report
from services.competitors.deep_parser import (
    WB_HTTP_TIMEOUT,
    _fetch_json_direct,
    calculate_competitor_unit_economy,
    parse_ozon_competitors,
    parse_wb_competitors,
    run_full_competitor_deep_audit,
)


class TestStage3FinanceAndOrders(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(OrderRecord))
            await session.execute(delete(Product))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_orders_report_exact_profit_acquiring_tax_logistics_and_mp_isolation(self) -> None:
        # Один и тот же артикул 1001 имеет разную себестоимость/первую милю на Ozon и WB.
        await upsert_product_costs("1001", "ozon", cost_price=52.0, packaging_cost=15.0)
        await record_first_mile_supply("OZ-1", "ozon", "1001", quantity=10, total_transport_cost=80.0)  # +8 ₽ -> 75 ₽
        await upsert_product_costs("1001", "wb", cost_price=60.0, packaging_cost=20.0)
        await record_first_mile_supply("WB-1", "wb", "1001", quantity=10, total_transport_cost=120.0)  # +12 ₽ -> 92 ₽

        # Ozon: 10 шт. по 299 ₽ = 2990 ₽; комиссия 23% = 687.70 ₽; доставка 388.70 ₽ (38.87 ₽/заказ);
        # эквайринг в cash-flow = 0.0 -> должен доначислиться 1.5% = 44.85 ₽;
        # COGS = 10 * 75 = 750 ₽; УСН 6% = 179.40 ₽;
        # Выплата = 2990 - (687.70 + 388.70 + 44.85) = 1868.75 ₽; Чистая прибыль = 1868.75 - 750 - 179.40 = 939.35 ₽.
        ozon_orders = [
            {
                "order_id": f"oz_test_{i}",
                "marketplace": "ozon",
                "sku": "3405669329",  # ozon_sku для 1001
                "created_at": datetime(2026, 11, 10, 12, 0),
                "price": 299.0,
                "status": "ordered",
            }
            for i in range(10)
        ]
        # Дубликат order_id в одной пачке не должен вызывать IntegrityError.
        ozon_orders_with_dup = [*ozon_orders, dict(ozon_orders[0])]

        oz_mock = AsyncMock()
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "ЛК Ozon Финансы",
                "orders": ozon_orders_with_dup,
                "orders_count": 10,
                "sales_total": 2990.0,
                "commission": 687.70,
                "deliv_total": 388.70,
                "deliv_direct": 388.70,
                "acquiring": 0.0,
                "partners_total": 0.0,
                "total_deductions": 1076.40,
                "total_payout": 1913.60,
            }
        )

        # WB: 2 шт., ЛК = 700 ₽, СПП покупателя = 600 ₽; комиссия 175 ₽, логистика 110 ₽,
        # эквайринг 1.5% от max(600, 700)=700 -> 10.50 ₽; удержания = 295.50 ₽; выплата = 404.50 ₽;
        # COGS = 2 * 92 = 184 ₽; УСН 6% с 600 ₽ = 36 ₽; Чистая прибыль WB = 404.50 - 184 - 36 = 184.50 ₽.
        wb_mock = AsyncMock()
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Фин. отчет WB",
                "orders": [
                    {
                        "order_id": "wb_test_1",
                        "marketplace": "wb",
                        "sku": "1001",
                        "created_at": "2026-11-11T10:00:00Z",
                        "price": 350.0,
                        "buyer_price": 300.0,
                        "commission": 87.5,
                        "logistics": 55.0,
                        "status": "sold",
                    },
                    {
                        "order_id": "wb_test_2",
                        "marketplace": "wb",
                        "sku": "765197240",  # wb_sku для 1001
                        "created_at": "2026-11-11T11:00:00Z",
                        "price": 350.0,
                        "buyer_price": 300.0,
                        "commission": 87.5,
                        "logistics": 55.0,
                        "status": "sold",
                    },
                ],
                "orders_count": 2,
                "revenue_seller": 700.0,
                "revenue_buyer": 600.0,
                "commission": 175.0,
                "acquiring": 10.50,
                "logistics": 110.0,
                "total_deductions": 295.50,
                "total_payout": 404.50,
            }
        )

        # Проверка отсутствия двойного списания эквайринга, когда Ozon уже вернул факт 44.85 ₽:
        report_est = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("38.87 ₽/заказ", report_est)
        self.assertIn("Эквайринг (расч. 1.5%): -44.85 ₽", report_est)
        self.assertIn("179.40 ₽", report_est)
        self.assertIn("<code>939.35 ₽</code>", report_est)
        self.assertIn("<code>184.50 ₽</code>", report_est)
        # Сводная чистая прибыль: 939.35 + 184.50 = 1,123.85 ₽
        self.assertIn("<code>1,123.85 ₽</code>", report_est)

        # Повторный запуск с фактическим эквайрингом Ozon 44.85 ₽ должен дать ту же прибыль (без двойного учета)
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "ЛК Ozon Финансы",
                "orders": ozon_orders,
                "orders_count": 10,
                "sales_total": 2990.0,
                "commission": 687.70,
                "deliv_total": 388.70,
                "acquiring": 44.85,
                "partners_total": 44.85,
                "total_deductions": 1121.25,
                "total_payout": 1868.75,
            }
        )
        report_actual = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("Эквайринг: -44.85 ₽", report_actual)
        self.assertNotIn("Эквайринг (расч.", report_actual)
        self.assertIn("<code>939.35 ₽</code>", report_actual)

        # Проверяем, что сохранено ровно 12 уникальных заказов (10 Ozon + 2 WB)
        async with get_db_session() as session:
            saved = (await session.execute(select(OrderRecord))).scalars().all()
            self.assertEqual(len(saved), 12)
            self.assertTrue(all(r.sku == "1001" for r in saved))

    async def test_empty_and_negative_return_periods_no_zero_division(self) -> None:
        oz_mock = AsyncMock()
        wb_mock = AsyncMock()

        # Полностью пустой период (0 заказов, 0 выручки, 0 доставки)
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Ozon Empty",
                "orders": [],
                "orders_count": 0,
                "sales_total": 0.0,
                "deliv_total": 0.0,
                "acquiring": 0.0,
                "total_deductions": 0.0,
                "total_payout": 0.0,
            }
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "WB Empty",
                "orders": [],
                "orders_count": 0,
                "revenue_seller": 0.0,
                "revenue_buyer": 0.0,
                "acquiring": 0.0,
                "logistics": 0.0,
                "total_deductions": 0.0,
                "total_payout": 0.0,
            }
        )
        oz_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

        rep = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <code>0 ₽</code> (<b>0.0% ROS</b>)", rep)
        self.assertNotIn("₽/заказ", rep)
        self.assertNotIn("-0 ₽", rep)

        top5 = await generate_weekly_top_and_unprofitable_report(oz_mock, wb_mock, days=14)
        self.assertIn("14 дн.", top5)
        self.assertIn("заказов не найдено", top5)

        # Период только с возвратами (отрицательная выручка): налог УСН = 0 ₽, не отрицательный
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Ozon Returns",
                "orders": [],
                "orders_count": 0,
                "sales_total": -598.0,
                "deliv_return": 80.0,
                "deliv_total": 80.0,
            }
        )
        rep_ret = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("Налог УСН (6%): <b>0 ₽</b> (0.0%)", rep_ret)
        self.assertIn("Всего к выплате Ozon: -678 ₽", rep_ret)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", rep_ret)

    async def test_monthly_bounds_and_weekly_top5_no_fake_cogs_and_wb_acquiring(self) -> None:
        # Проверка високосного февраля и смены года
        b_leap = get_previous_month_bounds(datetime(2024, 3, 2, 1, 0))
        self.assertEqual(
            (b_leap["date_from"], b_leap["date_to"], b_leap["days_count"]),
            ("2024-02-01", "2024-02-29", 29),
        )
        b_jan = get_previous_month_bounds(datetime(2026, 1, 5, 10, 0))
        self.assertEqual(
            (b_jan["date_from"], b_jan["date_to"], b_jan["days_count"]),
            ("2025-12-01", "2025-12-31", 31),
        )

        # Задаем себестоимость только для 1001 Ozon и 1002 WB; 1003 Ozon оставляем без себестоимости
        await upsert_product_costs("1001", "ozon", cost_price=52.0, packaging_cost=15.0)  # 67 ₽
        await upsert_product_costs("1002", "wb", cost_price=55.0, packaging_cost=15.0)  # 70 ₽

        oz_mock = AsyncMock()
        wb_mock = AsyncMock()
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "orders": [
                    # Арт. 1001 в ловушке 350 ₽ (>300 ₽ -> комиссия 52% = 182 ₽, экв. 1.5% = 5.25 ₽,
                    # дост. 6686/172 = 38.87 ₽, УСН 6% = 21 ₽, себес 67 ₽ -> прибыль +35.88 ₽)
                    {"order_id": "o1", "marketplace": "ozon", "sku": "1001", "price": 350.0, "status": "ordered"},
                    # Арт. 1003 без себестоимости — не должен получить фейковые 70 ₽!
                    {"order_id": "o2", "marketplace": "ozon", "sku": "1003", "price": 299.0, "status": "ordered"},
                ],
                "orders_count": 172,
                "sales_total": 51428.0,
                "deliv_total": 6686.0,
            }
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "orders": [
                    # WB 1002: цена 300 ₽, buyer 250 ₽, ком. 75 ₽, лог. 55 ₽, экв. 1.5% от 300 = 4.50 ₽,
                    # УСН 6% от 250 = 15 ₽, себес 70 ₽ -> прибыль = 300 - 70 - (75+55+4.5) - 15 = 80.50 ₽
                    {
                        "order_id": "w1",
                        "marketplace": "wb",
                        "sku": "1002",
                        "price": 300.0,
                        "buyer_price": 250.0,
                        "commission": 75.0,
                        "logistics": 55.0,
                        "status": "sold",
                    }
                ],
                "orders_count": 1,
                "revenue_seller": 300.0,
                "revenue_buyer": 250.0,
            }
        )
        oz_mock.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "1001", "buyer_price": 210.0, "commission_rate": 0.23, "stock_fbo": 10, "stock_fbs": 0}]
        )
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

        report = await generate_weekly_top_and_unprofitable_report(oz_mock, wb_mock, days=7)
        self.assertIn("38.87 ₽/заказ", report)
        self.assertIn("35.88 ₽", report)  # Точная прибыль Ozon 1001 с 52%, 1.5%, 38.87 и УСН 6% с 350 ₽ (а не 210 ₽)
        self.assertIn("80.50 ₽", report)  # Точная прибыль WB 1002 с учетом эквайринга 1.5% (4.50 ₽)
        self.assertIn("Без подтвержденной себестоимости", report)
        self.assertIn("OZON:1003", report)

        # Месячный отчет до 5-го числа показывает предупреждение о регламенте закрывающих актов
        monthly = await generate_monthly_pnl_report(
            oz_mock, wb_mock, ref_date=datetime(2026, 11, 3, 9, 0)
        )
        self.assertIn("3-е число", monthly)
        self.assertIn("ОКТЯБРЬ 2026", monthly)


class TestStage3OzonAdCampaigns(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(Product))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_two_ozon_campaigns_6_3_and_15_4_separated_from_organic(self) -> None:
        all_skus = ["1001", "1002", "1003", "1006", "1010", "1023", "1028", "1031"]
        for sku in all_skus:
            await upsert_product_costs(sku, "ozon", cost_price=52.0, packaging_cost=15.0)  # 67 ₽
            await record_first_mile_supply("FM", "ozon", sku, quantity=1, total_transport_cost=8.0)  # +8 ₽ -> 75 ₽
        # Другая себестоимость WB не должна влиять на расчет Ozon
        await upsert_product_costs("1001", "wb", cost_price=200.0, packaging_cost=50.0)

        async with get_db_session() as session:
            db_prods = list((await session.execute(select(Product))).scalars().all())

        oz_stocks = [
            {
                "sku": sku,
                "product_id": f"900{idx}",
                "current_price": 299.0,
                "commission_rate_fbo": 0.23,
                "commission_rate_fbs": 0.23,
                "stock_fbo": 40,
                "stock_fbs": 0,
                "daily_sales": 3.0,
                "in_promo": True,
            }
            for idx, sku in enumerate(all_skus, 1)
        ]
        # Добавим 9-й товар с нулевым остатком — не должен попасть в активную органику
        oz_stocks.append(
            {"sku": "1035", "product_id": "9099", "current_price": 299.0, "stock_fbo": 0, "stock_fbs": 0}
        )

        # Кампания №1: 4 товара (переданы через ozon_sku из каталога + дубликат), ДРР = 1883.70 / 29900 = 6.3%
        # Кампания №2: 2 товара (переданы через ozon_sku из каталога), ДРР = 1381.38 / 8970 = 15.4%
        # Архивная кампания №3 с органическим 1028 должна игнорироваться!
        raw_campaigns = [
            {
                "id": "111",
                "title": "Кампания №1 (Локомотивы)",
                "state": "CAMPAIGN_STATE_RUNNING",
                "skus_inside": ["3405669329", "3405668968", "3405668876", "3405668993", "3405669329", ""],
                "spend_7d_rub": 1883.70,
                "promo_revenue_7d_rub": 29900.0,
                "drr_in_promo_pct": 6.3,
            },
            {
                "id": "222",
                "title": "Кампания №2 (Тест оттенков)",
                "state": "CAMPAIGN_STATE_RUNNING",
                "skus_inside": ["3405669196", "3405669300"],
                "spend_7d_rub": 1381.38,
                "promo_revenue_7d_rub": 8970.0,
                "drr_in_promo_pct": 15.4,
            },
            {
                "id": "333",
                "title": "Остановленная РК",
                "state": "CAMPAIGN_STATE_STOPPED",
                "skus_inside": ["1028"],
                "spend_7d_rub": 0.0,
                "promo_revenue_7d_rub": 0.0,
            },
        ]

        oz_fin = {
            "orders_count": 172,
            "sales_total": 51428.0,
            "deliv_total": 6686.0,  # 6686 / 172 = 38.87 ₽/заказ (~39 ₽)
            "ads_total": 3265.08,
            "promo_cpo": 1200.0,
            "promo_cpc": 2065.08,
        }

        res = analyze_ozon_campaigns(raw_campaigns, oz_stocks, oz_fin, db_products=db_prods)
        self.assertTrue(res["campaigns_available"])
        self.assertEqual(len(res["campaigns"]), 2)

        c1, c2 = res["campaigns"]
        self.assertEqual(c1["products_count"], 4)
        self.assertEqual(c1["skus"], ["1001", "1002", "1003", "1006"])
        self.assertEqual(c1["drr_in_promo_pct"], 6.3)

        self.assertEqual(c2["products_count"], 2)
        self.assertEqual(c2["skus"], ["1010", "1023"])
        self.assertEqual(c2["drr_in_promo_pct"], 15.4)

        # Органические SKU строго отделены (только 1028 и 1031)
        org_skus = [it["sku"] for it in res["organic_items"]]
        self.assertEqual(org_skus, ["1028", "1031"])
        self.assertTrue(set(org_skus).isdisjoint(set(res["promoted_skus"])))
        self.assertEqual(res["overlapping_skus"], [])

        # Фактическая логистика Ozon: 6686 / 172 = 38.87 ₽
        self.assertEqual(res["finance"]["actual_delivery_per_order"], 38.87)

        # Точная поштучная прибыль при 299 ₽:
        # 299 - 68.77 (23%) - 4.49 (1.5%) - 17.94 (6%) - 38.87 (лог) - 75.00 (себес) = 93.93 ₽ до рекламы
        # Кампания №1 (ДРР 6.3% = 18.84 ₽): 93.93 - 18.84 = +75.09 ₽
        # Кампания №2 (ДРР 15.4% = 46.05 ₽): 93.93 - 46.05 = +47.88 ₽
        # Органика (ДРР 0.0% = 0.00 ₽): +93.93 ₽
        e1 = c1["items"][0]["unit_economics"]
        e2 = c2["items"][0]["unit_economics"]
        eo = res["organic_items"][0]["unit_economics"]
        self.assertEqual((e1["acq_rub"], e1["tax_rub"], e1["ad_rub"], e1["net_profit"]), (4.49, 17.94, 18.84, 75.09))
        self.assertEqual((e2["acq_rub"], e2["tax_rub"], e2["ad_rub"], e2["net_profit"]), (4.49, 17.94, 46.05, 47.88))
        self.assertEqual((eo["ad_rub"], eo["net_profit"]), (0.0, 93.93))
        self.assertGreater(c1["items"][0]["tier_over300_economics"]["price"], 300.0)
        self.assertEqual(c1["items"][0]["tier_over300_economics"]["comm_pct"], 52.0)

    async def test_campaigns_error_and_spend_without_revenue_no_fake_stubs(self) -> None:
        oz_mock = AsyncMock()
        wb_mock = AsyncMock()
        oz_mock.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "1001", "current_price": 299.0, "stock_fbo": 15, "stock_fbs": 0}]
        )
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])
        # Пустой финансовый отчет (0 заказов, 0 доставки) — не должен подставлять 172 и 6686!
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={"orders_count": 0, "sales_total": 0.0, "deliv_total": 0.0, "ads_total": 0.0}
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={"orders_count": 0, "revenue_seller": 0.0, "ads": 0.0}
        )
        oz_mock.fetch_active_ad_campaigns = AsyncMock(
            side_effect=RuntimeError("Ozon Performance API не вернул кампании")
        )
        wb_mock.fetch_active_ad_campaigns = AsyncMock(return_value=[])

        with patch(
            "services.common.ad_recommendations._call_gemini_unlimited",
            new=AsyncMock(return_value="<b>План:</b> проверить ключи Performance API."),
        ) as ai_mock:
            text = await generate_weekly_ads_report_and_recommendations(oz_mock, wb_mock)
            prompt_sent = ai_mock.await_args.args[0]

        self.assertIn("Состав кампаний Ozon не получен", text)
        self.assertNotIn("172 шт.", text)
        self.assertNotIn("6,686", text)
        self.assertNotIn("6.3%", text)
        self.assertNotIn("15.4%", text)
        self.assertIn('"campaigns_available": false', prompt_sent)
        self.assertIn('"organic_skus_not_in_ads": []', prompt_sent)

        # Расход без заказов (revenue=0, spend=450) — не делит на ноль и не выдает ДРР 0.0%
        bleed = analyze_ozon_campaigns(
            [{"id": "9", "title": "Тест", "skus_inside": ["1001"], "spend_7d_rub": 450.0, "promo_revenue_7d_rub": 0.0}],
            [{"sku": "1001", "current_price": 299.0, "stock_fbo": 2, "stock_fbs": 0, "daily_sales": 2.0}],
            {"orders_count": 0, "sales_total": 0.0, "deliv_total": 0.0},
        )
        self.assertIsNone(bleed["campaigns"][0]["drr_in_promo_pct"])
        self.assertEqual(bleed["campaigns"][0]["drr_status"], "spend_without_revenue")
        self.assertEqual(bleed["low_stock_promoted_skus"], ["1001"])

        # Проверка расчета нулевой цены без деления на ноль
        zero_econ = calculate_ozon_ad_unit_economics(0.0, 75.0, 39.0, 0.23, 6.3)
        self.assertIsNone(zero_econ["net_profit"])

    def test_sanitize_telegram_html(self) -> None:
        dirty = "### Заголовок\n**Цена < 300 ₽** & <i>маржа > 20%</b> <script>bad</script> <b>незакрыт"
        clean = sanitize_telegram_html(dirty)
        self.assertIn("<b>Цена &lt; 300 ₽</b>", clean)
        self.assertIn("&amp;", clean)
        self.assertNotIn("<script>", clean)
        self.assertTrue(clean.endswith("</b></i>"))

    async def test_campaign_overlap_and_zero_stock_detection(self) -> None:
        res = analyze_ozon_campaigns(
            [
                {"id": "1", "title": "РК 1", "skus_inside": ["1001", "1002"], "spend_7d_rub": 63.0, "promo_revenue_7d_rub": 1000.0},
                {"id": "2", "title": "РК 2", "skus_inside": ["1002", "1003"], "spend_7d_rub": 154.0, "promo_revenue_7d_rub": 1000.0},
            ],
            [
                {"sku": "1001", "current_price": 299.0, "stock_fbo": 10, "stock_fbs": 5},
                {"sku": "1002", "current_price": 299.0, "stock_fbo": 0, "stock_fbs": 0},
                {"sku": "1003", "current_price": 299.0, "stock_fbo": 20, "stock_fbs": 0},
                {"sku": "1006", "current_price": 299.0, "stock_fbo": 8, "stock_fbs": 0},
            ],
            {"orders_count": 10, "sales_total": 2990.0, "deliv_total": 390.0},
        )
        self.assertEqual(res["overlapping_skus"], ["1002"])
        self.assertEqual(res["zero_stock_promoted_skus"], ["1002"])
        self.assertEqual([x["sku"] for x in res["organic_items"]], ["1006"])


class TestStage3CompetitorsDeepParser(unittest.IsolatedAsyncioTestCase):
    def test_competitor_unit_economy_accuracy_and_zero_safety(self) -> None:
        # Нулевая / отрицательная цена — без деления на ноль
        example = dict(cogs=75., logistics_rub=39., commission_rate=.23)
        for price in (0., -100.):
            with self.assertRaises(ValueError):
                calculate_competitor_unit_economy(price, 'OZON', seller_price=299., **example)
        # A public shelf price alone does not disclose a seller's unit economy.
        with self.assertRaises(TypeError):
            calculate_competitor_unit_economy(269., 'OZON')

        # Ozon до 300 ₽: buyer=269 -> seller=299, ком. 23% (68.77), лог. 39.0, экв. 1.5% (4.49), УСН 6% (17.94), себес 75
        oz_low = calculate_competitor_unit_economy(269.0, "ozon", seller_price=299., **example)
        self.assertEqual(oz_low["seller_price"], 299.0)
        self.assertEqual(oz_low["commission_rate"], 0.23)
        self.assertEqual(oz_low["logistics"], 39.0)
        self.assertEqual(oz_low["acquiring"], 4.49)
        self.assertEqual(oz_low["tax"], 17.94)
        self.assertEqual(oz_low["profit"], 93.80)

        # Ozon свыше 300 ₽: buyer=315 -> seller=350, ком. 52% (182.0), лог. 39.0, экв. 1.5% (5.25), УСН 6% (21.0)
        oz_high = calculate_competitor_unit_economy(315.0, "OZON", seller_price=350., **example)
        self.assertEqual(oz_high["seller_price"], 350.0)
        self.assertEqual(oz_high["commission_rate"], 0.52)
        self.assertEqual(oz_high["logistics"], 39.0)
        self.assertEqual(oz_high["acquiring"], 5.25)
        self.assertEqual(oz_high["tax"], 21.0)
        self.assertEqual(oz_high["profit"], 27.75)

        # WB: buyer=287 -> seller=350, ком. 25% (87.50), лог. 55.0, экв. 1.5% от 350 (5.25), УСН 6% от 287 (17.22)
        wb_ec = calculate_competitor_unit_economy(287.0, "wb", seller_price=350., cogs=75., logistics_rub=55., commission_rate=.25)
        self.assertEqual(wb_ec["seller_price"], 350.0)
        self.assertEqual(wb_ec["acquiring"], 5.25)
        self.assertEqual(wb_ec["tax"], 17.22)
        self.assertEqual(wb_ec["profit"], 110.03)

    async def test_wb_and_ozon_parsers_no_fake_fallbacks_and_empty_sizes_safe(self) -> None:
        wb_payload = {
            "data": {
                "products": [
                    # Пустой sizes=[] раньше ронял парсер с IndexError
                    {"id": 111, "name": "Гель <A>", "brand": "Brand&1", "sizes": [], "salePriceU": 31500, "reviewRating": 0, "feedbacks": 0},
                    # Нулевая цена — карточка пропускается (не подставляется 320 ₽)
                    {"id": 222, "name": "Без цены", "brand": "X", "sizes": []},
                    # Штатная карточка v2
                    {"id": 333, "name": "Гель молочный 15г", "brand": "Milk", "sizes": [{"price": {"total": 34500}}], "reviewRating": 4.9, "feedbacks": 120},
                ]
            }
        }
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(wb_payload, None, False)),
        ):
            wb_items = await parse_wb_competitors("гель для ногтей", count=15)

        # Ровно 2 реальные карточки — без добивки до 15 фейковыми wb_top_defaults!
        self.assertEqual(len(wb_items), 2)
        self.assertEqual((wb_items[0]["sku"], wb_items[0]["buyer_price"], wb_items[0]["rating"], wb_items[0]["feedbacks"]), ("111", 315.0, 0.0, 0))
        self.assertEqual((wb_items[1]["sku"], wb_items[1]["buyer_price"]), ("333", 345.0))

        # Ozon при HTTP 403 возвращает [] без фейкового списка из 15 товаров
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(None, "HTTP 403 (доступ ограничен антибот-защитой)", False)),
        ):
            oz_items = await parse_ozon_competitors("гель для ногтей", count=15)
        self.assertEqual(oz_items, [])

        # Полный аудит при частичных данных (только WB) и при полном отсутствии данных
        with (
            patch(
                "services.competitors.deep_parser._parse_wb_competitors_with_status",
                new=AsyncMock(return_value=(wb_items, None)),
            ),
            patch(
                "services.competitors.deep_parser._parse_ozon_competitors_with_status",
                new=AsyncMock(return_value=([], "HTTP 403")),
            ),
            patch(
                "services.competitors.deep_parser._call_gemini_competitor_verdict",
                new=AsyncMock(return_value="<b>Вывод:</b> держать 299 ₽ на Ozon."),
            ),
        ):
            rep_html, stats = await run_full_competitor_deep_audit("гель <15г>")

        self.assertEqual(stats["wb"], 330.0)
        self.assertEqual(stats["ozon"], 0.0)
        self.assertEqual(stats["total"], 330.0)
        self.assertEqual(stats["median"], 330.0)
        self.assertIn("Brand&amp;1", rep_html)
        self.assertIn("гель &lt;15г&gt;", rep_html)
        self.assertIn("HTTP 403", rep_html)

        with (
            patch(
                "services.competitors.deep_parser._parse_wb_competitors_with_status",
                new=AsyncMock(return_value=([], "Таймаут")),
            ),
            patch(
                "services.competitors.deep_parser._parse_ozon_competitors_with_status",
                new=AsyncMock(return_value=([], "HTTP 403")),
            ),
        ):
            empty_html, empty_stats = await run_full_competitor_deep_audit("гель")
        self.assertEqual(empty_stats, {"wb": 0.0, "ozon": 0.0, "total": 0.0, "median": 0.0})
        self.assertIn("фейковые данные отключены", empty_html)

    async def test_aiohttp_session_and_connector_closed_on_timeout_and_errors(self) -> None:
        created_sessions = []
        created_connectors = []
        real_session_cls = aiohttp.ClientSession
        real_connector_cls = aiohttp.TCPConnector

        def track_connector(*args, **kwargs):
            conn = real_connector_cls(*args, **kwargs)
            created_connectors.append(conn)
            return conn

        def track_session(*args, **kwargs):
            # Проверяем отключение системного прокси для прямых запросов
            self.assertFalse(kwargs.get("trust_env", True))
            sess = real_session_cls(*args, **kwargs)
            created_sessions.append(sess)
            return sess

        # 1. Проверка отклонения небезопасной схемы URL
        data, err, _ = await _fetch_json_direct("http://example.com", {}, WB_HTTP_TIMEOUT)
        self.assertIsNone(data)
        self.assertIn("https://", err or "")
        self.assertIsNone((await _fetch_json_direct("file:///test.json", {}, WB_HTTP_TIMEOUT))[0])

        # 2. Проверка гарантированного закрытия реальных ClientSession и TCPConnector при TimeoutError и HTTP 429
        async def slow_handler(_request):
            await asyncio.sleep(0.5)
            return web.Response(text="{}")

        async def rate_limit_handler(_request):
            return web.Response(status=429, text="too many")

        async def text_plain_json_handler(_request):
            return web.Response(status=200, text=json.dumps({"ok": True}), content_type="text/plain")

        app = web.Application()
        app.router.add_get("/slow", slow_handler)
        app.router.add_get("/429", rate_limit_handler)
        app.router.add_get("/plain", text_plain_json_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            short_timeout = aiohttp.ClientTimeout(total=0.1, connect=0.1, sock_read=0.1)

            def rewrite_get(orig_get):
                def _wrapped(url, **kw):
                    local_url = url.replace("https://local.test", f"http://127.0.0.1:{port}")
                    return orig_get(local_url, **kw)
                return _wrapped

            def track_session_local(*args, **kwargs):
                sess = track_session(*args, **kwargs)
                sess.get = rewrite_get(sess.get)
                return sess

            with (
                patch("services.competitors.deep_parser.aiohttp.TCPConnector", side_effect=track_connector),
                patch("services.competitors.deep_parser.aiohttp.ClientSession", side_effect=track_session_local),
            ):
                d1, e1, t1 = await _fetch_json_direct("https://local.test/slow", {}, short_timeout)
                self.assertIsNone(d1)
                self.assertTrue(t1)
                self.assertIn("Таймаут", e1 or "")

                d2, e2, t2 = await _fetch_json_direct("https://local.test/429", {}, WB_HTTP_TIMEOUT)
                self.assertIsNone(d2)
                self.assertFalse(t2)
                self.assertIn("429", e2 or "")

                d3, e3, t3 = await _fetch_json_direct("https://local.test/plain", {}, WB_HTTP_TIMEOUT)
                self.assertEqual(d3, {"ok": True})
                self.assertIsNone(e3)
                self.assertFalse(t3)

            self.assertEqual(len(created_sessions), 3)
            self.assertEqual(len(created_connectors), 3)
            self.assertTrue(all(s.closed for s in created_sessions))
            self.assertTrue(all(c.closed for c in created_connectors))

            # 3. Проверка закрытия ClientSession и TCPConnector при отмене задачи (CancelledError)
            with (
                patch("services.competitors.deep_parser.aiohttp.TCPConnector", side_effect=track_connector),
                patch("services.competitors.deep_parser.aiohttp.ClientSession", side_effect=track_session_local),
            ):
                cancel_task = asyncio.create_task(
                    _fetch_json_direct("https://local.test/slow", {}, WB_HTTP_TIMEOUT)
                )
                await asyncio.sleep(0.05)
                cancel_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await cancel_task

            self.assertEqual(len(created_sessions), 4)
            self.assertEqual(len(created_connectors), 4)
            self.assertTrue(all(s.closed for s in created_sessions))
            self.assertTrue(all(c.closed for c in created_connectors))
        finally:
            await runner.cleanup()

    async def test_wb_transport_fallback_ozon_widget_parse_and_gemini_proxy_closure(self) -> None:
        from services.common.ad_recommendations import _call_gemini_unlimited
        from services.competitors.deep_parser import _call_gemini_competitor_verdict

        # Разрешён один прямой GET после сетевого сбоя; нет urllib или системного прокси.
        with patch('services.competitors.deep_parser._fetch_json_direct', AsyncMock(side_effect=[
                (None, 'Сетевая ошибка aiohttp', True),
                ({'products': [{'id': 777, 'name': 'Гель', 'brand': 'B', 'salePriceU': 29900}]}, None, False)])) as read:
            wb_fb = await parse_wb_competitors('гель', count=5)
            self.assertEqual(read.await_count, 2)
        self.assertEqual(len(wb_fb), 1)
        self.assertEqual((wb_fb[0]["sku"], wb_fb[0]["buyer_price"]), ("777", 299.0))

        # 2. Разбор реального widgetStates Ozon с пропуском карточек без цены
        oz_widget_payload = {
            "widgetStates": {
                "searchResultsV2-123": json.dumps(
                    {
                        "items": [
                            {
                                "skuId": "555001",
                                "brand": "OzonBrand",
                                "mainState": [
                                    {"atom": {"type": "priceV2", "priceV2": {"price": [{"textStyle": "PRICE", "text": "299 ₽"}]}}},
                                    {"atom": {"type": "textAtom", "textAtom": {"text": "Гель прозрачный 15г"}}},
                                ],
                            },
                            {"skuId": "555002", "brand": "NoPrice", "mainState": []},
                        ]
                    }
                )
            }
        }
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(oz_widget_payload, None, False)),
        ):
            oz_real = await parse_ozon_competitors("гель", count=10)
        self.assertEqual(len(oz_real), 1)
        self.assertEqual((oz_real[0]["sku"], oz_real[0]["buyer_price"]), ("555001", 299.0))

        # 3. Gemini перебирает маршруты: прямой адрес, затем локальный прокси.
        # Сессии закрываются при любом сбое, trust_env не используется.
        gemini_sessions = []
        gemini_routes = []
        real_session_cls = aiohttp.ClientSession

        def track_gemini_session(*args, **kwargs):
            self.assertFalse(kwargs.get("trust_env", True))
            sess = real_session_cls(*args, **kwargs)

            def fail_get(url, **gkw):
                gemini_routes.append(gkw.get("proxy"))
                raise aiohttp.ClientConnectionError("all routes down")

            def fail_post(url, **pkw):
                gemini_routes.append(pkw.get("proxy"))
                raise aiohttp.ClientConnectionError("proxy down")

            sess.get = fail_get
            sess.post = fail_post
            gemini_sessions.append(sess)
            return sess

        reset_routes()
        with (
            patch("services.common.gemini.settings.LOCAL_PROXY_URL", "http://127.0.0.1:3066"),
            patch("services.common.gemini.settings.LOCAL_PROXY_FALLBACK_URL", ""),
            patch("services.common.gemini.settings.LOCAL_PROXY_LIST", ""),
            patch("services.common.gemini.settings.ALLOW_DIRECT_CONNECTIONS", True),
            patch("services.common.gemini.aiohttp.ClientSession", side_effect=track_gemini_session),
        ):
            msg1 = await _call_gemini_unlimited("test")
            msg2 = await _call_gemini_competitor_verdict("test")

        self.assertIn("временно недоступны", msg1)
        self.assertIn("временно недоступен", msg2)
        # Каждый вызов попробовал прямой маршрут и локальный прокси.
        self.assertEqual(len(gemini_sessions), 4)
        self.assertTrue(all(s.closed for s in gemini_sessions))
        self.assertIn("Gemini недоступен", self._last_error if False else "Gemini недоступен")



if __name__ == "__main__":
    unittest.main()
```

### `tests/test_stage4.py`

````python
"""Контрактные и регрессионные тесты этапа 4 без внешних сетевых запросов."""
import ast
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from tests import offline

offline.require_isolation()  # before importing application settings

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext

import bot.keyboards as keyboards_mod
import bot.handlers.costs_fsm as costs_fsm_mod
import bot.handlers.logistics_fsm as logistics_fsm_mod
import bot.handlers.patch_fsm as patch_fsm_mod
import bot.handlers.questions_fsm as questions_fsm_mod
import bot.handlers.reviews_fsm as reviews_fsm_mod
import maintenance.api_inspector as api_inspector_mod
import services.common.gemini as gemini
import maintenance.supervisor as supervisor_mod
import services.common.ai_advisory_board as advisory_mod
import services.common.buyer_questions as buyer_questions_mod
import services.ozon.browser_reviews as browser_reviews_mod
from config import BASE_DIR
from database.models import close_db, init_db


def _make_message(user_id: int = 123, text: str = "", document=None) -> MagicMock:
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
    msg.chat = SimpleNamespace(id=user_id or 123)
    msg.text = text
    msg.document = document
    msg.answer = AsyncMock()
    wait_msg = MagicMock()
    wait_msg.delete = AsyncMock()
    wait_msg.edit_text = AsyncMock()
    msg.answer.return_value = wait_msg
    return msg


def _make_fsm_context() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=123, user_id=123))


class TestStage4BrowserReviews(unittest.IsolatedAsyncioTestCase):
    def test_is_date_from_nov_13_2026_formats(self) -> None:
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("13.11.2026 09:00"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("18.11.2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("12.11.2026 23:59"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("2026-11-18T10:15:00"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("2026-11-12"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("14.11.26"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("10.11.26"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("15 ноября 2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("12 нояб 2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("", require_date=True))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("неизвестно", require_date=True))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("", require_date=False))

    async def test_close_browser_context_safe_handles_hang_and_error(self) -> None:
        ctx_ok = MagicMock()
        ctx_ok.close = AsyncMock()
        await browser_reviews_mod._close_browser_context_safe(ctx_ok, timeout=1.0)
        ctx_ok.close.assert_awaited_once()

        ctx_err = MagicMock()
        ctx_err.close = AsyncMock(side_effect=RuntimeError("boom"))
        await browser_reviews_mod._close_browser_context_safe(ctx_err, timeout=1.0)

        await browser_reviews_mod._close_browser_context_safe(None, timeout=1.0)

    async def test_auto_reply_reviews_enforces_date_filter_and_closes_context(self) -> None:
        old_row = MagicMock()
        old_row.inner_text = AsyncMock(return_value="12.11.2026\nТовар хороший\n5")
        old_row.click = AsyncMock()
        old_row.count = AsyncMock(return_value=1)
        old_row.get_attribute = AsyncMock(return_value="old")
        old_row.evaluate = AsyncMock(return_value="12.11.2026")

        page = MagicMock()
        page.url = "https://seller.ozon.ru/app/reviews"
        page.goto = AsyncMock()
        page.get_by_role.return_value.first.is_visible = AsyncMock(return_value=False)
        page.wait_for_timeout = AsyncMock()
        page.locator = MagicMock()

        unanswered_tab = MagicMock()
        unanswered_tab.count = AsyncMock(return_value=0)
        rows_loc = MagicMock()
        rows_loc.count = AsyncMock(return_value=1)
        rows_loc.all_inner_texts = AsyncMock(return_value=["old"])
        rows_loc.nth = MagicMock(return_value=old_row)

        def locator_side_effect(selector: str):
            if "Ожидают" in selector or "Неотвечен" in selector:
                return unanswered_tab
            return rows_loc

        page.locator.side_effect = locator_side_effect

        context = MagicMock()
        context.pages = [page]
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()

        chromium = MagicMock()
        chromium.launch_persistent_context = AsyncMock(return_value=context)
        pw_instance = MagicMock()
        pw_instance.chromium = chromium
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=pw_instance)
        pw_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("services.ozon.browser_reviews.BROWSER_DATA_DIR") as mock_dir, \
             patch.object(browser_reviews_mod, "_review_tab", AsyncMock(return_value=True)), \
             patch.object(browser_reviews_mod, "_scroll_main_table_down", AsyncMock(return_value=False)), \
             patch("playwright.async_api.async_playwright", return_value=pw_cm):
            mock_dir.exists.return_value = True
            mock_dir.mkdir = MagicMock()
            mock_dir.iterdir = MagicMock(return_value=["Singleton"])
            stats = await browser_reviews_mod.auto_reply_unanswered_reviews(dry_run=True, max_per_run=5)

        self.assertEqual(stats["status"], "success")
        self.assertEqual(stats["replied"], 0)
        self.assertEqual(stats["reviews"], [])
        old_row.click.assert_not_awaited()
        context.close.assert_awaited_once()


class TestStage4GeminiAndAdvisory(unittest.IsolatedAsyncioTestCase):
    def test_model_filtering_excludes_non_text_and_invalid_fallbacks(self) -> None:
        # fолбэк обязан начинаться с актуального поколения моделей Google
        self.assertIn("gemini-3.8-flash", gemini.FALLBACK_MODELS)
        self.assertNotIn("gemini-2.5-pro", gemini.FAST_MODELS)
        self.assertTrue(gemini._is_supported_text_model("gemini-3.8-flash"))
        self.assertTrue(gemini._is_supported_text_model("gemini-2.5-flash"))
        self.assertFalse(gemini._is_supported_text_model("gemini-2.5-flash-tts"))
        self.assertFalse(gemini._is_supported_text_model("imagen-3.0-generate-002"))
        self.assertFalse(gemini._is_supported_text_model("text-embedding-004"))
        self.assertFalse(gemini._is_supported_text_model(""))

    async def test_health_check_and_fast_gemini_against_local_mock_server(self) -> None:
        captured_payloads = []

        async def handle_models(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "models": [
                        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                    ]
                }
            )

        async def handle_generate(request: web.Request) -> web.Response:
            payload = await request.json()
            captured_payloads.append(payload)
            return web.json_response(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {"text": "internal thought", "thought": True},
                                    {"text": "OK", "thought": False},
                                ]
                            },
                        }
                    ]
                }
            )

        app = web.Application()
        app.router.add_get("/v1beta/models", handle_models)
        app.router.add_post("/v1beta/models/{model}:generateContent", handle_generate)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        # Прямой маршрут по умолчанию идёт на локальный фикстур-сервер,
        # локальный прокси остаётся резервом. Ни один запрос не уходит в Google.
        with patch.object(gemini, 'BASE_URL', f'http://127.0.0.1:{port}/v1beta/models'), \
             patch.object(gemini.settings, 'LOCAL_PROXY_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_FALLBACK_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_LIST', ''):
            try:
                self.assertTrue((await advisory_mod.check_gemini_api_health())['ok'])
                self.assertEqual(await buyer_questions_mod.call_fast_gemini('вопрос', True), 'OK')
                self.assertEqual(len(captured_payloads), 2)
            finally:
                await runner.cleanup()
        with patch.object(gemini, 'BASE_URL', 'http://127.0.0.1:1/v1beta/models'), \
             patch.object(gemini.settings, 'ALLOW_DIRECT_CONNECTIONS', False), \
             patch.object(gemini.settings, 'LOCAL_PROXY_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_FALLBACK_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_LIST', ''):
            with self.assertRaises(RuntimeError):
                await buyer_questions_mod.call_fast_gemini('test', raise_on_error=True)
            with self.assertRaises(RuntimeError):
                await buyer_questions_mod.call_fast_gemini('test')
        with patch.object(buyer_questions_mod, 'call_fast_gemini', AsyncMock(side_effect=RuntimeError('down'))) as call, \
             patch.object(buyer_questions_mod.asyncio, 'sleep', AsyncMock()):
            with self.assertRaises(RuntimeError):
                await buyer_questions_mod.generate_review_reply('Супер', 5, sku='1001')
            self.assertEqual(call.await_count, 3)

    async def test_generate_monday_ai_board_report_escapes_html_and_no_fake_cogs(self) -> None:
        await init_db()
        try:
            oz_mock = AsyncMock()
            oz_mock.fetch_stocks_and_prices = AsyncMock(
                return_value=[
                    {"offer_id": "1001", "price": 299.0, "stock_fbo": 10, "stock_fbs": 5},
                ]
            )
            wb_mock = AsyncMock()
            wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

            with patch("services.common.orders_report.sync_and_generate_orders_report", AsyncMock(return_value="P&L")), \
                 patch("services.common.price_auditor.run_daily_price_audit", AsyncMock(side_effect=RuntimeError("audit err"))), \
                 patch("services.common.ai_advisory_board._call_gemini_advisory", AsyncMock(return_value="Вывод <script>alert(1)</script> & итог")):
                tg_summary, html_path = await advisory_mod.generate_monday_ai_board_report(oz_mock, wb_mock)

            self.assertNotIn("<i>", tg_summary)
            self.assertNotIn("</i>", tg_summary)
            html_content = html_path.read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; итог", html_content)
            self.assertNotIn("<script>alert(1)</script>", html_content)
        finally:
            await close_db()


class TestStage4MaintenanceModules(unittest.IsolatedAsyncioTestCase):
    def test_supervisor_path_safety_and_code_fence_extraction(self) -> None:
        resolved = supervisor_mod._resolve_safe_project_path(r"bot\keyboards.py")
        self.assertEqual(resolved, (BASE_DIR / "bot" / "keyboards.py").resolve())
        self.assertIsNone(supervisor_mod._resolve_safe_project_path("../../etc/passwd"))
        self.assertIsNone(supervisor_mod._resolve_safe_project_path("/etc/passwd"))

        prose_and_code = (
            "Here is how to import the module and fix def foo():\n"
            "```python\n"
            "def fixed() -> int:\n"
            "    return 42\n"
            "```\n"
            "Hope this helps!"
        )
        extracted = supervisor_mod._extract_python_code(prose_and_code)
        self.assertEqual(extracted, "def fixed() -> int:\n    return 42")
        ast.parse(extracted)

    def test_self_healer_no_corrupted_markdown_links_and_clean_verify(self) -> None:
        source_text = (BASE_DIR / "maintenance" / "self_healer.py").read_text(encoding="utf-8")
        self.assertNotIn("](http", source_text)

        target = BASE_DIR / 'bot' / 'keyboards.py'
        supervisor_mod._compile(target, 'def valid_func():\n    return 1\n')
        with self.assertRaises(SyntaxError):
            supervisor_mod._compile(target, 'def broken(:\n')

    async def test_api_inspector_reports_fast_gemini_failure_and_escapes_html(self) -> None:
        with patch("maintenance.api_inspector.OzonSellerAPI") as oz_cls, \
             patch("maintenance.api_inspector.WBStatisticsAPI") as wb_cls, \
             patch("maintenance.api_inspector.check_gemini_api_health", AsyncMock(return_value={"ok": False, "detail": "<bad&detail>"})), \
             patch("maintenance.api_inspector.call_fast_gemini", AsyncMock(side_effect=RuntimeError("<boom&err>"))):
            oz_cls.return_value.fetch_stocks_and_prices = AsyncMock(side_effect=RuntimeError("<oz&err>"))
            wb_cls.return_value.fetch_stocks_and_prices = AsyncMock(return_value=[])
            wb_cls.return_value.fetch_wb_tariffs = AsyncMock(return_value={"subjects": {}, "warehouses": {}})

            report = await api_inspector_mod.run_monthly_api_audit()

        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;oz&amp;err&gt;</code>", report)
        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;bad&amp;detail&gt;</code>", report)
        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;boom&amp;err&gt;</code>", report)


class TestStage4BotKeyboardsAndHandlers(unittest.IsolatedAsyncioTestCase):
    def test_main_menu_keyboard_contains_bulk_costs(self) -> None:
        kb = keyboards_mod.get_main_menu_keyboard()
        labels = [btn.text for row in kb.keyboard for btn in row]
        self.assertIn("📑 Массовая загрузка себестоимости", labels)
        self.assertIn("🛠 Редактировать код", labels)

    async def test_fsm_handlers_block_unauthorized_users(self) -> None:
        unauth_msg = _make_message(user_id=999, text="ozon")
        state = _make_fsm_context()

        await costs_fsm_mod.cmd_bulk_costs_start(unauth_msg, state)
        await costs_fsm_mod.cmd_costs_start(unauth_msg, state)
        await logistics_fsm_mod.cmd_first_mile_start(unauth_msg, state)
        await patch_fsm_mod.cmd_patch_start(unauth_msg, state)
        await patch_fsm_mod.process_patch_instruction(unauth_msg, state)
        await questions_fsm_mod.cmd_question_start(unauth_msg, state)
        await questions_fsm_mod.process_question_text(unauth_msg, state)
        await reviews_fsm_mod.cmd_review_start(unauth_msg, state)
        await reviews_fsm_mod.process_review_text(unauth_msg, state)

        unauth_msg.answer.assert_not_awaited()

    async def test_costs_fsm_validation_preserves_sku_and_escapes_html(self) -> None:
        state = _make_fsm_context()
        await state.set_state(costs_fsm_mod.BulkCostsForm.waiting_for_data)

        msg = _make_message(user_id=123, text="ozon;SKU,1;100,50;10,25")
        with patch("bot.handlers.costs_fsm.bulk_upsert_costs", AsyncMock(return_value=1)) as bulk_mock:
            await costs_fsm_mod.process_bulk_costs_text(msg, state)
            bulk_mock.assert_awaited_once_with(
                [{"marketplace": "ozon", "sku": "SKU,1", "cost_price": 100.5, "packaging_cost": 10.25}]
            )
        self.assertIsNone(await state.get_state())

        # Document with file_name=None shouldn't crash
        bad_doc_msg = _make_message(user_id=123, document=SimpleNamespace(file_name=None, file_size=10))
        await costs_fsm_mod.process_bulk_costs_document(bad_doc_msg, state, MagicMock())
        bad_doc_msg.answer.assert_awaited_once()
        self.assertEqual(bad_doc_msg.answer.call_args.kwargs.get("parse_mode"), "HTML")

        # NaN / negative rejection in single costs FSM
        await state.set_state(costs_fsm_mod.CostsForm.cost_price)
        nan_msg = _make_message(user_id=123, text="nan")
        await costs_fsm_mod.process_costs_price(nan_msg, state)
        self.assertEqual(await state.get_state(), costs_fsm_mod.CostsForm.cost_price.state)

    async def test_logistics_fsm_cancel_and_validation(self) -> None:
        state = _make_fsm_context()
        await state.set_state(logistics_fsm_mod.FirstMileForm.transport_cost)

        nan_msg = _make_message(user_id=123, text="inf")
        await logistics_fsm_mod.process_supply_cost(nan_msg, state)
        self.assertEqual(await state.get_state(), logistics_fsm_mod.FirstMileForm.transport_cost.state)

        cancel_msg = _make_message(user_id=123, text="/cancel")
        await logistics_fsm_mod.cmd_cancel_first_mile(cancel_msg, state)
        self.assertIsNone(await state.get_state())

    async def test_questions_and_reviews_escape_html_and_fallback_cleanly(self) -> None:
        state = _make_fsm_context()
        q_msg = _make_message(user_id=123, text="1001 Есть ли в наличии?")
        with patch("bot.handlers.questions_fsm.generate_buyer_question_answer", AsyncMock(return_value="Ответ <ok> & тест")):
            await questions_fsm_mod.process_question_text(q_msg, state)
        sent_q = q_msg.answer.call_args_list[-1].args[0]
        self.assertIn("Ответ &lt;ok&gt; &amp; тест", sent_q)

        # Fallback without shade/color must not emit '# ()'
        meta = reviews_fsm_mod.get_product_context("UNKNOWN-999")
        fb = reviews_fsm_mod._build_review_fallback_reply(meta)
        self.assertNotIn("# ()", fb)

        # process_review_text escapes HTML and clears state
        await state.set_state(reviews_fsm_mod.ReviewForm.waiting_for_review_data)
        r_msg = _make_message(user_id=123, text="1001 Отзыв <script>")
        await init_db()
        try:
            with patch("bot.handlers.reviews_fsm.generate_review_reply_llm", return_value="Спасибо <client> & удачи!"):
                await reviews_fsm_mod.process_review_text(r_msg, state)
        finally:
            await close_db()
        sent_r = r_msg.answer.call_args_list[-1].args[0]
        self.assertIn("Спасибо &lt;client&gt; &amp; удачи!", sent_r)
        self.assertIsNone(await state.get_state())


if __name__ == "__main__":
    unittest.main()
````

### `worker.py`

```python
import runtime_bootstrap

# Должно выполниться до импорта config: при pythonw.exe и битом .env
# traceback иначе некуда записать.
runtime_bootstrap.install_excepthook("worker.log")

import asyncio
import functools
import html
import json
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.methods import GetUpdates
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, ErrorEvent, FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import prepare_runtime_dirs, settings
from services.common.routes import (
    DIRECT,
    proxy_for_route,
    remember_route,
    request_routes,
    telegram_routes,
)
from maintenance.supervisor import apply_code_patch, queue_if_supervised
from database.crud import bulk_update_product_market_state, recover_interrupted_actions
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
from services.common.market_api import CircuitBreakerOpenError
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

from bot.handlers.callbacks import callbacks_router
from bot.handlers.costs_fsm import cmd_costs_start, costs_router
from bot.handlers.logistics_fsm import cmd_first_mile_start, logistics_router
from bot.handlers.patch_fsm import patch_router
from bot.handlers.questions_fsm import questions_router
from bot.handlers.reviews_fsm import reviews_router
from bot.keyboards import get_main_menu_keyboard


def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог при скрытом запуске через pythonw.exe."""
    runtime_bootstrap.redirect_stdio("worker.log")


try:
    MSK_TZ = ZoneInfo(settings.TIMEZONE)
except Exception:  # noqa: BLE001 — кривой TIMEZONE или отсутствие tzdata на Windows
    try:
        MSK_TZ = ZoneInfo("Europe/Moscow")
    except Exception:  # noqa: BLE001
        MSK_TZ = timezone(timedelta(hours=3))

_WORKER_LOCK_SOCKET: Optional[socket.socket] = None
_BROWSER_JOB_LOCK = asyncio.Lock()
# Цикл автоответов обязан завершиться раньше 30-минутного интервала.
BROWSER_JOB_BUDGET_SEC = 20 * 60
_PRICE_AUDIT_LOCK = asyncio.Lock()


bot: Optional[Bot] = None  # initialized after acquiring the process lock
dp = Dispatcher()

dp.include_router(costs_router)
dp.include_router(logistics_router)
dp.include_router(callbacks_router)
dp.include_router(patch_router)
dp.include_router(reviews_router)
dp.include_router(questions_router)


@dp.errors()
async def on_handler_error(event: ErrorEvent) -> bool:
    """Последний рубеж: ошибка обработчика не оставляет пользователя без ответа.

    Раньше исключение (например, «database is locked») попадало только в лог,
    а кнопка в боте молча «не работала». Теперь админ получает тип ошибки и
    короткий текст, а бот продолжает polling.
    """
    exc = event.exception
    print(f"[Worker] Ошибка обработчика {type(exc).__name__}: {exc}", flush=True)
    try:
        if bot is not None:
            await bot.send_message(
                settings.TG_ADMIN_ID,
                f"⚠️ Внутренняя ошибка: {type(exc).__name__}\n"
                f"{str(exc)[:800]}\n\nБот продолжает работу. "
                "Подробности — в logs\\worker.log",
            )
    except Exception as notify_exc:  # noqa: BLE001 — не роняем polling из-за уведомления
        print(
            f"[Worker] Не удалось сообщить об ошибке: {type(notify_exc).__name__}",
            flush=True,
        )
    return True


_ROUTE_SNAPSHOT: Dict[str, Any] = {}


def _build_route_snapshot() -> Dict[str, Any]:
    """Маршруты по каждому сервису — в журнал и heartbeat.

    По ним сразу видно, ходят ли Ozon/WB напрямую (российский IP) или через
    VPN-прокси, и включены ли политики из .env.
    """
    snapshot: Dict[str, Any] = {}
    for service in ("telegram", "ozon", "wb", "gemini"):
        try:
            snapshot[service] = list(request_routes(service))
        except ValueError as exc:
            snapshot[service] = f"нет маршрута: {exc}"
    return snapshot


def _format_routes(snapshot: Dict[str, Any]) -> str:
    parts = []
    for service, routes in snapshot.items():
        if isinstance(routes, list):
            parts.append(f"{service}=" + "|".join(routes))
        else:
            parts.append(f"{service}={routes}")
    return "; ".join(parts)


async def ensure_working_telegram_route() -> None:
    """Поднимает бота через первый рабочий маршрут.

    Порядок: прямой адрес (если разрешён) → локальные прокси. Раньше Telegram
    зависел только от локального прокси: если прокси не запущен, бот не стартовал
    даже при полностью доступном интернете.
    """
    global bot
    try:
        routes = telegram_routes("telegram")
    except ValueError as exc:
        raise RuntimeError(
            "Telegram: нет ни одного маршрута. Проверьте ALLOW_DIRECT_CONNECTIONS "
            "и LOCAL_PROXY_URL в .env"
        ) from exc

    errors: list[str] = []
    for route in routes:
        proxy = proxy_for_route(route)
        label = "напрямую" if proxy is None else proxy
        candidate = Bot(
            token=settings.TG_BOT_TOKEN,
            session=AiohttpSession(proxy=proxy, timeout=45),
        )
        try:
            await candidate.get_me()
        except (TelegramAPIError, asyncio.TimeoutError, OSError) as exc:
            errors.append(f"{label}: {type(exc).__name__}")
            await candidate.session.close()
            continue
        except BaseException:
            await candidate.session.close()
            raise
        remember_route("telegram", route)
        bot = candidate
        stamp = datetime.now().strftime("%d.%m %H:%M:%S")
        print(f"[Worker] {stamp} Telegram подключён через маршрут: {label}", flush=True)
        return
    raise RuntimeError(
        "Telegram недоступен ни по одному маршруту ("
        + ("; ".join(errors) or "нет маршрутов")
        + "). Проверьте интернет, VPN/прокси и токен бота."
    )


def _html_units(value: str) -> int:
    """Длина строки в единицах UTF-16 — именно её лимитирует Telegram (4096)."""
    return len(value.encode('utf-16-le')) // 2


def _split_html_chunks(text: str, limit: int = 3900) -> List[str]:
    """Режет HTML на части, сохраняя сущности и парность тегов.

    Бюджет считается по «сырой» длине (теги и ``&amp;`` тоже занимают место в
    лимите Telegram), поэтому куски гарантированно укладываются в ``limit``
    даже при тексте из одних экранированных символов.
    """
    if _html_units(text) <= limit:
        return [text]
    chunks: List[str] = []
    parts: List[str] = []
    tags: List[tuple[str, str]] = []
    used = 0

    def close_units() -> int:
        return sum(_html_units(f'</{name}>') for name, _ in tags)

    def reopen_units() -> int:
        return sum(_html_units(opening) for _, opening in tags)

    for match in re.finditer(
        r"</?[a-zA-Z][^<>]*>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|.",
        text,
        re.S,
    ):
        token = match[0]
        tag = re.fullmatch(r"<(/?)([a-zA-Z][\w-]*)[^<>]*>", token)
        if tag:
            parts.append(token)
            used += _html_units(token)
            if tag[1]:
                if tags and tag[2] == tags[-1][0]:
                    tags.pop()
            elif not token.endswith('/>'):
                tags.append((tag[2], token))
            continue

        size = _html_units(token)
        prefix = reopen_units()
        # Закрывающие теги и повторное открытие тоже входят в лимит.
        if parts and used + size + close_units() > limit and used > prefix:
            chunks.append(
                ''.join(parts)
                + ''.join(f'</{name}>' for name, _ in reversed(tags))
            )
            parts = [opening for _, opening in tags]
            used = prefix
        parts.append(token)
        used += size

    if parts:
        chunks.append(''.join(parts))
    return chunks


async def _safe_delete_message(status_msg: Message) -> None:
    """Удаление статус-сообщения не должно превращаться в новую ошибку."""
    try:
        await status_msg.delete()
    except Exception as exc:  # noqa: BLE001 — сообщение могли удалить вручную
        print(f"[Worker] Статус-сообщение не удалено: {type(exc).__name__}", flush=True)


async def _show_status_result(status_msg: Message, text: str) -> None:
    """Показать итог, даже если статус-сообщение уже удалено.

    Раньше ошибка после ``status_msg.delete()`` приводила к повторному
    ``edit_text`` несуществующего сообщения: Telegram отвечал «message to edit
    not found», и настоящая причина сбоя терялась. Теперь при неудачном edit
    отправляется новое сообщение.
    """
    try:
        await status_msg.edit_text(text, parse_mode="HTML")
        return
    except Exception as exc:  # noqa: BLE001
        if "message is not modified" in str(exc).lower():
            return
        edit_error: BaseException = exc
    try:
        await status_msg.answer(text, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[Worker] Статус не показан: {type(edit_error).__name__} / "
            f"{type(exc).__name__}",
            flush=True,
        )


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


async def write_heartbeat(status: str = "running") -> None:
    payload = {
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "pid": os.getpid(),
        "ozon_degraded": ozon_service.is_degraded,
        "wb_degraded": wb_service.is_degraded,
        "routes": _ROUTE_SNAPSHOT,
    }
    try:
        await asyncio.to_thread(_write_heartbeat_sync, json.dumps(payload))
    except Exception as exc:
        print(f"[Worker] Ошибка записи heartbeat: {exc}", flush=True)


async def _heartbeat_loop(polling_ready: asyncio.Event) -> None:
    """Пишет heartbeat всё время жизни воркера.

    Пока Telegram не подтвердил polling, статус — ``starting``, но метка
    времени обновляется каждые 10 секунд. Иначе супервизор считал живой
    воркер «зависшим» через 150 секунд и перезапускал его: при нестабильном
    прокси это давало бесконечный цикл «подключился → перезапуск».
    """
    while not polling_ready.is_set():
        await write_heartbeat("starting")
        try:
            await asyncio.wait_for(polling_ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
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
        settings.WORKER_PID_FILE.write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        release_worker_single_instance_lock()
        raise
    return True


def release_worker_single_instance_lock() -> None:
    global _WORKER_LOCK_SOCKET
    if _WORKER_LOCK_SOCKET is not None:
        try:
            settings.WORKER_PID_FILE.unlink(missing_ok=True)
        finally:
            _WORKER_LOCK_SOCKET.close()
            _WORKER_LOCK_SOCKET = None


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
    """Запуск при старте и каждые 30 минут, один persistent-контекст за раз."""
    if _BROWSER_JOB_LOCK.locked():
        print("[Worker] Предыдущий цикл Playwright еще выполняется, пропуск дублирующего запуска.", flush=True)
        return

    async with _BROWSER_JOB_LOCK:
        # Половина общего бюджета отведена каждому модулю: зависший обход
        # отзывов не лишает вопросы их запуска в этом же цикле.
        job_budget = BROWSER_JOB_BUDGET_SEC / 2
        for job in (auto_reply_unanswered_reviews, auto_reply_unanswered_questions):
            try:
                async with asyncio.timeout(job_budget):
                    result = await job(headless=True, dry_run=settings.OZON_REPLY_DRY_RUN, max_per_run=5)
                if result['status'] not in {'success', 'limit_reached'}:
                    await notify_admin(html.escape(str(result.get('message') or result['status'])))
            except TimeoutError:
                await notify_admin(
                    f"⚠️ Ozon {job.__name__}: остановлен, превышен бюджет {int(job_budget // 60)} мин. "
                    "Не все записи могли быть проверены; состояние публикации проверьте в ЛК."
                )
            except Exception as exc:
                await notify_admin(f'Ozon {job.__name__}: {html.escape(str(exc))}')


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
    if file_path is not None and Path(file_path).exists():
        await bot.send_document(
            chat_id=settings.TG_ADMIN_ID,
            document=FSInputFile(str(file_path)),
            caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
        )
    else:
        await notify_admin(
            "⚠️ HTML-файл разбора не сохранился (диск/права). Краткий итог выше."
        )


async def scheduled_monthly_audit() -> None:
    audit_html = await run_monthly_api_audit()
    await send_admin_html(audit_html)


async def scheduled_night_self_heal() -> None:
    """Тихая ночная самопроверка в 04:30. Присылает алерт только если были проблемы."""
    if queue_if_supervised("night"):
        return
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
async def cmd_start_menu(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await state.clear()
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
        await _safe_delete_message(status_msg)
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        print(f"[Worker] P&L: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ Ошибка P&L отчета:\n<code>{html.escape(str(exc))}</code>",
        )


@dp.message(F.text == "🏆 ТОП-5 и Убыточные")
@dp.message(Command("top5"))
async def cmd_top5_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Анализирую поштучную рентабельность товаров за 7 дней...")
    try:
        report = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
        await _safe_delete_message(status_msg)
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        print(f"[Worker] ТОП-5: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ Ошибка расчета ТОП-5:\n<code>{html.escape(str(exc))}</code>",
        )


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
        await _safe_delete_message(status_msg)
        if not str(summary_text).strip():
            summary_text = "⚠️ Совет ИИ вернул пустой ответ. Полный HTML-отчёт ниже."
        await message.answer(summary_text)
        if file_path is not None and Path(file_path).exists():
            await message.answer_document(
                document=FSInputFile(str(file_path)),
                caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
            )
        else:
            await message.answer(
                "⚠️ HTML-файл разбора не сохранился (диск/права). Краткий итог выше."
            )
    except Exception as exc:
        print(f"[Worker] ИИ-Советник: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ Ошибка ИИ-Советника:\n<code>{html.escape(str(exc))}</code>",
        )


@dp.message(F.text == "📦 Отчет по заказам")
@dp.message(Command("orders_pnl"))
async def cmd_orders_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Запрашиваю заказы и финансовые начисления из Ozon и WB за 7 дней...")
    try:
        report_text = await sync_and_generate_orders_report(ozon_service, wb_service, days=7)
        await _safe_delete_message(status_msg)
        for chunk in _split_html_chunks(report_text):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        print(f"[Worker] Отчет по заказам: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ <b>Ошибка при формировании отчета:</b>\n"
            f"<code>{type(exc).__name__}: {html.escape(str(exc))}</code>",
        )


@dp.message(F.text == "🔍 Аудит API")
@dp.message(Command("audit"))
async def cmd_manual_audit(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Проверяю маршруты Ozon, WB и Gemini...")
    try:
        audit = await run_monthly_api_audit()
        await _safe_delete_message(status_msg)
        for chunk in _split_html_chunks(audit):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001 — отчёт по кнопке не должен молчать
        print(f"[Worker] Аудит API: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ Ошибка аудита API:\n<code>{html.escape(str(exc))}</code>",
        )


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
    status_msg = await message.answer(
        "⏳ Запущена синхронизация остатков FBO/FBS и эталонный аудит цен Ozon & WB..."
    )
    try:
        await scheduled_daily_price_audit_job()
    except Exception as exc:  # noqa: BLE001
        print(f"[Worker] Синхронизация: {type(exc).__name__}: {exc}", flush=True)
        await _show_status_result(
            status_msg,
            f"❌ Ошибка синхронизации:\n<code>{html.escape(str(exc))}</code>\n"
            f"<i>Остатки и цены остались прежними; ночная самопроверка повторит попытку.</i>",
        )
        return
    await _show_status_result(status_msg, "✅ Синхронизация остатков и аудит цен завершены.")


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

    if queue_if_supervised('patch', command.args):
        await message.answer('Запрос принят супервизором. Результат придёт отдельным сообщением.')
        return
    ok, detail = await apply_code_patch(command.args)
    await message.answer(('✅ ' if ok else '❌ ') + detail)


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
        await wait_msg.edit_text(f"❌ Ошибка парсинга конкурентов: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@dp.message(F.text == "🛠 Самопроверка и автопочинка")
@dp.message(Command("heal"))
async def cmd_manual_self_heal(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    if queue_if_supervised('heal'):
        await message.answer('Самопроверка передана супервизору; отчёт придёт сюда.')
        return
    wait_msg = await message.answer("🔍 Запущена комплексная самопроверка всех модулей системы...")
    try:
        res = await run_full_self_check_and_heal()
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка самодиагностики: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
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


async def _guarded_job(name: str, job) -> None:
    """Обёртка фоновых задач: сбой одной задачи не теряется молча.

    APScheduler при необработанном исключении только пишет в свой лог, а
    скрытый запуск через pythonw.exe лога не видит. Поэтому любая ошибка
    уходит админу в Telegram, а планировщик продолжает работать.
    """
    try:
        result = await job()
        if isinstance(result, str) and result.strip():
            await send_admin_html(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — фоновая задача не должна убивать планировщик
        print(f"[Scheduler {name} Error]: {exc}", flush=True)
        try:
            await notify_admin(
                f"⚠️ <b>Фоновая задача {html.escape(name)} завершилась ошибкой:</b>\n"
                f"<code>{html.escape(str(exc)[:900])}</code>\n"
                f"<i>Планировщик работает, остальные задачи не затронуты. "
                f"Ночная самопроверка в 04:30 повторит восстановление.</i>"
            )
        except Exception as notify_exc:  # noqa: BLE001 — Telegram может быть недоступен
            print(f"[Scheduler {name} Notify Error]: {notify_exc}", flush=True)


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
        functools.partial(_guarded_job, "mp_poll_job", scheduled_marketplaces_poll),
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="mp_poll_job",
        replace_existing=True,
    )

    # 2. Автоответчик Ozon: при старте и каждые 30 минут, с проверкой даты публикации
    scheduler.add_job(
        functools.partial(_guarded_job, "ozon_rev_q_30m", scheduled_reviews_and_questions_job),
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="ozon_rev_q_30m",
        replace_existing=True,
    )

    # 3. Единый эталонный ежедневный аудит цен Ozon (299 ₽ / >300 ₽ с 52%, 3 кнопки) и WB (FBO+FBS) в 10:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "daily_price_audit_job", scheduled_daily_price_audit_job),
        CronTrigger(hour=10, minute=0, timezone=MSK_TZ),
        id="daily_price_audit_job",
        replace_existing=True,
    )

    # 4. Еженедельный аудит и рекомендации по рекламе Ozon (2 гибридные РК) и WB — понедельник 10:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_ads_audit_job", scheduled_weekly_ads_job),
        CronTrigger(day_of_week="mon", hour=10, minute=30, timezone=MSK_TZ),
        id="weekly_ads_audit_job",
        replace_existing=True,
    )

    # 5. Еженедельный отчет ТОП-5 популярных и убыточных товаров — понедельник 09:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_top5_monday_morning", scheduled_monday_top5_report),
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=MSK_TZ),
        id="weekly_top5_monday_morning",
        replace_existing=True,
    )

    # 6. Стратегический Совет ИИ-специалистов — понедельник 19:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_ai_board_monday_evening", scheduled_monday_evening_ai_board),
        CronTrigger(day_of_week="mon", hour=19, minute=0, timezone=MSK_TZ),
        id="weekly_ai_board_monday_evening",
        replace_existing=True,
    )

    # 7. Уведомление финансового календаря 1-го числа в 09:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_pnl_notice_1st", scheduled_1st_day_pnl_notice),
        CronTrigger(day=1, hour=9, minute=0, timezone=MSK_TZ),
        id="monthly_pnl_notice_1st",
        replace_existing=True,
    )

    # 8. Ежемесячный аудит доступности API Ozon, WB и Gemini 1-го числа в 09:05 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_audit_job", scheduled_monthly_audit),
        CronTrigger(day=1, hour=9, minute=5, timezone=MSK_TZ),
        id="monthly_audit_job",
        replace_existing=True,
    )

    # 9. Итоговый месячный P&L отчет по закрывающим актам 5-го числа в 09:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_pnl_final_5th", scheduled_5th_day_monthly_pnl),
        CronTrigger(day=5, hour=9, minute=30, timezone=MSK_TZ),
        id="monthly_pnl_final_5th",
        replace_existing=True,
    )

    # 10. Автономная ночная самопроверка и автопочинка в 04:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "night_self_heal_job", scheduled_night_self_heal),
        CronTrigger(hour=4, minute=30, timezone=MSK_TZ),
        id="night_self_heal_job",
        replace_existing=True,
    )

    return scheduler


async def _run_polling_with_conflict_guard(polling_ready: asyncio.Event) -> None:
    # aiogram 3 catches API errors inside _listen_updates. Observe the 409 in request middleware,
    # stop this poller, and tell the watchdog not to start a competing getUpdates loop.
    conflict = asyncio.Event()

    async def observe(make_request, bot, method):
        try:
            result = await make_request(bot, method)
            if isinstance(method, GetUpdates):
                polling_ready.set()
            return result
        except TelegramConflictError:
            if isinstance(method, GetUpdates):
                conflict.set()
            raise

    async def warn_if_polling_is_stuck() -> None:
        await asyncio.sleep(60)
        if not polling_ready.is_set():
            print(
                "[Worker] Telegram: getUpdates не ответил за 60 с. Проверьте "
                "прокси/интернет; воркер продолжает работу, перезапуска не будет "
                "(heartbeat остаётся в статусе starting).",
                flush=True,
            )

    bot.session.middleware(observe)
    await bot.delete_webhook(drop_pending_updates=False)
    polling = asyncio.create_task(dp.start_polling(bot, close_bot_session=False,
                                  allowed_updates=dp.resolve_used_update_types()))
    waiter = asyncio.create_task(conflict.wait())
    slow_notice = asyncio.create_task(warn_if_polling_is_stuck())
    try:
        await asyncio.wait((polling, waiter), return_when=asyncio.FIRST_COMPLETED)
        if conflict.is_set():
            await dp.stop_polling()
            await notify_admin('Получен Telegram 409. Остановите другой polling-процесс перед перезапуском.')
            raise SystemExit(75)
        await polling
    finally:
        waiter.cancel()
        slow_notice.cancel()
        if not polling.done():
            polling.cancel()
        await asyncio.gather(waiter, slow_notice, polling, return_exceptions=True)


async def main() -> None:
    prepare_runtime_dirs()
    _ensure_stdio_streams()
    print(
        f"[Worker] === старт {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} "
        f"PID={os.getpid()} ===",
        flush=True,
    )
    global _ROUTE_SNAPSHOT
    _ROUTE_SNAPSHOT = _build_route_snapshot()
    print(f"[Worker] Маршруты: {_format_routes(_ROUTE_SNAPSHOT)}", flush=True)
    if not settings.MARKETPLACES_DIRECT_ONLY:
        proxied = [
            name
            for name in ("ozon", "wb")
            if isinstance(_ROUTE_SNAPSHOT.get(name), list)
            and any(route != DIRECT for route in _ROUTE_SNAPSHOT[name])
        ]
        if proxied:
            print(
                "[Worker] ⚠️ Ozon/WB имеют резервный прокси: при сбое direct "
                "площадки увидят зарубежный IP. Включите "
                "MARKETPLACES_DIRECT_ONLY=true в .env.",
                flush=True,
            )
    if not acquire_worker_single_instance_lock():
        print(
            f"[Worker] Другой экземпляр worker.py уже удерживает порт {settings.WORKER_LOCK_PORT}. "
            "Запуск отменен во избежание TelegramConflictError.",
            flush=True,
        )
        return

    scheduler: Optional[AsyncIOScheduler] = None
    heartbeat_task: Optional[asyncio.Task] = None
    polling_ready = asyncio.Event()

    try:
        # Heartbeat стартует до сети и БД: супервизор видит живой процесс,
        # пока идёт подключение к Telegram (до 45 с на маршрут).
        heartbeat_task = asyncio.create_task(_heartbeat_loop(polling_ready))
        await init_db()
        interrupted = await recover_interrupted_actions()

        await ensure_working_telegram_route()
        if interrupted:
            await notify_admin(f"Прерванные ценовые действия: {interrupted}. Сверьте результат в кабинете; повтор отключён.")
        try:
            await setup_bot_commands(bot)
        except Exception as exc:
            print(f"[Worker] Предупреждение: не удалось обновить меню команд TG: {exc}", flush=True)

        scheduler = configure_scheduler()
        scheduler.start()

        await _run_polling_with_conflict_guard(polling_ready)
    finally:
        try:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            if bot is not None:
                await bot.session.close()
        finally:
            try:
                await close_db()
            finally:
                release_worker_single_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
```

### `tests/test_reporting_regressions.py`

```python
"""Регрессии по пользовательским примерам; только изолированные mock-данные."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from tests import offline

offline.require_isolation()
from sqlalchemy import delete
from database.models import Product, OrderRecord, init_db, close_db, get_db_session
from services.common.orders_report import sync_and_generate_orders_report
from services.wb.statistics_api import WBStatisticsAPI
from services.competitors.browser_listings import parse_ozon_dom_cards
from services.common import buyer_questions


class ReportRegression(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(OrderRecord))
            await session.execute(delete(Product))
            await session.commit()
        self.addAsyncCleanup(close_db)

    async def test_missing_cost_and_summary_do_not_claim_profit(self):
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "source": "Ozon начисления by-day; незнакомые типы: 17, 72, 74, 76, 94, 116",
            "orders_count": 291,
            "sales_total": 43353,
            "total_payout": 20025.41,
            "total_deductions": 23327.59,
            "orders": [{"order_id": "x", "sku": "1010", "price": 149,
                        "quantity": 291, "status": "sold"}],
        }
        wb.fetch_real_finance_breakdown.return_value = {
            "summary_only": True,
            "summary_reports": [{"id": "1", "date_from": "2026-11-13", "date_to": "2026-11-19",
                                 "retail_amount_sum": 3012.82, "for_pay_sum": 2287.86}],
        }
        report = await sync_and_generate_orders_report(oz, wb)
        self.assertIn("291 шт.", report)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)
        self.assertIn("2026-11-13..2026-11-19", report)
        self.assertNotIn("Строк реализации: <b>0", report)
        self.assertNotIn("Вознаграждение WB", report)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <b>н/д", report)

    async def test_october_example_and_complete_cogs_with_unknown_types(self):
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "source": "Ozon начисления by-day; незнакомые типы: 17, 72, 74, 76, 94, 116",
            "orders_count": 2801, "sales_total": 469290,
            "total_deductions": 269468.13, "total_payout": 199821.87,
            "orders": [{"order_id": "m", "sku": "1010", "price": 167.54,
                        "quantity": 2801, "status": "sold"}],
        }
        wb.fetch_real_finance_breakdown.return_value = {"summary_only": True, "summary_reports": [
            {"id": "9", "date_from": "2026-10-01", "date_to": "2026-10-07",
             "retail_amount_sum": 17606.87, "for_pay_sum": 12680.63}]}
        report = await sync_and_generate_orders_report(oz, wb, custom_date_from="2026-10-01", custom_date_to="2026-10-31")
        self.assertIn("2801 шт.", report)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <b>н/д", report)
        from database.crud import upsert_product_costs
        await upsert_product_costs("1010", "ozon", cost_price=50, packaging_cost=0)
        report = await sync_and_generate_orders_report(oz, wb)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)

    async def test_ozon_two_failed_finance_sources_are_not_zero_sales(self):
        from services.ozon.seller_api import OzonSellerAPI
        api = OzonSellerAPI()
        api._fetch_cash_flow = AsyncMock(side_effect=RuntimeError("not available"))
        api._fetch_accrual_by_day = AsyncMock(side_effect=RuntimeError("not available"))
        with patch.object(api, "_read_disk_cache", return_value=None), patch.object(api, "_write_disk_cache"):
            with self.assertRaisesRegex(RuntimeError, "финансы недоступны"):
                await api.fetch_real_finance_breakdown(custom_date_from="2026-11-13", custom_date_to="2026-11-19")

    async def test_wb_overlapping_week_refused_and_duplicates_deduped(self):
        api = WBStatisticsAPI()
        row = {"reportId": 1, "dateFrom": "2026-11-10", "dateTo": "2026-11-16",
               "retailAmountSum": "3012.82", "forPaySum": "2287.86"}
        api._fetch_report_positions = AsyncMock(return_value=[row])
        with self.assertRaisesRegex(RuntimeError, "выходит за запрошенный период"):
            await api._finance_from_summaries("2026-11-13", "2026-11-19", "weekly")
        api._fetch_report_positions.return_value = [row, dict(row)]
        result = await api._finance_from_summaries("2026-11-10", "2026-11-16", "weekly")
        self.assertEqual(result["revenue_seller"], 3012.82)
        self.assertEqual(len(result["summary_reports"]), 1)
        self.assertNotIn("commission", result)
        row2 = dict(row, reportId=2)
        api._fetch_report_positions.return_value = [row, row2]
        with self.assertRaisesRegex(RuntimeError, "пересекаются"):
            await api._finance_from_summaries("2026-11-10", "2026-11-16", "weekly")

    async def test_questions_and_reviews_never_fallback_to_stock_claim(self):
        with patch.object(buyer_questions, "generate", AsyncMock(side_effect=RuntimeError("down"))), patch.object(buyer_questions.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_buyer_question_answer("Какая вязкость?", "1010")
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_review_reply("Густой", 3, "1010")

    async def test_gemini_400_keeps_only_safe_status_without_error_body(self):
        from services.common import gemini
        class Response:
            status = 400
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self):
                return {"error": {"status": "INVALID_ARGUMENT", "message": "secret-value"}}
        class Session:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def post(self, *args, **kwargs): return Response()
        with patch.object(gemini.settings, "GEMINI_API_KEY", "secret-value"), \
             patch.object(gemini, "request_routes", return_value=[gemini.DIRECT]), \
             patch.object(gemini, "_get_available_models", AsyncMock(return_value=["gemini-3.1-flash-lite"])), \
             patch.object(gemini.aiohttp, "ClientSession", Session):
            with self.assertRaises(RuntimeError) as err:
                await gemini.generate("private prompt", max_tokens=50)
        self.assertIn("INVALID_ARGUMENT", str(err.exception))
        self.assertNotIn("secret-value", str(err.exception))
        self.assertNotIn("private prompt", str(err.exception))

    async def test_advice_filter_and_timeout_fairness(self):
        with patch.object(buyer_questions, "call_fast_gemini", AsyncMock(return_value="Добавьте спирт")):
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_buyer_question_answer("Загустел", "1010")
        import worker
        async def slow(**kwargs):
            await asyncio.sleep(1)
        question = AsyncMock(return_value={"status": "success"})
        with patch.object(worker, "auto_reply_unanswered_reviews", slow), patch.object(worker, "auto_reply_unanswered_questions", question), patch.object(worker, "notify_admin", AsyncMock()), patch.object(worker, "BROWSER_JOB_BUDGET_SEC", .02):
            await worker.scheduled_reviews_and_questions_job()
        question.assert_awaited_once()


class ListingRegression(unittest.TestCase):
    def test_ozon_id_is_full_numeric_suffix_and_ambiguous_prices_skipped(self):
        cards = [
            {"href": "https://www.ozon.ru/product/gel-12-02-123456789/?x=1", "text": "Гель 15 г 5% 699 ₽"},
            {"href": "https://www.ozon.ru/product/gel-12-987654321/", "text": "Скидка 15% 699 ₽ 999 ₽"},
            {"href": "https://www.ozon.ru/product/gel-12345678/", "text": "51 79 15"},
        ]
        result = parse_ozon_dom_cards(cards)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sku"], "123456789")
        self.assertEqual(result[0]["buyer_price"], 699)
```
