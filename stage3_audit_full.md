# Этап 3 — Senior-аудит (Финансы, Реклама и Конкуренты) и полные исправленные файлы

## Список найденных проблем и исправлений

### 1. `services/common/orders_report.py`

1. **`sync_and_generate_orders_report` / индексация `Product` (`products_by_sku`) и `ext_to_art`:**
   - **Суть бага:** таблица `products` имеет составной первичный ключ `(sku, marketplace)`, так как артикулы YaSoKo (`1001`, `1002` и т.д.) едины на Ozon и WB. Прежний словарь `{p.sku: p}` перезаписывал себестоимость и первую милю Ozon данными WB (и наоборот), а общий `ext_to_art` смешивал внешние идентификаторы обеих площадок.
   - **Исправление:** введена строгая раздельная индексация `(marketplace, sku)` и `(marketplace, external_id)` (`_index_products_by_marketplace`, `_lookup_product_for_order`) и раздельное сопоставление `OZON_SKU_TO_ART` / `WB_SKU_TO_ART`.

2. **`sync_and_generate_orders_report` / `calc_cogs`:**
   - **Суть бага:** при дублирующихся `order_id` в ответе API и при заказах со статусами отмены/возврата себестоимость списывалась повторно; при отсутствии себестоимости в БД отчёт молча показывал `0 ₽` без предупреждения, завышая чистую прибыль.
   - **Исправление:** добавлена дедупликация по `order_id`, исключение отменённых/возвратных заказов (`CANCELLED_ORDER_STATUSES`) и явное предупреждение в отчёте о количестве единиц и артикулах без заполненной себестоимости (без подстановки вымышленных сумм).

3. **`sync_and_generate_orders_report` (расчёт эквайринга `1.5%`, налога УСН `6%`, фактической логистики Ozon `~39 ₽` и чистой прибыли):**
   - **Суть бага:**
     - ставка УСН была захардкожена (`0.06`) вместо `settings.TAX_RATE`, а при отрицательном `sales_total` (период только с возвратами) рассчитывался отрицательный налог УСН, искусственно завышающий чистую прибыль;
     - если эквайринг ещё не разнесён в cash-flow Ozon или WB (`acquiring == 0` при положительной выручке), эквайринг `1.5%` (`settings.ACQUIRING_RATE`) терялся; при отсутствии ключей `total_deductions` / `total_payout` в блоке Ozon выплата обнулялась (`0.0`);
     - двоичное округление `float` / `round()` (банковское `ROUND_HALF_EVEN`) давало ошибку на копейках (например, `299 * 0.015 -> 4.48` вместо `4.49 ₽`);
     - в блоке Ozon не выводилась фактическая стоимость доставки на 1 заказ (`deliv_total / orders_count`, `~38.87 ₽ ≈ 39 ₽`), а при пустом периоде отображались некорректные `(100%)` и `(6.0%)`.
   - **Исправление:** все денежные расчёты переведены на `Decimal` с `ROUND_HALF_UP`; используются константы `settings.ACQUIRING_RATE` (`1.5%`) и `settings.TAX_RATE` (`6%`); при нулевом эквайринге и положительных продажах доначисляется расчётный эквайринг `1.5%` (с явной пометкой `расч. 1.5%` и защитой от двойного учёта, когда эквайринг уже пришёл из API); налоговая база ограничена снизу нулём (`max(..., 0.0)`); выводится фактическая логистика Ozon на заказ при `orders_count > 0`; полностью исключено деление на ноль и `TypeError` на `None` при пустых периодах.

4. **`sync_and_generate_orders_report` (запись заказов в SQLite и HTML-безопасность):**
   - **Суть бага:** выполнялись N+1 запросы `SELECT` на каждый заказ без `_db_write_lock`, без `get_db_session()` и без обработки `IntegrityError` (дубль `order_id` в пачке или конкурентной задаче ронял отчёт); тексты ошибок API и `source` вставлялись в HTML без `html.escape()`.
   - **Исправление:** добавлены дедупликация пачки, пакетная выборка существующих `order_id` чанками по 500, сериализация записи через `_db_write_lock` + `get_db_session()`, обработка `IntegrityError` через savepoint и экранирование всех динамических строк.

---

### 2. `services/common/financial_pnl.py`

1. **`get_previous_month_bounds`, `generate_monthly_pnl_report`:**
   - **Суть бага:** использовался устаревший `datetime.utcnow()` вместо бизнес-таймзоны `settings.TIMEZONE` (`Europe/Moscow`), из-за чего в интервале `00:00–02:59 МСК` 1-го и 5-го числа неверно определялись прошлый месяц и признак `now_day < 5`; два раздельных вызова времени создавали гонку на границе суток; аннотация `Dict[str, str]` противоречила целочисленному `days_count`.
   - **Исправление:** переход на единый снимок времени в `ZoneInfo(settings.TIMEZONE)` (с расчётом границ по `date()` без искажений часового пояса) и корректную типизацию `Dict[str, Any]`.

2. **`generate_weekly_top_and_unprofitable_report` (юнит-экономика Ozon и WB, эквайринг `1.5%`, УСН `6%`, логистика Ozon `~39 ₽`, запрет фейковой себестоимости):**
   - **Суть бага:**
     - при отсутствии себестоимости подставлялась вымышленная константа `cogs = 70.0 ₽`, а fallback `products_map.get(art)` смешивал себестоимость между Ozon и WB;
     - для WB в `mp_fee` входили только `commission + logistics` — эквайринг `1.5%` и общемагазинные удержания WB (хранение, приёмка, реклама, штрафы, обратная логистика) полностью терялись;
     - для Ozon вместо реальной комиссии (`22–23%` до `300 ₽` и `52%` свыше `300 ₽`), эквайринга `1.5%` и поштучной фактической доставки (`~39 ₽/заказ`) применялся грубый средний процент `oz_overhead_ratio` с искусственным зажимом `[0.20, 0.85]` и заглушкой `0.35`, маскировавший убыточные товары в ловушке `> 300 ₽`;
     - для Ozon налоговая база УСН `6%` ошибочно подменялась текущей витринной `marketing_price` (`t_live["buyer_price"]`) вместо цены реализации заказа;
     - не отфильтровывались отменённые заказы и дубли `order_id`; ошибки API скрывались под сообщением «заказов не найдено»; в сообщении пустого периода было зашито «7 дней» вместо аргумента `days`; не экранировались HTML-символы.
   - **Исправление:** реализован точный поштучный расчёт через `Decimal` (`ROUND_HALF_UP`) с учётом порога `settings.OZON_LOW_PRICE_LIMIT` (`300 ₽`), комиссии `settings.OZON_HIGH_COMMISSION_RATE` (`52%`), эквайринга `settings.ACQUIRING_RATE` (`1.5%`), налога УСН `settings.TAX_RATE` (`6%`), фактической логистики Ozon (`deliv_total / orders_count`) и накладных расходов; фейковая себестоимость `70 ₽` удалена (товары без `/costs` помечаются явно и не искажают рейтинг убыточных); добавлены защита от деления на ноль, вывод ошибок API и HTML-экранирование.

3. **`generate_weekly_pnl_report`:**
   - **Суть бага:** создавались отдельные несвязанные экземпляры API-клиентов при каждом подвызове.
   - **Исправление:** добавлены опциональные параметры `ozon_service`/`wb_service` с переиспользованием единых экземпляров.

---

### 3. `services/common/ad_recommendations.py`

1. **`generate_weekly_ads_report_and_recommendations` (фейковые заглушки `172` заказа и `6686.0 ₽` доставки, отсутствие обработки ошибок API):**
   - **Суть бага:** строки `max(int(oz_fin.get("orders_count") or 172), 1)` и `float(oz_fin.get("deliv_total") or 6686.0)` подменяли пустой период вымышленными `172` заказами и доставкой `6686 ₽` (а при малом числе заказов без разнесённой доставки завышали логистику в десятки раз). Прямой вызов `ozon_api.fetch_active_ad_campaigns(days=7)` без перехвата исключений ронял всю еженедельную задачу при ошибке Performance API.
   - **Исправление:** все заглушки удалены; фактическая доставка Ozon рассчитывается строго как `deliv_total / orders_count` только при `orders_count > 0` и `deliv_total > 0` (иначе `None` — без деления на ноль); все запросы к API и БД выполняются параллельно через `asyncio.gather(..., return_exceptions=True)`.

2. **`analyze_ozon_campaigns`, `calculate_ozon_ad_unit_economics`, `generate_weekly_ads_report_and_recommendations` (разбор 2 кампаний Ozon с ДРР `6.3%` и `15.4%` и отделение от органики):**
   - **Суть бага:** состав и ДРР двух кампаний Ozon (Кампания №1: 4 товара, ДРР `6.3%`; Кампания №2: 2 товара, ДРР `15.4%`) и фиксированные суммы прибыли (`+54,5 ₽`, `+27,5 ₽`, `+83 ₽` при `740 ₽`) были жёстко зашиты в текст промпта и выдавались даже при пустом/ошибочном ответе API; идентификаторы `ozon_sku` из Performance API не сопоставлялись с артикулами `offer_id`, не удалялись дубли и остановленные РК, а рекламируемые и органические SKU смешивались в одном списке `oz_summary`.
   - **Исправление:** выделена детерминированная функция `analyze_ozon_campaigns`: сопоставляет `ozon_sku` (`OZON_SKU_TO_ART`), `product_id` и `offer_id` строго в контуре Ozon; отфильтровывает неактивные кампании; дедуплицирует SKU; точно считает `products_count` и `drr_in_promo_pct` (включая штатный разбор Кампании №1 на 4 SKU с ДРР `6.3%` и Кампании №2 на 2 SKU с ДРР `15.4%`, а также защиту от деления на ноль при `spend > 0, revenue == 0`); строго отделяет подтверждённые органические SKU (`ДРР 0.0%`) от продвигаемых; выявляет пересечения SKU между РК и товары с нулевым/критическим остатком (`< 2 дн.`); при недоступности Performance API не создаёт фейковых кампаний и не объявляет непроверенные SKU органикой; считает точную поштучную прибыль и безубыточный ДРР с эквайрингом `1.5%`, УСН `6%`, комиссией `22–23%` / `52%` и фактической доставкой `~39 ₽`.

3. **`_call_gemini_unlimited`, `sanitize_telegram_html`:**
   - **Суть бага:** перебор `PROXY_CANDIDATES` игнорировал `settings.LOCAL_PROXY_URL` и допускал обход локального прокси (системный прокси / прямое соединение); в `ClientTimeout` отсутствовали `connect`/`sock_read`; неэкранированные `<`, `>`, `&` и Markdown из ответа Gemini ломали отправку в Telegram с `parse_mode="HTML"`.
   - **Исправление:** вызов Gemini выполняется строго через `settings.LOCAL_PROXY_URL` (`trust_env=False`) с полными таймаутами (`total=120, connect=15, sock_connect=15, sock_read=90`), гарантированным закрытием `ClientSession` и `TCPConnector` в `finally`, и стековой санитизацией HTML (`sanitize_telegram_html`).

---

### 4. `services/competitors/deep_parser.py`

1. **`_fetch_json_direct`, `parse_wb_competitors`, `_fetch_wb_native_sync`, `parse_ozon_competitors`, `_call_gemini_competitor_verdict` (сессии `aiohttp`, таймауты, маршрутизация и обработка ошибок):**
   - **Суть бага:**
     - `aiohttp.ClientSession` и `urllib.request.urlopen` создавались без отключения системного прокси (`trust_env=False` / `ProxyHandler({})`), без `connect`/`sock_read` таймаутов, без ограничения размера ответа (риск OOM на 4 ГБ RAM) и без явного закрытия `TCPConnector`;
     - `await resp.json()` падал с `ContentTypeError`, когда WB возвращал JSON с `Content-Type: text/plain`;
     - обращение `p.get("sizes", [{}])[0]` падало с `IndexError` на реальных карточках WB с пустым списком `"sizes": []`;
     - все исключения и HTTP-статусы (`403`, `429`, `5xx`, битый JSON) молча проглатывались (`except Exception: pass`), а вызов `_call_gemini_advisory` допускал прямое соединение в обход `LOCAL_PROXY_URL`.
   - **Исправление:** реализован единый `_fetch_json_direct` с проверкой `https://`, `trust_env=False`, детальными таймаутами (`total=15, connect=6, sock_connect=6, sock_read=10`), лимитом чтения `2 МБ`, независимым разбором `json.loads`, раздельной обработкой `TimeoutError` / `aiohttp.ClientError` / `OSError` / `JSONDecodeError` / HTTP `403`/`429`/`5xx`, логированием и гарантированным закрытием `ClientSession` и `TCPConnector` в `finally` (включая `CancelledError`); резервный `_fetch_wb_native_sync` изолирован через `ProxyHandler({})` с таймаутом `10 сек.`; вызов Gemini переведён на `settings.LOCAL_PROXY_URL` с полным закрытием сессии и коннектора.

2. **`parse_wb_competitors`, `parse_ozon_competitors`, `run_full_competitor_deep_audit` (удаление фейковых заглушек и защита от деления на ноль):**
   - **Суть бага:** при `< 8` карточках WB к выдаче подмешивались 15 вымышленных товаров `wb_top_defaults`, а `parse_ozon_competitors` всегда возвращал 15 вымышленных товаров `ozon_top_defaults`; при нулевой цене/рейтинге/отзывах подставлялись `320 ₽`, `4.8`, `150`; при пустой выдаче подставлялись фейковые средние `335 ₽` и `328 ₽`, а полусумма двух средних выдавалась за «рыночную медиану».
   - **Исправление:** все 30 вымышленных карточек и фейковые дефолты удалены; `parse_ozon_competitors` выполняет реальный запрос с безопасным разбором `widgetStates` и честно возвращает `[]` при блокировке антиботом; при пустых выборках исключено деление на ноль, считается настоящая `statistics.median`, экранируются все HTML-поля.

3. **`calculate_competitor_unit_economy`:**
   - **Суть бага:** для Ozon использовалась завышенная логистика `65 ₽` вместо эталонной `~39 ₽`, игнорировалась комиссия `52%` свыше `300 ₽` (`settings.OZON_HIGH_COMMISSION_RATE`), налог УСН `6%` на Ozon считался от витринной цены вместо цены продавца, эквайринг `1.5%` не входил в выводимую сумму расходов МП, проверка `mp == "WB"` была чувствительна к регистру, а при `buyer_price <= 0` возвращался некорректный убыток `-140 ₽`.
   - **Исправление:** расчёт переведён на `Decimal` (`ROUND_HALF_UP`) и константы `settings.ACQUIRING_RATE` (`1.5%`), `settings.TAX_RATE` (`6%`), `settings.OZON_LOW_PRICE_LIMIT` (`300 ₽`), `settings.OZON_HIGH_COMMISSION_RATE` (`52%`), логистика Ozon `39.0 ₽`, корректная база УСН по каждой площадке и безопасный возврат нулей при `buyer_price <= 0`.

---

## Проверка и ограничения

- Выполнено 22 автоматических теста (`tests/test_stage2.py` — 11 тестов, `tests/test_stage3.py` — 11 тестов) с `-W default`: точная копеечная сверка чистой прибыли, эквайринга `1.5%`, УСН `6%`, фактической логистики Ozon `38.87 ₽/заказ` (`6686 / 172`), изоляция себестоимости Ozon/WB, пустые и возвратные периоды без деления на ноль, разбор 2 кампаний Ozon (4 SKU с ДРР `6.3%` и 2 SKU с ДРР `15.4%`) с полным отделением от органических SKU и без заглушек при сбое API, закрытие всех `aiohttp.ClientSession` и `TCPConnector` при таймауте, HTTP `429`, `text/plain` JSON и `CancelledError`.
- Реальные сетевые запросы к кабинетам Ozon, Wildberries, витринам и Gemini в тестовой среде не выполнялись; фактическая доступность витрины Ozon без антибот-блокировки и ключей Ozon Performance API проверяется на боевом хосте.
- Файлы `.env`, `logs/`, `backups/` и `*.db` не читались.

---

## Полные исправленные исходники (без сокращений)

### `services/common/orders_report.py`

```python
import asyncio
import html
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import settings
from database.crud import _db_write_lock
from database.models import OrderRecord, Product, get_db_session, utcnow_naive
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


def _parse_order_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        if raw:
            try:
                dt = datetime.fromisoformat(raw)
                return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
            except ValueError:
                pass
    return utcnow_naive()


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
    amt = max(_safe_float(value, 0.0), 0.0)
    if amt < 0.005:
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
            if not order_id or order_id in deduped:
                continue
            mp = str(ord_data.get("marketplace") or default_mp).strip().lower()
            if mp not in {"ozon", "wb"}:
                mp = default_mp
            raw_sku = str(ord_data.get("sku") or "").strip()
            deduped[order_id] = {
                "order_id": order_id[:128],
                "marketplace": mp,
                "sku": _resolve_order_sku(raw_sku, mp)[:64],
                "created_at": _parse_order_datetime(ord_data.get("created_at")),
                "price": round(_safe_nonneg(ord_data.get("price")), 2),
                "commission": round(_safe_nonneg(ord_data.get("commission")), 2),
                "logistics": round(_safe_nonneg(ord_data.get("logistics")), 2),
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
        f"чистая прибыль не учитывает их закупочную стоимость.</i>\n"
    )


async def sync_and_generate_orders_report(
    ozon_service: OzonSellerAPI,
    wb_service: WBStatisticsAPI,
    days: int = 7,
    custom_date_from: Optional[str] = None,
    custom_date_to: Optional[str] = None,
    report_title: str = "📊 Точный финансовый отчет (Данные ЛК Ozon и WB)",
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
        oz_comm = round(_safe_nonneg(ozon_fin.get("commission")), 2)

        oz_d_dir = round(_safe_nonneg(ozon_fin.get("deliv_direct")), 2)
        oz_d_ret = round(_safe_nonneg(ozon_fin.get("deliv_return")), 2)
        oz_d_ho = round(_safe_nonneg(ozon_fin.get("deliv_ozon_handover")), 2)
        oz_deliv = (
            round(_safe_nonneg(ozon_fin.get("deliv_total")), 2)
            if ozon_fin.get("deliv_total") is not None
            else round(oz_d_dir + oz_d_ret + oz_d_ho, 2)
        )
        oz_deliv_per_order: Optional[float] = (
            _div_rub(oz_deliv, oz_count) if oz_count > 0 else None
        )

        oz_dropoff = _round_rub(_safe_nonneg(ozon_fin.get("partner_dropoff")))
        oz_pack = _round_rub(_safe_nonneg(ozon_fin.get("partner_pack")))
        oz_pret = _round_rub(_safe_nonneg(ozon_fin.get("partner_returns")))
        raw_oz_acq = _round_rub(_safe_nonneg(ozon_fin.get("acquiring")))
        oz_star = _round_rub(_safe_nonneg(ozon_fin.get("star_products")))
        oz_plm = _round_rub(_safe_nonneg(ozon_fin.get("partner_last_mile")))

        # Если эквайринг еще не отражен в cash-flow Ozon при положительных продажах,
        # учитываем эталонный эквайринг 1.5% (settings.ACQUIRING_RATE) без двойного списания.
        oz_fbo = _round_rub(_safe_nonneg(ozon_fin.get("fbo_storage")))
        oz_cpo = _round_rub(_safe_nonneg(ozon_fin.get("promo_cpo")))
        oz_cpc = _round_rub(_safe_nonneg(ozon_fin.get("promo_cpc")))
        oz_ads = (
            _round_rub(_safe_nonneg(ozon_fin.get("ads_total")))
            if ozon_fin.get("ads_total") is not None
            else _round_rub(_to_dec(oz_cpo) + _to_dec(oz_cpc))
        )
        oz_fines = _round_rub(_safe_nonneg(ozon_fin.get("other_fines")))
        oz_decomp = _round_rub(_safe_nonneg(ozon_fin.get("decompensations")))
        oz_mutual = _round_rub(_safe_nonneg(ozon_fin.get("mutual_settlements")))

        oz_part_no_acq = (
            _round_rub(_safe_nonneg(ozon_fin.get("partners_total")))
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
        if raw_oz_acq > 0.0:
            oz_acq = raw_oz_acq
            oz_acq_delta = 0.0
        elif oz_sales > 0.0 and acq_rate > 0.0:
            est_oz_acq = _mul_rub(oz_sales, acq_rate)
            raw_tot_ded = ozon_fin.get("total_deductions")
            lump_sum_already_covers_acq = (
                "acquiring" not in ozon_fin
                and raw_tot_ded is not None
                and _safe_nonneg(raw_tot_ded) >= known_oz_ded_no_acq + est_oz_acq - 0.05
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
            oz_ded_tot = _round_rub(_to_dec(_safe_nonneg(ozon_fin.get("total_deductions"))) + _to_dec(oz_acq_delta))
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
            f"• Заказано единиц: <b>{oz_count} шт.</b>\n"
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
            f"📦 Себестоимость + Упаковка + Первая миля: <b>{_fmt_expense(oz_cogs)}</b> ({_pct(oz_cogs, oz_sales)})\n"
            f"{oz_missing_note}"
            f"🏛 Налог УСН ({tax_pct_label}): <b>{_fmt_expense(oz_tax)}</b> ({_pct(oz_tax, oz_sales)})\n"
            f"👉 <b>Чистая прибыль Ozon:</b> <code>{_fmt_money(oz_net_profit)} ₽</code> (<b>{_pct(oz_net_profit, oz_sales)}</b>)\n\n"
        )

    # --- БЛОК WILDBERRIES ---
    if not isinstance(wb_fin, dict):
        api_errors_count += 1
        err_text = html.escape(str(wb_fin))
        wb_block = f"🟣 <b>WILDBERRIES:</b>\n❌ Ошибка API WB: <code>{err_text}</code>\n\n"
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = wb_acq = 0.0
        wb_count = wb_missing_cogs = 0
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
        wb_comm = round(_safe_nonneg(wb_fin.get("commission")), 2)

        raw_wb_acq = _round_rub(_safe_nonneg(wb_fin.get("acquiring")))
        wb_acq_base = max(wb_rev_b, wb_rev_s, 0.0)

        wb_log_ret = _round_rub(_safe_nonneg(wb_fin.get("logistics_return")))
        if wb_fin.get("logistics") is not None:
            wb_log = _round_rub(_safe_nonneg(wb_fin.get("logistics")))
            wb_log_dir = (
                _round_rub(_safe_nonneg(wb_fin.get("logistics_direct")))
                if wb_fin.get("logistics_direct") is not None
                else _round_rub(max(_to_dec(wb_log) - _to_dec(wb_log_ret), Decimal("0")))
            )
        else:
            wb_log_dir = _round_rub(_safe_nonneg(wb_fin.get("logistics_direct")))
            wb_log = _round_rub(_to_dec(wb_log_dir) + _to_dec(wb_log_ret))

        wb_stor = _round_rub(_safe_nonneg(wb_fin.get("storage")))
        wb_acc = _round_rub(_safe_nonneg(wb_fin.get("acceptance")))
        wb_ads = _round_rub(_safe_nonneg(wb_fin.get("ads")))
        wb_other = _round_rub(_safe_nonneg(wb_fin.get("other_services")))

        known_wb_ded_no_acq = _round_rub(
            _to_dec(wb_comm)
            + _to_dec(wb_log)
            + _to_dec(wb_stor)
            + _to_dec(wb_acc)
            + _to_dec(wb_ads)
            + _to_dec(wb_other)
        )

        wb_acq_estimated = False
        if raw_wb_acq > 0.0:
            wb_acq = raw_wb_acq
            wb_acq_delta = 0.0
        elif wb_acq_base > 0.0 and acq_rate > 0.0:
            est_wb_acq = _mul_rub(wb_acq_base, acq_rate)
            raw_wb_tot_ded = wb_fin.get("total_deductions")
            wb_lump_covers_acq = (
                "acquiring" not in wb_fin
                and raw_wb_tot_ded is not None
                and _safe_nonneg(raw_wb_tot_ded) >= known_wb_ded_no_acq + est_wb_acq - 0.05
            )
            wb_acq = est_wb_acq
            wb_acq_delta = 0.0 if wb_lump_covers_acq else est_wb_acq
            wb_acq_estimated = True
        else:
            wb_acq = 0.0
            wb_acq_delta = 0.0

        computed_wb_ded = _round_rub(_to_dec(known_wb_ded_no_acq) + _to_dec(wb_acq))
        if wb_fin.get("total_deductions") is not None:
            wb_ded_tot = _round_rub(_to_dec(_safe_nonneg(wb_fin.get("total_deductions"))) + _to_dec(wb_acq_delta))
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
            f"• Заказано единиц: <b>{wb_count} шт.</b> (Отмены/возвраты: {_fmt_money(wb_ret)} ₽)\n"
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
            f"📦 Себестоимость + Упаковка + Первая миля: <b>{_fmt_expense(wb_cogs)}</b> ({_pct(wb_cogs, wb_rev_s)})\n"
            f"{wb_missing_note}"
            f"🏛 Налог УСН {tax_pct_label} (с {_fmt_money(wb_tax_base)} ₽): <b>{_fmt_expense(wb_tax)}</b>\n"
            f"👉 <b>Чистая прибыль WB:</b> <code>{_fmt_money(wb_net_profit)} ₽</code> (<b>{_pct(wb_net_profit, wb_rev_s)}</b>)\n\n"
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
    warnings_suffix = ("\n" + "\n".join(summary_warnings)) if summary_warnings else ""

    safe_title = html.escape(str(report_title))
    return (
        f"<b>{safe_title}</b>\n\n"
        f"{ozon_block}"
        f"{wb_block}"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏁 <b>СВОДНЫЙ ИТОГ (Ozon + WB):</b>\n"
        f"• Всего единиц: <b>{tot_count} шт.</b>\n"
        f"• Общий оборот (в ценах ЛК): <b>{_fmt_money(tot_sales)} ₽</b>\n"
        f"• Все удержания маркетплейсов: <b>{_fmt_expense(tot_ded)}</b> ({_pct(tot_ded, tot_sales)}), в т.ч. эквайринг <b>{_fmt_expense(tot_acq)}</b>\n"
        f"• Себестоимость, упаковка и первая миля: <b>{_fmt_expense(tot_cogs)}</b> ({_pct(tot_cogs, tot_sales)})\n"
        f"• Налог УСН {tax_pct_label}: <b>{_fmt_expense(tot_tax)}</b>\n"
        f"💰 <b>ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <code>{_fmt_money(tot_profit)} ₽</code> (<b>{_pct(tot_profit, tot_sales)} ROS</b>)"
        f"{warnings_suffix}"
    )
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
        if 0.0 < weighted < high_comm:
            return weighted

    if prod_db is not None:
        db_comm = _safe_nonneg(prod_db.commission_rate)
        if 0.0 < db_comm < high_comm:
            return db_comm

    if report_comm_rate is not None and 0.0 < report_comm_rate < high_comm:
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

    report_body = await sync_and_generate_orders_report(
        ozon_service=oz_api,
        wb_service=wb_api,
        days=int(bounds["days_count"]),
        custom_date_from=str(bounds["date_from"]),
        custom_date_to=str(bounds["date_to"]),
        report_title=(
            f"📅 ИТОГОВЫЙ P&L ОТЧЕТ ЗА ПРОШЛЫЙ МЕСЯЦ: "
            f"{str(bounds['title']).upper()} ({bounds['date_from']} .. {bounds['date_to']})"
        ),
    )
    return publication_notice + report_body


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

            raw_oz_acq = _safe_nonneg(oz_fin.get("acquiring"))
            if raw_oz_acq > 0.0:
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
    if isinstance(wb_fin, dict):
        wb_units_from_orders = sum(max(_safe_int(o.get("quantity"), 1), 1) for o in active_wb_orders)
        raw_wb_cnt = max(_safe_int(wb_fin.get("orders_count"), 0), 0)
        wb_orders_cnt = raw_wb_cnt if raw_wb_cnt > 0 else wb_units_from_orders

        wb_rev_s = _safe_float(wb_fin.get("revenue_seller"))
        wb_rev_b = (
            _safe_float(wb_fin.get("revenue_buyer"))
            if wb_fin.get("revenue_buyer") is not None
            else wb_rev_s
        )
        wb_acq_base_total = max(wb_rev_b, wb_rev_s, 0.0)
        raw_wb_acq = _safe_nonneg(wb_fin.get("acquiring"))
        if raw_wb_acq > 0.0 and wb_acq_base_total > 0.0:
            wb_acq_rate = raw_wb_acq / wb_acq_base_total

        if wb_orders_cnt > 0:
            wb_other_total = (
                _safe_nonneg(wb_fin.get("storage"))
                + _safe_nonneg(wb_fin.get("acceptance"))
                + _safe_nonneg(wb_fin.get("ads"))
                + _safe_nonneg(wb_fin.get("other_services"))
                + _safe_nonneg(wb_fin.get("logistics_return"))
            )
            if wb_other_total > 0.0:
                wb_overhead_per_order = _div_rub(wb_other_total, wb_orders_cnt)

    sku_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    oz_delivery_missing_flag = False

    combined_orders: List[Tuple[str, Dict[str, Any]]] = [
        *[("ozon", o) for o in active_oz_orders],
        *[("wb", o) for o in active_wb_orders],
    ]

    for default_mp, order in combined_orders:
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
            ord_comm = _safe_nonneg(order.get("commission"))
            if ord_comm > 0.0:
                unit_comm = _round_rub(ord_comm)
            else:
                c_rate = _resolve_ozon_commission_rate(unit_price, t_live, p_db, oz_report_comm_rate)
                unit_comm = _mul_rub(unit_price, c_rate)

            ord_acq = _safe_nonneg(order.get("acquiring"))
            unit_acq = _round_rub(ord_acq) if ord_acq > 0.0 else _mul_rub(unit_price, oz_acq_rate)

            ord_log = _safe_nonneg(order.get("logistics"))
            if ord_log > 0.0:
                unit_log = _round_rub(ord_log)
            elif oz_deliv_per_order is not None:
                unit_log = oz_deliv_per_order
            else:
                unit_log = 0.0
                oz_delivery_missing_flag = True

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
                + _to_dec(oz_dropoff_per_order)
                + _to_dec(oz_other_overhead_per_order)
                + _to_dec(unit_ad)
            )
            # На Ozon база УСН 6% — цена реализации продавца.
            unit_tax = _mul_rub(unit_price, tax_rate)
        else:
            unit_comm = _round_rub(_safe_nonneg(order.get("commission")))
            unit_log = _round_rub(_safe_nonneg(order.get("logistics")))
            ord_acq = _safe_nonneg(order.get("acquiring"))
            unit_acq = (
                _round_rub(ord_acq)
                if ord_acq > 0.0
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
            profit_line = "   • Чистая прибыль: <i>н/д (не задана себестоимость в /costs)</i>"
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
            f"\n⚠️ <i>Без подтвержденной себестоимости (исключены из расчета прибыли): "
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

### `services/common/ad_recommendations.py`

```python
import asyncio
import html
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set

import aiohttp
from sqlalchemy import select

from config import settings
from database.models import Product, get_db_session
from products_catalog import OZON_SKU_TO_ART, PRODUCTS_CATALOG, WB_SKU_TO_ART
from services.common.orders_report import (
    _div_rub,
    _fmt_money,
    _mul_rub,
    _round_pct,
    _round_rub,
    _safe_float,
    _safe_int,
    _safe_nonneg,
    _to_dec,
)

GEMINI_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=15, sock_connect=15, sock_read=90)
GEMINI_MODELS_TIMEOUT = aiohttp.ClientTimeout(total=12, connect=8, sock_connect=8, sock_read=10)

INACTIVE_CAMPAIGN_STATES: Set[str] = {
    "INACTIVE",
    "STOPPED",
    "ARCHIVED",
    "FINISHED",
    "CANCELLED",
    "CANCELED",
    "PAUSED",
    "CAMPAIGN_STATE_INACTIVE",
    "CAMPAIGN_STATE_STOPPED",
    "CAMPAIGN_STATE_ARCHIVED",
    "CAMPAIGN_STATE_FINISHED",
    "CAMPAIGN_STATE_PLANNED",
}

_EXCLUDED_GEMINI_MODEL_TOKENS = ("tts", "embedding", "imagen", "veo", "aqa", "whisper", "audio")
_FALLBACK_GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
)
_TAG_TOKEN_RE = re.compile(r"<(/?)(b|i|code)>", re.IGNORECASE)


def sanitize_telegram_html(raw_text: str) -> str:
    """
    Очищает текст для безопасной отправки в Telegram (parse_mode='HTML'):
    убирает Markdown-разметку, экранирует произвольные '<', '>', '&' и гарантирует
    строгий баланс разрешенных тегов <b>, <i>, <code>.
    """
    if not raw_text:
        return ""

    cleaned = re.sub(r"^#{1,6}\s*", "", raw_text, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", cleaned)

    out: List[str] = []
    open_stack: List[str] = []
    cursor = 0

    for match in _TAG_TOKEN_RE.finditer(cleaned):
        start, end = match.span()
        if start > cursor:
            out.append(html.escape(cleaned[cursor:start]))
        is_closing = bool(match.group(1))
        tag = match.group(2).lower()

        if not is_closing:
            if tag not in open_stack:
                open_stack.append(tag)
                out.append(f"<{tag}>")
        else:
            if tag in open_stack:
                to_reopen: List[str] = []
                while open_stack:
                    top = open_stack.pop()
                    out.append(f"</{top}>")
                    if top == tag:
                        break
                    to_reopen.append(top)
                for reopen_tag in reversed(to_reopen):
                    open_stack.append(reopen_tag)
                    out.append(f"<{reopen_tag}>")
        cursor = end

    if cursor < len(cleaned):
        out.append(html.escape(cleaned[cursor:]))

    while open_stack:
        out.append(f"</{open_stack.pop()}>")

    return "".join(out).strip()


async def _discover_models_via_proxy(
    session: aiohttp.ClientSession,
    api_key: str,
    proxy_url: str,
) -> List[str]:
    configured = str(getattr(settings, "GEMINI_MODEL", "") or "").replace("models/", "").strip()
    discovered: List[str] = []
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with session.get(url, proxy=proxy_url, timeout=GEMINI_MODELS_TIMEOUT) as resp:
            if resp.status == 200:
                raw = await resp.text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
                for item in data.get("models") or []:
                    if not isinstance(item, dict):
                        continue
                    methods = item.get("supportedGenerationMethods") or []
                    name = str(item.get("name") or "").replace("models/", "").strip()
                    low = name.lower()
                    if (
                        name
                        and "generateContent" in methods
                        and not any(tok in low for tok in _EXCLUDED_GEMINI_MODEL_TOKENS)
                    ):
                        discovered.append(name)
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, OSError):
        pass

    discovered.sort(
        key=lambda x: ("2.5-flash" in x, "flash" in x, "2.5" in x),
        reverse=True,
    )
    ordered = [
        m
        for m in [configured, *discovered, *_FALLBACK_GEMINI_MODELS]
        if m and not any(tok in m.lower() for tok in _EXCLUDED_GEMINI_MODEL_TOKENS)
    ]
    return list(dict.fromkeys(ordered))


async def _call_gemini_unlimited(prompt: str) -> str:
    """
    Запрашивает аналитические рекомендации Gemini строго через настроенный локальный прокси
    (settings.LOCAL_PROXY_URL, trust_env=False) с гарантированным закрытием сессии и коннектора.
    """
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return "⚠️ <i>GEMINI_API_KEY не задан — показан только проверенный фактический расчет.</i>"

    proxy_url = str(getattr(settings, "LOCAL_PROXY_URL", "") or "").strip()
    if not proxy_url:
        return "⚠️ <i>LOCAL_PROXY_URL не задан — прямой вызов Gemini отключён правилами маршрутизации.</i>"

    last_error = "нет ответа от доступных моделей"
    connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            timeout=GEMINI_TIMEOUT,
        ) as session:
            models = await _discover_models_via_proxy(session, api_key, proxy_url)
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.15, "maxOutputTokens": 8192},
            }
            for model_name in models:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model_name}:generateContent?key={api_key}"
                )
                try:
                    async with session.post(url, json=payload, proxy=proxy_url) as resp:
                        body_text = await resp.text(encoding="utf-8", errors="replace")
                        if resp.status in (401, 403):
                            last_error = f"{model_name}: HTTP {resp.status} (доступ отклонён)"
                            break
                        if resp.status != 200:
                            last_error = f"{model_name}: HTTP {resp.status}"
                            continue
                        data = json.loads(body_text)
                        candidates = data.get("candidates") or []
                        if not candidates:
                            continue
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        text_out = "".join(
                            str(p.get("text") or "")
                            for p in parts
                            if isinstance(p, dict) and not p.get("thought", False)
                        ).strip()
                        if text_out:
                            return sanitize_telegram_html(text_out)
                except asyncio.TimeoutError:
                    last_error = f"{model_name}: таймаут запроса"
                    continue
                except (aiohttp.ClientError, json.JSONDecodeError, OSError) as exc:
                    last_error = f"{model_name}: {type(exc).__name__}"
                    continue
    except asyncio.TimeoutError:
        last_error = "таймаут подключения к локальному прокси"
    except (aiohttp.ClientError, OSError) as exc:
        last_error = f"ошибка прокси ({type(exc).__name__})"
    finally:
        if not connector.closed:
            await connector.close()

    return (
        f"⚠️ <i>Рекомендации ИИ временно недоступны ({html.escape(last_error)}). "
        f"Выше приведён точный фактический расчёт по данным API.</i>"
    )


def calculate_ozon_ad_unit_economics(
    price: float,
    cogs_rub: Optional[float],
    actual_delivery_rub: Optional[float],
    commission_rate_under_300: float,
    drr_pct: Optional[float],
    fbs_dropoff_rub: float = 0.0,
    overhead_per_order_rub: float = 0.0,
) -> Dict[str, Optional[float]]:
    """
    Точный поштучный расчет юнит-экономики SKU Ozon с учетом:
    - комиссии API до 300 ₽ (22–23%) и 52% свыше 300 ₽ (settings.OZON_HIGH_COMMISSION_RATE);
    - эквайринга 1.5% (settings.ACQUIRING_RATE);
    - налога УСН 6% (settings.TAX_RATE);
    - фактической логистики Ozon (~39 ₽/заказ из отчёта, без выдуманных констант);
    - индивидуального ДРР конкретной кампании (например, 6.3%, 15.4% или 0.0% для органики).
    """
    clean_price = round(_safe_nonneg(price), 2)
    tax_rate = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
    acq_rate = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
    low_limit = _safe_nonneg(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0)
    high_comm = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)

    if clean_price <= 0.0:
        return {
            "price": 0.0,
            "comm_pct": 0.0,
            "comm_rub": 0.0,
            "acq_rub": 0.0,
            "tax_rub": 0.0,
            "ad_pct": round(drr_pct, 1) if drr_pct is not None else None,
            "ad_rub": None if drr_pct is None else 0.0,
            "delivery_rub": round(actual_delivery_rub, 2) if actual_delivery_rub is not None else None,
            "pvz_rub": round(_safe_nonneg(fbs_dropoff_rub), 2),
            "overhead_rub": round(_safe_nonneg(overhead_per_order_rub), 2),
            "cogs_rub": round(cogs_rub, 2) if cogs_rub is not None else None,
            "net_profit": None,
            "ros_pct": 0.0,
            "breakeven_drr_pct": None,
        }

    base_comm = _safe_nonneg(commission_rate_under_300)
    if not (0.0 < base_comm < 1.0):
        base_comm = 0.23
    eff_comm = base_comm if clean_price <= low_limit else high_comm

    comm_rub = _mul_rub(clean_price, eff_comm)
    acq_rub = _mul_rub(clean_price, acq_rate)
    tax_rub = _mul_rub(clean_price, tax_rate)
    ad_rub: Optional[float] = (
        _round_rub((_to_dec(clean_price) * _to_dec(_safe_nonneg(drr_pct))) / _to_dec(100))
        if drr_pct is not None
        else None
    )
    pvz_rub = _round_rub(_safe_nonneg(fbs_dropoff_rub))
    ovh_rub = _round_rub(_safe_nonneg(overhead_per_order_rub))

    if cogs_rub is None or actual_delivery_rub is None:
        return {
            "price": clean_price,
            "comm_pct": round(eff_comm * 100.0, 1),
            "comm_rub": comm_rub,
            "acq_rub": acq_rub,
            "tax_rub": tax_rub,
            "ad_pct": round(_safe_nonneg(drr_pct), 1) if drr_pct is not None else None,
            "ad_rub": ad_rub,
            "delivery_rub": _round_rub(actual_delivery_rub) if actual_delivery_rub is not None else None,
            "pvz_rub": pvz_rub,
            "overhead_rub": ovh_rub,
            "cogs_rub": _round_rub(cogs_rub) if cogs_rub is not None else None,
            "net_profit": None,
            "ros_pct": None,
            "breakeven_drr_pct": None,
        }

    deliv_rub = _round_rub(_safe_nonneg(actual_delivery_rub))
    clean_cogs = _round_rub(_safe_nonneg(cogs_rub))
    profit_before_ads = _round_rub(
        _to_dec(clean_price)
        - _to_dec(comm_rub)
        - _to_dec(acq_rub)
        - _to_dec(tax_rub)
        - _to_dec(deliv_rub)
        - _to_dec(pvz_rub)
        - _to_dec(ovh_rub)
        - _to_dec(clean_cogs)
    )
    breakeven_drr_pct = max(_round_pct(profit_before_ads, clean_price), 0.0)

    if ad_rub is None:
        net_profit: Optional[float] = None
        ros_pct: Optional[float] = None
    else:
        net_profit = _round_rub(_to_dec(profit_before_ads) - _to_dec(ad_rub))
        ros_pct = _round_pct(net_profit, clean_price)

    return {
        "price": clean_price,
        "comm_pct": round(eff_comm * 100.0, 1),
        "comm_rub": comm_rub,
        "acq_rub": acq_rub,
        "tax_rub": tax_rub,
        "ad_pct": round(_safe_nonneg(drr_pct), 1) if drr_pct is not None else None,
        "ad_rub": ad_rub,
        "delivery_rub": deliv_rub,
        "pvz_rub": pvz_rub,
        "overhead_rub": ovh_rub,
        "cogs_rub": clean_cogs,
        "net_profit": net_profit,
        "ros_pct": ros_pct,
        "breakeven_drr_pct": breakeven_drr_pct,
    }


def _extract_raw_campaign_skus(camp: Dict[str, Any]) -> List[str]:
    raw_seq = camp.get("skus_inside")
    if raw_seq is None:
        raw_seq = camp.get("products")
    if raw_seq is None:
        raw_seq = camp.get("skus")
    if not isinstance(raw_seq, list):
        return []

    extracted: List[str] = []
    seen: Set[str] = set()
    for entry in raw_seq:
        if isinstance(entry, dict):
            candidate = (
                entry.get("offer_id")
                or entry.get("sku")
                or entry.get("product_id")
                or entry.get("id")
                or ""
            )
        else:
            candidate = entry
        token = str(candidate or "").strip()
        if not token or token.lower() == "none" or token in seen:
            continue
        seen.add(token)
        extracted.append(token)
    return extracted


def _is_campaign_active(camp: Dict[str, Any]) -> bool:
    state = str(camp.get("state") or "").strip().upper()
    if not state:
        return True
    if state in INACTIVE_CAMPAIGN_STATES:
        return False
    if any(marker in state for marker in ("STOPPED", "INACTIVE", "ARCHIVED", "FINISHED", "PAUSED")):
        return False
    return True


def analyze_ozon_campaigns(
    raw_campaigns: Optional[Sequence[Dict[str, Any]]],
    oz_stocks: Optional[Sequence[Dict[str, Any]]],
    oz_fin: Optional[Dict[str, Any]],
    db_products: Optional[Sequence[Product]] = None,
    campaigns_error: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Разбирает рекламные кампании Ozon и строго отделяет продвигаемые SKU от органических:
    - без подмены пустых или недоступных данных фейковыми заглушками;
    - без смешивания выручки/SKU между Кампанией №1, Кампанией №2 и органическими товарами;
    - без деления на ноль при пустом периоде или нулевой атрибутированной выручке.
    """
    # 1. Финансовые итоги Ozon за период (строго фактические, без 172 и 6686 по умолчанию)
    fin_dict = oz_fin if isinstance(oz_fin, dict) else {}
    oz_orders_cnt = max(_safe_int(fin_dict.get("orders_count"), 0), 0)
    oz_sales_total = round(_safe_float(fin_dict.get("sales_total")), 2)
    oz_deliv_total = round(
        _safe_nonneg(fin_dict.get("deliv_total"))
        if fin_dict.get("deliv_total") is not None
        else (
            _safe_nonneg(fin_dict.get("deliv_direct"))
            + _safe_nonneg(fin_dict.get("deliv_return"))
            + _safe_nonneg(fin_dict.get("deliv_ozon_handover"))
        ),
        2,
    )
    actual_delivery_per_order: Optional[float] = (
        _div_rub(oz_deliv_total, oz_orders_cnt)
        if oz_orders_cnt > 0 and oz_deliv_total > 0.0
        else None
    )

    dropoff_total = _safe_nonneg(fin_dict.get("partner_dropoff"))
    actual_dropoff_per_order = (
        _div_rub(dropoff_total, oz_orders_cnt) if oz_orders_cnt > 0 and dropoff_total > 0.0 else 0.0
    )

    other_hold_total = (
        _safe_nonneg(fin_dict.get("partner_pack"))
        + _safe_nonneg(fin_dict.get("partner_returns"))
        + _safe_nonneg(fin_dict.get("star_products"))
        + _safe_nonneg(fin_dict.get("partner_last_mile"))
        + _safe_nonneg(fin_dict.get("fbo_storage"))
        + _safe_nonneg(fin_dict.get("other_fines"))
        + _safe_nonneg(fin_dict.get("decompensations"))
        + _safe_nonneg(fin_dict.get("mutual_settlements"))
    )
    overhead_per_order = (
        _div_rub(other_hold_total, oz_orders_cnt)
        if oz_orders_cnt > 0 and other_hold_total > 0.0
        else 0.0
    )

    promo_cpo = _round_rub(_safe_nonneg(fin_dict.get("promo_cpo")))
    promo_cpc = _round_rub(_safe_nonneg(fin_dict.get("promo_cpc")))
    ads_total = (
        _round_rub(_safe_nonneg(fin_dict.get("ads_total")))
        if fin_dict.get("ads_total") is not None
        else _round_rub(_to_dec(promo_cpo) + _to_dec(promo_cpc))
    )
    store_tacos_pct = _round_pct(ads_total, oz_sales_total) if oz_sales_total > 0.0 else 0.0

    # 2. Индексация себестоимости и остатков Ozon (строго marketplace == 'ozon')
    oz_cogs_by_sku: Dict[str, float] = {}
    prod_id_to_art: Dict[str, str] = {}
    for prod in db_products or []:
        if str(prod.marketplace or "").strip().lower() != "ozon":
            continue
        sku_key = str(prod.sku or "").strip()
        ext_key = str(prod.external_id or "").strip()
        if sku_key and ext_key:
            prod_id_to_art[ext_key] = sku_key
        if sku_key and _safe_nonneg(prod.cost_price) > 0.0:
            oz_cogs_by_sku[sku_key] = round(
                _safe_nonneg(prod.cost_price)
                + _safe_nonneg(prod.packaging_cost)
                + _safe_nonneg(prod.first_mile_unit_cost),
                2,
            )

    stock_by_art: Dict[str, Dict[str, Any]] = {}
    known_offer_ids: Set[str] = set(PRODUCTS_CATALOG.keys())
    for item in oz_stocks or []:
        if not isinstance(item, dict):
            continue
        raw_offer = str(item.get("sku") or "").strip()
        if not raw_offer:
            continue
        art = OZON_SKU_TO_ART.get(raw_offer, raw_offer)
        known_offer_ids.add(art)
        stock_by_art[art] = item
        pid = str(item.get("product_id") or item.get("external_id") or "").strip()
        if pid:
            prod_id_to_art[pid] = art

    def resolve_ozon_identifier(raw_id: str) -> str:
        clean = str(raw_id or "").strip()
        if not clean:
            return ""
        if clean in known_offer_ids:
            return clean
        if clean in OZON_SKU_TO_ART:
            return OZON_SKU_TO_ART[clean]
        if clean in prod_id_to_art:
            return prod_id_to_art[clean]
        return clean

    def build_sku_card(art: str, drr_pct: Optional[float]) -> Dict[str, Any]:
        st_item = stock_by_art.get(art, {})
        fbo_st = max(_safe_int(st_item.get("stock_fbo"), 0), 0)
        fbs_st = max(_safe_int(st_item.get("stock_fbs"), 0), 0)
        total_st = fbo_st + fbs_st
        daily_sales = round(_safe_nonneg(st_item.get("daily_sales")), 2)
        days_of_stock: Optional[float] = (
            round(total_st / daily_sales, 1) if daily_sales > 0.0 else None
        )

        price = round(_safe_nonneg(st_item.get("current_price")), 2)
        c_fbo = _safe_nonneg(st_item.get("commission_rate_fbo") or st_item.get("commission_rate"))
        c_fbs = _safe_nonneg(st_item.get("commission_rate_fbs") or c_fbo)
        if fbo_st > 0 and fbs_st > 0 and c_fbo > 0.0 and c_fbs > 0.0:
            weighted_comm = (c_fbo * fbo_st + c_fbs * fbs_st) / total_st
            fbs_share = fbs_st / total_st
        elif fbs_st > 0:
            weighted_comm = c_fbs if c_fbs > 0.0 else c_fbo
            fbs_share = 1.0
        else:
            weighted_comm = c_fbo
            fbs_share = 0.0

        weighted_pvz = round(actual_dropoff_per_order * fbs_share, 2)
        cogs_val = oz_cogs_by_sku.get(art)
        econ_current = calculate_ozon_ad_unit_economics(
            price=price,
            cogs_rub=cogs_val,
            actual_delivery_rub=actual_delivery_per_order,
            commission_rate_under_300=weighted_comm if weighted_comm > 0.0 else 0.23,
            drr_pct=drr_pct,
            fbs_dropoff_rub=weighted_pvz,
            overhead_per_order_rub=overhead_per_order,
        )
        low_comm_for_tiers = (
            weighted_comm
            if 0.0 < weighted_comm < _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)
            else 0.23
        )
        econ_299 = calculate_ozon_ad_unit_economics(
            price=299.0,
            cogs_rub=cogs_val,
            actual_delivery_rub=actual_delivery_per_order,
            commission_rate_under_300=low_comm_for_tiers,
            drr_pct=drr_pct,
            fbs_dropoff_rub=weighted_pvz,
            overhead_per_order_rub=overhead_per_order,
        )

        econ_over300: Optional[Dict[str, Optional[float]]] = None
        if cogs_val is not None and actual_delivery_per_order is not None and drr_pct is not None:
            high_comm = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)
            acq_r = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
            tax_r = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
            low_lim = _safe_nonneg(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0)
            remaining_share = 1.0 - high_comm - acq_r - tax_r - (_safe_nonneg(drr_pct) / 100.0)
            if remaining_share > 0.01:
                base_299_prof = econ_299["net_profit"] if econ_299["net_profit"] is not None else 0.0
                target_profit = max(base_299_prof + 25.0, 55.0)
                fixed_costs = cogs_val + actual_delivery_per_order + weighted_pvz + overhead_per_order
                over300_price = float(
                    max(int(low_lim) + 1, math.ceil((fixed_costs + target_profit + 0.02) / remaining_share))
                )
                econ_over300 = calculate_ozon_ad_unit_economics(
                    price=over300_price,
                    cogs_rub=cogs_val,
                    actual_delivery_rub=actual_delivery_per_order,
                    commission_rate_under_300=low_comm_for_tiers,
                    drr_pct=drr_pct,
                    fbs_dropoff_rub=weighted_pvz,
                    overhead_per_order_rub=overhead_per_order,
                )

        cat_meta = PRODUCTS_CATALOG.get(art, {})
        return {
            "sku": art,
            "ozon_sku": str(cat_meta.get("ozon_sku") or ""),
            "product_id": str(st_item.get("product_id") or st_item.get("external_id") or ""),
            "name": str(cat_meta.get("name") or f"Арт. {art}"),
            "current_price": price,
            "in_promo": bool(st_item.get("in_promo", False)),
            "stock_fbo": fbo_st,
            "stock_fbs": fbs_st,
            "total_stock": total_st,
            "daily_sales": daily_sales,
            "days_of_stock": days_of_stock,
            "unit_economics": econ_current,
            "tier_299_economics": econ_299,
            "tier_over300_economics": econ_over300,
        }

    # 3. Если Performance API вернул ошибку — не подменяем кампании и не смешиваем с органикой
    if campaigns_error is not None or raw_campaigns is None:
        unclassified = [
            build_sku_card(art, drr_pct=None)
            for art, st in stock_by_art.items()
            if max(_safe_int(st.get("stock_fbo"), 0), 0) + max(_safe_int(st.get("stock_fbs"), 0), 0) > 0
        ]
        return {
            "campaigns_available": False,
            "campaigns_error": str(campaigns_error or "Данные кампаний Ozon не получены"),
            "campaigns": [],
            "promoted_skus": [],
            "organic_items": [],
            "unclassified_items": unclassified,
            "overlapping_skus": [],
            "zero_stock_promoted_skus": [],
            "low_stock_promoted_skus": [],
            "finance": {
                "orders_count": oz_orders_cnt,
                "sales_total": oz_sales_total,
                "deliv_total": oz_deliv_total,
                "actual_delivery_per_order": actual_delivery_per_order,
                "dropoff_per_order": actual_dropoff_per_order,
                "overhead_per_order": overhead_per_order,
                "ads_total": ads_total,
                "promo_cpo": promo_cpo,
                "promo_cpc": promo_cpc,
                "store_tacos_pct": store_tacos_pct,
            },
        }

    # 4. Точный разбор каждой активной РК Ozon
    parsed_campaigns: List[Dict[str, Any]] = []
    sku_to_campaigns: Dict[str, List[str]] = {}
    promoted_set: Set[str] = set()
    zero_stock_promoted: Set[str] = set()
    low_stock_promoted: Set[str] = set()

    for camp in raw_campaigns:
        if not isinstance(camp, dict) or not _is_campaign_active(camp):
            continue

        cid = str(camp.get("id") or "").strip()
        title = str(camp.get("title") or (f"Кампания #{cid}" if cid else "Кампания Ozon")).strip()
        state = str(camp.get("state") or "ACTIVE").strip()

        raw_skus = _extract_raw_campaign_skus(camp)
        resolved_skus: List[str] = []
        seen_resolved: Set[str] = set()
        for raw_token in raw_skus:
            art = resolve_ozon_identifier(raw_token)
            if art and art not in seen_resolved:
                seen_resolved.add(art)
                resolved_skus.append(art)

        spend_raw = camp.get("spend_7d_rub")
        if spend_raw is None:
            spend_raw = camp.get("spend_rub", 0.0)
        rev_raw = camp.get("promo_revenue_7d_rub")
        if rev_raw is None:
            rev_raw = camp.get("revenue_rub", 0.0)
        drr_raw = camp.get("drr_in_promo_pct")
        if drr_raw is None:
            drr_raw = camp.get("drr_pct")

        spend_rub = round(_safe_nonneg(spend_raw), 2)
        revenue_rub = round(_safe_nonneg(rev_raw), 2)

        if revenue_rub > 0.0:
            drr_pct: Optional[float] = _round_pct(spend_rub, revenue_rub)
            drr_status = "calculated"
        elif spend_rub > 0.0:
            drr_pct = None
            drr_status = "spend_without_revenue"
        elif drr_raw is not None and _safe_nonneg(drr_raw) > 0.0:
            drr_pct = round(_safe_nonneg(drr_raw), 1)
            drr_status = "reported"
        else:
            drr_pct = 0.0
            drr_status = "zero"

        camp_items: List[Dict[str, Any]] = []
        for art in resolved_skus:
            promoted_set.add(art)
            sku_to_campaigns.setdefault(art, []).append(cid or title)
            card = build_sku_card(art, drr_pct=drr_pct)
            camp_items.append(card)
            if art in stock_by_art and card["total_stock"] <= 0:
                zero_stock_promoted.add(art)
            elif card["days_of_stock"] is not None and card["days_of_stock"] < 2.0:
                low_stock_promoted.add(art)

        parsed_campaigns.append(
            {
                "id": cid,
                "title": title,
                "state": state,
                "products_count": len(resolved_skus),
                "skus": resolved_skus,
                "raw_skus": raw_skus,
                "spend_7d_rub": spend_rub,
                "promo_revenue_7d_rub": revenue_rub,
                "drr_in_promo_pct": drr_pct,
                "drr_status": drr_status,
                "items": camp_items,
                "products_missing": len(resolved_skus) == 0,
                "source": str(camp.get("source") or "Ozon Performance API"),
            }
        )

    # 5. Строгое выделение органических SKU (в наличии и НЕ входят ни в одну активную РК)
    has_unlisted_campaign_products = any(c["products_missing"] for c in parsed_campaigns)
    organic_items: List[Dict[str, Any]] = []
    unclassified_items: List[Dict[str, Any]] = []

    for art, st in stock_by_art.items():
        total_st = max(_safe_int(st.get("stock_fbo"), 0), 0) + max(_safe_int(st.get("stock_fbs"), 0), 0)
        if total_st <= 0 or art in promoted_set:
            continue
        if has_unlisted_campaign_products:
            # Если хотя бы одна активная РК вернулась без списка товаров, не объявляем остальные SKU органикой
            unclassified_items.append(build_sku_card(art, drr_pct=None))
        else:
            organic_items.append(build_sku_card(art, drr_pct=0.0))

    overlapping_skus = sorted(sku for sku, camps in sku_to_campaigns.items() if len(camps) > 1)

    return {
        "campaigns_available": True,
        "campaigns_error": None,
        "campaigns": parsed_campaigns,
        "promoted_skus": sorted(promoted_set),
        "organic_items": organic_items,
        "unclassified_items": unclassified_items,
        "overlapping_skus": overlapping_skus,
        "zero_stock_promoted_skus": sorted(zero_stock_promoted),
        "low_stock_promoted_skus": sorted(low_stock_promoted),
        "finance": {
            "orders_count": oz_orders_cnt,
            "sales_total": oz_sales_total,
            "deliv_total": oz_deliv_total,
            "actual_delivery_per_order": actual_delivery_per_order,
            "dropoff_per_order": actual_dropoff_per_order,
            "overhead_per_order": overhead_per_order,
            "ads_total": ads_total,
            "promo_cpo": promo_cpo,
            "promo_cpc": promo_cpc,
            "store_tacos_pct": store_tacos_pct,
        },
    }


def _format_drr_label(camp: Dict[str, Any]) -> str:
    status = camp.get("drr_status")
    drr = camp.get("drr_in_promo_pct")
    if status == "spend_without_revenue":
        return "∞ (расход без заказов!)"
    if drr is None:
        return "н/д"
    return f"{float(drr):.1f}%"


def _format_unit_profit_brief(card: Dict[str, Any]) -> str:
    econ = card["unit_economics"]
    price = card["current_price"]
    if price <= 0.0:
        return "цена н/д"
    ad_rub = econ.get("ad_rub")
    net = econ.get("net_profit")
    be_drr = econ.get("breakeven_drr_pct")
    parts = [f"цена {price:.0f} ₽"]
    if ad_rub is not None and ad_rub > 0.0:
        parts.append(f"реклама -{ad_rub:.1f} ₽")
    if net is not None:
        parts.append(f"чистая прибыль {net:+.1f} ₽/шт.")
        if be_drr is not None:
            parts.append(f"безубыт. ДРР {be_drr:.1f}%")
    else:
        parts.append("прибыль н/д (нужны себестоимость и факт. доставка)")
    return ", ".join(parts)


def _render_factual_ads_header(
    oz_analysis: Dict[str, Any],
    wb_campaigns: Sequence[Dict[str, Any]],
    wb_fin: Optional[Dict[str, Any]],
    api_errors: Sequence[str],
) -> str:
    fin = oz_analysis["finance"]
    deliv_unit = fin["actual_delivery_per_order"]
    deliv_str = (
        f"<b>{deliv_unit:.2f} ₽/заказ</b> ({_fmt_money(fin['deliv_total'])} ₽ / {fin['orders_count']} шт.)"
        if deliv_unit is not None
        else "<i>н/д (нет заказов или данных доставки за период)</i>"
    )

    lines: List[str] = [
        "🔵 <b>ФАКТИЧЕСКИЙ СРЕЗ РЕКЛАМЫ OZON (7 дн.):</b>",
        f"• Выручка магазина: <b>{_fmt_money(fin['sales_total'])} ₽</b> | Заказов: <b>{fin['orders_count']} шт.</b>",
        f"• Фактическая логистика Ozon: {deliv_str}",
        (
            f"• Общие списания на рекламу (фин. отчёт): <b>{_fmt_money(fin['ads_total'])} ₽</b> "
            f"(TACoS {fin['store_tacos_pct']:.1f}% | CPO: {_fmt_money(fin['promo_cpo'])} ₽, CPC: {_fmt_money(fin['promo_cpc'])} ₽)"
        ),
    ]

    if not oz_analysis["campaigns_available"]:
        err_msg = html.escape(str(oz_analysis.get("campaigns_error") or "API недоступен"))
        lines.append(
            f"⚠️ <b>Состав кампаний Ozon не получен:</b> <code>{err_msg}</code>\n"
            f"<i>Фейковые кампании не подставляются; разделение на рекламные и органические SKU отложено до ответа Performance API.</i>"
        )
    elif not oz_analysis["campaigns"]:
        lines.append("• Активных рекламных кампаний в Ozon Performance API не обнаружено.")
    else:
        for idx, camp in enumerate(oz_analysis["campaigns"], 1):
            safe_title = html.escape(camp["title"])
            safe_id = html.escape(camp["id"]) if camp["id"] else "—"
            drr_lbl = _format_drr_label(camp)
            lines.append(
                f"\n🎯 <b>Кампания №{idx}: {safe_title}</b> (ID: <code>{safe_id}</code>)\n"
                f"• Товаров в РК: <b>{camp['products_count']} шт.</b> | ДРР в продвижении: <b>{drr_lbl}</b>\n"
                f"• Расход 7 дн.: <b>{_fmt_money(camp['spend_7d_rub'])} ₽</b> | "
                f"Выручка в РК: <b>{_fmt_money(camp['promo_revenue_7d_rub'])} ₽</b>"
            )
            if camp["products_missing"]:
                lines.append("  ⚠️ <i>API не вернул список SKU внутри кампании.</i>")
            for card in camp["items"]:
                safe_sku = html.escape(card["sku"])
                lines.append(
                    f"  ├ Арт. <code>{safe_sku}</code>: FBO {card['stock_fbo']} / FBS {card['stock_fbs']} шт. | "
                    f"{_format_unit_profit_brief(card)}"
                )

        if oz_analysis["organic_items"]:
            lines.append(
                f"\n🌿 <b>Органические SKU Ozon (вне рекламы — {len(oz_analysis['organic_items'])} шт., ДРР 0.0%):</b>"
            )
            for card in oz_analysis["organic_items"]:
                safe_sku = html.escape(card["sku"])
                lines.append(
                    f"  ├ Арт. <code>{safe_sku}</code>: FBO {card['stock_fbo']} / FBS {card['stock_fbs']} шт. | "
                    f"{_format_unit_profit_brief(card)}"
                )
        elif oz_analysis["unclassified_items"]:
            uncl_skus = ", ".join(html.escape(c["sku"]) for c in oz_analysis["unclassified_items"])
            lines.append(
                f"\n⚠️ <i>SKU с неподтверждённым статусом рекламы: <code>{uncl_skus}</code>.</i>"
            )

    if oz_analysis["overlapping_skus"]:
        ov_str = ", ".join(html.escape(s) for s in oz_analysis["overlapping_skus"])
        lines.append(f"\n🚨 <b>Пересечение SKU между кампаниями Ozon:</b> <code>{ov_str}</code>")
    if oz_analysis["zero_stock_promoted_skus"]:
        zs_str = ", ".join(html.escape(s) for s in oz_analysis["zero_stock_promoted_skus"])
        lines.append(f"🛑 <b>Нулевой остаток у товаров в рекламе Ozon:</b> <code>{zs_str}</code>")
    if oz_analysis["low_stock_promoted_skus"]:
        ls_str = ", ".join(html.escape(s) for s in oz_analysis["low_stock_promoted_skus"])
        lines.append(f"⚠️ <b>Остаток менее 2 дней в рекламе Ozon:</b> <code>{ls_str}</code>")

    # Блок Wildberries
    wb_fin_dict = wb_fin if isinstance(wb_fin, dict) else {}
    wb_rev = _safe_float(wb_fin_dict.get("revenue_seller"))
    wb_ads = _safe_nonneg(wb_fin_dict.get("ads"))
    wb_drr = _round_pct(wb_ads, wb_rev) if wb_rev > 0.0 else 0.0
    lines.append(
        f"\n🟣 <b>ФАКТИЧЕСКИЙ СРЕЗ РЕКЛАМЫ WB (7 дн.):</b>\n"
        f"• Выручка ЛК: <b>{_fmt_money(wb_rev)} ₽</b> | Списания ВБ.Продвижение: "
        f"<b>{_fmt_money(wb_ads)} ₽</b> (ДРР {wb_drr:.1f}%) | Активных/учтённых РК: <b>{len(wb_campaigns)}</b>"
    )

    if api_errors:
        lines.append("\n" + "\n".join(api_errors))

    return "\n".join(lines)


async def generate_weekly_ads_report_and_recommendations(ozon_api, wb_api) -> str:
    async def _fetch_campaigns_safe(api_obj) -> List[Dict[str, Any]]:
        if hasattr(api_obj, "fetch_active_ad_campaigns"):
            return await api_obj.fetch_active_ad_campaigns(days=7)
        return []

    async def _load_db_products() -> List[Product]:
        async with get_db_session() as session:
            res = await session.execute(select(Product))
            return list(res.scalars().all())

    (
        oz_stocks_res,
        wb_stocks_res,
        oz_fin_res,
        wb_fin_res,
        oz_camp_res,
        wb_camp_res,
        db_prods_res,
    ) = await asyncio.gather(
        ozon_api.fetch_stocks_and_prices(),
        wb_api.fetch_stocks_and_prices(),
        ozon_api.fetch_real_finance_breakdown(days=7),
        wb_api.fetch_real_finance_breakdown(days=7),
        _fetch_campaigns_safe(ozon_api),
        _fetch_campaigns_safe(wb_api),
        _load_db_products(),
        return_exceptions=True,
    )

    api_errors: List[str] = []

    oz_stocks: List[Dict[str, Any]] = []
    if isinstance(oz_stocks_res, Exception):
        api_errors.append(f"⚠️ Остатки/цены Ozon: <code>{html.escape(str(oz_stocks_res))}</code>")
    elif isinstance(oz_stocks_res, list):
        oz_stocks = oz_stocks_res

    wb_stocks: List[Dict[str, Any]] = []
    if isinstance(wb_stocks_res, Exception):
        api_errors.append(f"⚠️ Остатки/цены WB: <code>{html.escape(str(wb_stocks_res))}</code>")
    elif isinstance(wb_stocks_res, list):
        wb_stocks = wb_stocks_res

    oz_fin: Optional[Dict[str, Any]] = None
    if isinstance(oz_fin_res, Exception):
        api_errors.append(f"⚠️ Финансы Ozon: <code>{html.escape(str(oz_fin_res))}</code>")
    elif isinstance(oz_fin_res, dict):
        oz_fin = oz_fin_res

    wb_fin: Optional[Dict[str, Any]] = None
    if isinstance(wb_fin_res, Exception):
        api_errors.append(f"⚠️ Финансы WB: <code>{html.escape(str(wb_fin_res))}</code>")
    elif isinstance(wb_fin_res, dict):
        wb_fin = wb_fin_res

    oz_campaigns: Optional[List[Dict[str, Any]]] = None
    oz_campaigns_error: Optional[str] = None
    if isinstance(oz_camp_res, Exception):
        oz_campaigns_error = str(oz_camp_res)
    elif isinstance(oz_camp_res, list):
        oz_campaigns = oz_camp_res
    else:
        oz_campaigns_error = "Некорректный формат ответа Ozon Performance API"

    wb_campaigns: List[Dict[str, Any]] = []
    if isinstance(wb_camp_res, Exception):
        api_errors.append(f"⚠️ Реклама WB: <code>{html.escape(str(wb_camp_res))}</code>")
    elif isinstance(wb_camp_res, list):
        wb_campaigns = [c for c in wb_camp_res if isinstance(c, dict)]

    db_products: List[Product] = db_prods_res if isinstance(db_prods_res, list) else []

    oz_analysis = analyze_ozon_campaigns(
        raw_campaigns=oz_campaigns,
        oz_stocks=oz_stocks,
        oz_fin=oz_fin,
        db_products=db_products,
        campaigns_error=oz_campaigns_error,
    )

    factual_header = _render_factual_ads_header(
        oz_analysis=oz_analysis,
        wb_campaigns=wb_campaigns,
        wb_fin=wb_fin,
        api_errors=api_errors,
    )

    wb_summary: List[str] = []
    for item in wb_stocks:
        if not isinstance(item, dict):
            continue
        st_fbo = max(_safe_int(item.get("stock_fbo"), 0), 0)
        st_fbs = max(_safe_int(item.get("stock_fbs"), 0), 0)
        if st_fbo + st_fbs <= 0:
            continue
        raw_sku = str(item.get("sku") or "").strip()
        art = WB_SKU_TO_ART.get(raw_sku, raw_sku)
        nm_id = str(item.get("nm_id") or item.get("external_id") or "")
        cur_price = _safe_nonneg(item.get("current_price"))
        comm_pct = _safe_nonneg(item.get("commission_rate"), 0.25) * 100.0
        daily_sales = _safe_nonneg(item.get("daily_sales"))
        wb_summary.append(
            f"SKU {art} (nmID {nm_id}): цена {cur_price:.0f} ₽, комиссия WB={comm_pct:.1f}%, "
            f"FBO={st_fbo} шт., FBS={st_fbs} шт., продаж/день={daily_sales:.2f}"
        )

    prompt_payload_ozon = {
        "campaigns_available": oz_analysis["campaigns_available"],
        "campaigns_error": oz_analysis["campaigns_error"],
        "finance_7d": oz_analysis["finance"],
        "campaigns": [
            {
                "index": idx,
                "id": c["id"],
                "title": c["title"],
                "products_count": c["products_count"],
                "skus": c["skus"],
                "spend_7d_rub": c["spend_7d_rub"],
                "promo_revenue_7d_rub": c["promo_revenue_7d_rub"],
                "drr_in_promo_pct": c["drr_in_promo_pct"],
                "drr_status": c["drr_status"],
                "items": c["items"],
            }
            for idx, c in enumerate(oz_analysis["campaigns"], 1)
        ],
        "organic_skus_not_in_ads": oz_analysis["organic_items"],
        "unclassified_skus": oz_analysis["unclassified_items"],
        "overlapping_skus": oz_analysis["overlapping_skus"],
        "zero_stock_promoted_skus": oz_analysis["zero_stock_promoted_skus"],
        "low_stock_promoted_skus": oz_analysis["low_stock_promoted_skus"],
    }

    oz_json = json.dumps(prompt_payload_ozon, ensure_ascii=False, indent=2)
    wb_json = (
        json.dumps(wb_campaigns, ensure_ascii=False, indent=2)
        if wb_campaigns
        else "Активных РК WB со списаниями за 7 дней не найдено."
    )

    tax_pct = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06) * 100.0
    acq_pct = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015) * 100.0
    high_comm_pct = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52) * 100.0

    prompt = (
        "Ты — финансовый аналитик и performance-директор бренда YaSoKo "
        "(моделирующие гели, базы и топы для ногтей 15 г, ИП на УСН).\n\n"
        "СТРОГИЕ ФИНАНСОВЫЕ ПРАВИЛА (ЗАПРЕЩЕНО НАРУШАТЬ ИЛИ ВЫДУМЫВАТЬ ЦИФРЫ):\n"
        f"- Налог УСН = {tax_pct:.0f}%, эквайринг = {acq_pct:.1f}%, комиссия Ozon до 300 ₽ — из API (22–23%), "
        f"свыше 300 ₽ — {high_comm_pct:.0f}% (не советовать ловушку 301–450 ₽).\n"
        "- Используй ТОЛЬКО проверенные числа и списки SKU из JSON ниже. "
        "Если campaigns_available=false или поле равно null, прямо укажи, что данные API недоступны, "
        "и НЕ выдумывай состав кампаний, ДРР или прибыль.\n"
        "- Строго разделяй каждую активную кампанию Ozon между собой и НЕ смешивай продвигаемые SKU "
        "с органическими SKU из organic_skus_not_in_ads.\n\n"
        f"1. ПРОВЕРЕННЫЙ РАЗБОР КАМПАНИЙ, ОРГАНИКИ И ФИНАНСОВ OZON:\n{oz_json}\n\n"
        f"2. РЕКЛАМНЫЕ КАМПАНИИ WILDBERRIES:\n{wb_json}\n\n"
        "3. АКТИВНЫЕ ТОВАРЫ И ОСТАТКИ WILDBERRIES (FBO и FBS):\n"
        + ("\n".join(wb_summary) or "Нет подтвержденных остатков WB.")
        + "\n\n"
        "СОСТАВЬ КРАТКИЙ ПЛАН ДЕЙСТВИЙ ПО 4 РАЗДЕЛАМ БЕЗ ВОДЫ:\n"
        "1. Раздельный аудит каждой активной кампании Ozon (состав SKU, фактический ДРР в продвижении, "
        "поштучная прибыль против безубыточного ДРР, корректировка целевой ставки и защита остатков < 2 дней).\n"
        "2. Органические SKU Ozon вне рекламы (какие артикулы оставить в органике, а какие тестировать в РК при достаточном остатке).\n"
        "3. План по ВБ.Продвижению с учетом текущих остатков FBO/FBS и фактического ДРР.\n"
        "4. Контрольные точки проверки через 24 ч, 72 ч и 7 дней.\n"
        "Форматирование: только HTML-теги <b>, <i>, <code>. Не используй Markdown (**, ###) и таблицы."
    )

    ai_block = await _call_gemini_unlimited(prompt)
    return f"{factual_header}\n\n➖➖➖➖➖➖➖➖➖➖\n🧠 <b>РЕКОМЕНДАЦИИ ПО УПРАВЛЕНИЮ РЕКЛАМОЙ:</b>\n\n{ai_block}"
```

### `services/competitors/deep_parser.py`

```python
import asyncio
import html
import json
import logging
import math
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import settings
from services.common.orders_report import _mul_rub, _round_pct, _round_rub, _to_dec
from services.common.ad_recommendations import (
    _discover_models_via_proxy,
    sanitize_telegram_html,
)

logger = logging.getLogger(__name__)

WB_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
OZON_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
GEMINI_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=15, sock_connect=15, sock_read=70)
NATIVE_SOCKET_TIMEOUT_SEC = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 МБ — защита памяти хоста (4 ГБ RAM)

DEFAULT_COMPETITOR_COGS_RUB = 75.0  # Оценочная себестоимость сырья + баночка 15 г + этикетка
DEFAULT_OZON_ACTUAL_LOGISTICS_RUB = 39.0  # Фактическая эталонная логистика Ozon (~39 ₽/заказ)
DEFAULT_OZON_UNDER300_COMM_RATE = 0.23  # Льготная комиссия Ozon до 300 ₽ (22–23%)
DEFAULT_OZON_COINVEST_FACTOR = 0.90  # Оценочный коэффициент витрины Ozon к цене ЛК
DEFAULT_WB_LOGISTICS_RUB = 55.0  # Базовый тариф логистики WB для баночки 15 г
DEFAULT_WB_COMM_RATE = 0.25  # Базовая комиссия категории ногтевого сервиса WB
DEFAULT_WB_SPP_FACTOR = 0.82  # Оценочный коэффициент СПП WB (buyer_price / seller_price)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_PRICE_DIGITS_RE = re.compile(r"(\d[\d\s\u00a0\u2009]*(?:[.,]\d+)?)")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        cleaned = (
            value.strip()
            .replace("\u00a0", "")
            .replace("\u2009", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        if not cleaned:
            return default
        value = cleaned
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value: Any, default: int = 0) -> int:
    parsed = _safe_float(value, float(default))
    return int(parsed) if math.isfinite(parsed) else default


def _extract_price_number(raw: Any) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if math.isfinite(raw) and raw > 0.0 else 0.0
    if isinstance(raw, str):
        match = _PRICE_DIGITS_RE.search(raw)
        if match:
            val = _safe_float(match.group(1), 0.0)
            return val if val > 0.0 else 0.0
    return 0.0


async def _fetch_json_direct(
    url: str,
    headers: Dict[str, str],
    timeout: aiohttp.ClientTimeout,
) -> Tuple[Optional[Any], Optional[str], bool]:
    """
    Выполняет прямой HTTP GET (trust_env=False) с жесткими таймаутами, ограничением объема ответа
    и гарантированным закрытием aiohttp.ClientSession и TCPConnector при любом исходе.
    Возвращает: (json_data, error_message, is_transport_error).
    """
    if not url.startswith("https://"):
        return None, "Недопустимая схема URL (требуется https://)", False

    connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
    session: Optional[aiohttp.ClientSession] = None
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            timeout=timeout,
            headers=headers,
        ) as opened_session:
            session = opened_session
            async with session.get(url, allow_redirects=True, max_redirects=3) as resp:
                raw_bytes = await resp.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_bytes) > MAX_RESPONSE_BYTES:
                    return (
                        None,
                        f"HTTP {resp.status}: превышен лимит ответа ({MAX_RESPONSE_BYTES // 1024} КБ)",
                        False,
                    )
                if resp.status == 429:
                    return None, "HTTP 429 (превышен лимит запросов)", False
                if resp.status == 403:
                    return None, "HTTP 403 (доступ ограничен антибот-защитой)", False
                if resp.status != 200:
                    return None, f"HTTP {resp.status}", resp.status >= 500

                text = raw_bytes.decode("utf-8", errors="replace").strip()
                if not text:
                    return None, "Пустое тело ответа HTTP 200", False
                try:
                    return json.loads(text), None, False
                except json.JSONDecodeError as exc:
                    return None, f"Некорректный JSON ответа ({exc.msg})", False
    except asyncio.TimeoutError:
        return None, f"Таймаут запроса ({timeout.total} сек.)", True
    except aiohttp.ClientError as exc:
        return None, f"Сетевая ошибка aiohttp ({type(exc).__name__}: {exc})", True
    except OSError as exc:
        return None, f"Ошибка сокета ({type(exc).__name__}: {exc})", True
    finally:
        if session is not None and not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()


def _fetch_wb_native_sync(url: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Резервный прямой запрос к WB через стандартную библиотеку без системного прокси,
    с явным таймаутом сокета, ограничением размера ответа и корректной обработкой ошибок.
    """
    if not url.startswith("https://"):
        return [], "Недопустимая схема URL"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=NATIVE_SOCKET_TIMEOUT_SEC) as response:
            status = getattr(response, "status", 200)
            raw_bytes = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RESPONSE_BYTES:
                return [], f"HTTP {status}: превышен лимит ответа"
            if status != 200:
                return [], f"HTTP {status}"
            data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                return [], "Некорректная структура ответа WB"
            data_block = data.get("data")
            products = data_block.get("products") if isinstance(data_block, dict) else None
            if isinstance(products, list):
                return [p for p in products if isinstance(p, dict)], None
            return [], None
    except urllib.error.HTTPError as exc:
        return [], f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return [], f"URLError: {exc.reason}"
    except TimeoutError:
        return [], f"Таймаут резервного сокета ({NATIVE_SOCKET_TIMEOUT_SEC} сек.)"
    except json.JSONDecodeError as exc:
        return [], f"Некорректный JSON резервного ответа ({exc.msg})"
    except (OSError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"


def _extract_wb_product_price(product: Dict[str, Any]) -> float:
    sizes = product.get("sizes")
    if isinstance(sizes, list):
        for size in sizes:
            if not isinstance(size, dict):
                continue
            price_obj = size.get("price")
            if isinstance(price_obj, dict):
                for field in ("total", "product", "basic"):
                    cents = _safe_float(price_obj.get(field), 0.0)
                    if cents > 0.0:
                        return round(cents / 100.0, 2)
    for legacy_field in ("salePriceU", "priceU"):
        cents = _safe_float(product.get(legacy_field), 0.0)
        if cents > 0.0:
            return round(cents / 100.0, 2)
    return 0.0


async def _parse_wb_competitors_with_status(
    query: str,
    count: int = 15,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    clean_query = str(query or "").strip()
    safe_count = max(_safe_int(count, 15), 0)
    if not clean_query or safe_count == 0:
        return [], None

    encoded_query = urllib.parse.quote(clean_query)
    url = (
        "https://catalog.wb.ru/catalog/common/v2/search"
        f"?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={encoded_query}"
        "&resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false"
    )
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }

    raw_products: List[Dict[str, Any]] = []
    data, err_msg, is_transport_err = await _fetch_json_direct(url, headers, WB_HTTP_TIMEOUT)

    if err_msg is None and isinstance(data, dict):
        data_block = data.get("data")
        prods = data_block.get("products") if isinstance(data_block, dict) else None
        if isinstance(prods, list):
            raw_products = [p for p in prods if isinstance(p, dict)]
    elif is_transport_err:
        logger.warning("[Competitors WB] aiohttp error (%s), trying direct urllib fallback", err_msg)
        fallback_prods, fallback_err = await asyncio.to_thread(_fetch_wb_native_sync, url)
        if fallback_err is None:
            raw_products = fallback_prods
            err_msg = None
        else:
            err_msg = f"{err_msg}; fallback: {fallback_err}"
            logger.warning("[Competitors WB] fallback failed: %s", fallback_err)
    elif err_msg:
        logger.warning("[Competitors WB] request failed: %s", err_msg)

    items: List[Dict[str, Any]] = []
    seen_skus: set[str] = set()
    for prod in raw_products:
        if len(items) >= safe_count:
            break
        sku = str(prod.get("id") or "").strip()
        if not sku or sku in seen_skus:
            continue
        buyer_price = _extract_wb_product_price(prod)
        if buyer_price <= 0.0:
            continue

        raw_rating = (
            prod.get("reviewRating")
            if prod.get("reviewRating") is not None
            else prod.get("nmReviewRating")
        )
        rating = min(max(_safe_float(raw_rating, 0.0), 0.0), 5.0)
        feedbacks = max(_safe_int(prod.get("feedbacks"), 0), 0)
        name = str(prod.get("name") or "").strip() or f"Товар WB {sku}"
        brand = str(prod.get("brand") or "").strip() or "Без бренда"

        seen_skus.add(sku)
        items.append(
            {
                "mp": "WB",
                "sku": sku,
                "name": name,
                "brand": brand,
                "rating": round(rating, 1),
                "feedbacks": feedbacks,
                "buyer_price": buyer_price,
            }
        )

    # Не подмешиваем вымышленные карточки при неполной или недоступной выдаче WB.
    return items, err_msg


async def parse_wb_competitors(query: str, count: int = 15) -> List[Dict[str, Any]]:
    """Парсит реальные карточки конкурентов WB без подстановки фейковых товаров."""
    items, _ = await _parse_wb_competitors_with_status(query=query, count=count)
    return items


def _extract_ozon_items_from_payload(payload: Any, limit: int) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or limit <= 0:
        return []

    widget_states = payload.get("widgetStates")
    if not isinstance(widget_states, dict):
        return []

    items: List[Dict[str, Any]] = []
    seen_skus: set[str] = set()

    for key, raw_widget in widget_states.items():
        if len(items) >= limit:
            break
        key_low = str(key).lower()
        if "searchresults" not in key_low and "tilegrid" not in key_low:
            continue

        if isinstance(raw_widget, str):
            try:
                widget = json.loads(raw_widget)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw_widget, dict):
            widget = raw_widget
        else:
            continue

        raw_items = widget.get("items")
        if not isinstance(raw_items, list):
            continue

        for entry in raw_items:
            if len(items) >= limit:
                break
            if not isinstance(entry, dict):
                continue

            sku = str(entry.get("skuId") or entry.get("sku") or entry.get("id") or "").strip()
            if not sku or sku in seen_skus:
                continue

            buyer_price = _extract_price_number(entry.get("price") or entry.get("finalPrice"))
            title = str(entry.get("title") or entry.get("name") or "").strip()
            brand = str(entry.get("brand") or "").strip()
            rating = min(max(_safe_float(entry.get("rating"), 0.0), 0.0), 5.0)
            feedbacks = max(_safe_int(entry.get("reviewsCount") or entry.get("feedbacks"), 0), 0)

            for state in entry.get("mainState") or []:
                if not isinstance(state, dict):
                    continue
                atom = state.get("atom") if isinstance(state.get("atom"), dict) else state
                atom_type = str(atom.get("type") or "")
                if buyer_price <= 0.0 and "price" in atom_type.lower():
                    p_obj = atom.get("priceV2") or atom.get("price") or {}
                    if isinstance(p_obj, dict):
                        for p_el in p_obj.get("price") or []:
                            if isinstance(p_el, dict) and str(p_el.get("textStyle") or "") == "PRICE":
                                buyer_price = _extract_price_number(p_el.get("text"))
                                if buyer_price > 0.0:
                                    break
                        if buyer_price <= 0.0:
                            buyer_price = _extract_price_number(p_obj.get("text"))
                if not title and atom_type == "textAtom":
                    txt_atom = atom.get("textAtom")
                    if isinstance(txt_atom, dict) and txt_atom.get("text"):
                        title = str(txt_atom["text"]).strip()

            if buyer_price <= 0.0:
                continue

            seen_skus.add(sku)
            items.append(
                {
                    "mp": "OZON",
                    "sku": sku,
                    "name": title or f"Товар Ozon {sku}",
                    "brand": brand or "Без бренда",
                    "rating": round(rating, 1),
                    "feedbacks": feedbacks,
                    "buyer_price": round(buyer_price, 2),
                }
            )

    return items


async def _parse_ozon_competitors_with_status(
    query: str,
    count: int = 15,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    clean_query = str(query or "").strip()
    safe_count = max(_safe_int(count, 15), 0)
    if not clean_query or safe_count == 0:
        return [], None

    search_path = f"/search/?text={urllib.parse.quote(clean_query)}&from_global=true"
    url = (
        "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
        f"?url={urllib.parse.quote(search_path, safe='')}"
    )
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/",
    }

    data, err_msg, _ = await _fetch_json_direct(url, headers, OZON_HTTP_TIMEOUT)
    if err_msg is not None:
        logger.warning("[Competitors Ozon] live search unavailable: %s", err_msg)
        return [], err_msg

    items = _extract_ozon_items_from_payload(data, limit=safe_count)
    if not items:
        return [], "Витрина Ozon не вернула товарных карточек в JSON-выдаче"
    return items, None


async def parse_ozon_competitors(query: str, count: int = 15) -> List[Dict[str, Any]]:
    """
    Парсит реальную поисковую выдачу Ozon через aiohttp с таймаутами и закрытием сессии.
    При блокировке антиботом или сетевой ошибке возвращает пустой список без фейковых SKU.
    """
    items, _ = await _parse_ozon_competitors_with_status(query=query, count=count)
    return items


def calculate_competitor_unit_economy(
    buyer_price: float,
    mp: str,
    *,
    cogs: float = DEFAULT_COMPETITOR_COGS_RUB,
    ozon_logistics_rub: float = DEFAULT_OZON_ACTUAL_LOGISTICS_RUB,
    wb_logistics_rub: float = DEFAULT_WB_LOGISTICS_RUB,
) -> Dict[str, float]:
    """
    Оценочный расчет юнит-экономики конкурента на основе эталонных констант config.py:
    - Эквайринг: settings.ACQUIRING_RATE (1.5%)
    - Налог УСН: settings.TAX_RATE (6%)
    - Ozon: льготная комиссия 23% до 300 ₽ включительно, 52% свыше 300 ₽; логистика ~39 ₽/заказ
    - WB: комиссия категории 25%, базовая логистика 55 ₽, УСН 6% с цены покупателя (СПП)
    - Безопасная обработка нулевой/некорректной цены без деления на ноль.
    """
    clean_bp = round(max(_safe_float(buyer_price, 0.0), 0.0), 2)
    if clean_bp <= 0.0:
        return {
            "seller_price": 0.0,
            "buyer_price": 0.0,
            "commission": 0.0,
            "commission_rate": 0.0,
            "acquiring": 0.0,
            "logistics": 0.0,
            "mp_expenses": 0.0,
            "tax": 0.0,
            "cogs": 0.0,
            "profit": 0.0,
            "ros": 0.0,
        }

    acq_rate = max(_safe_float(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015), 0.0)
    tax_rate = max(_safe_float(getattr(settings, "TAX_RATE", 0.06), 0.06), 0.0)
    oz_low_limit = max(_safe_float(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0), 0.0)
    oz_high_comm = max(_safe_float(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52), 0.0)
    clean_cogs = round(max(_safe_float(cogs, DEFAULT_COMPETITOR_COGS_RUB), 0.0), 2)

    mp_norm = str(mp or "").strip().upper()
    if mp_norm == "WB":
        raw_seller = clean_bp / DEFAULT_WB_SPP_FACTOR
        seller_price = float(round(raw_seller)) if raw_seller >= 1.0 else _round_rub(raw_seller)
        comm_rate = DEFAULT_WB_COMM_RATE
        comm = _mul_rub(seller_price, comm_rate)
        logistics = _round_rub(max(_safe_float(wb_logistics_rub, DEFAULT_WB_LOGISTICS_RUB), 0.0))
        acq = _mul_rub(max(clean_bp, seller_price), acq_rate)
        tax_usn = _mul_rub(clean_bp, tax_rate)
    else:
        raw_seller = clean_bp / DEFAULT_OZON_COINVEST_FACTOR
        seller_price = float(round(raw_seller)) if raw_seller >= 1.0 else _round_rub(raw_seller)
        comm_rate = (
            DEFAULT_OZON_UNDER300_COMM_RATE if seller_price <= oz_low_limit else oz_high_comm
        )
        comm = _mul_rub(seller_price, comm_rate)
        logistics = _round_rub(
            max(_safe_float(ozon_logistics_rub, DEFAULT_OZON_ACTUAL_LOGISTICS_RUB), 0.0)
        )
        acq = _mul_rub(seller_price, acq_rate)
        tax_usn = _mul_rub(seller_price, tax_rate)

    mp_expenses = _round_rub(_to_dec(comm) + _to_dec(logistics) + _to_dec(acq))
    net_profit = _round_rub(
        _to_dec(seller_price) - _to_dec(clean_cogs) - _to_dec(mp_expenses) - _to_dec(tax_usn)
    )
    ros = _round_pct(net_profit, seller_price) if seller_price > 0.0 else 0.0

    return {
        "seller_price": seller_price,
        "buyer_price": clean_bp,
        "commission": comm,
        "commission_rate": comm_rate,
        "acquiring": acq,
        "logistics": logistics,
        "mp_expenses": mp_expenses,
        "tax": tax_usn,
        "cogs": clean_cogs,
        "profit": net_profit,
        "ros": ros,
    }


async def _call_gemini_competitor_verdict(prompt: str) -> str:
    """
    Запрашивает аналитический вердикт Gemini строго через локальный прокси (settings.LOCAL_PROXY_URL)
    с явными таймаутами и гарантированным закрытием aiohttp-сессии и коннектора.
    """
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return "⚠️ <i>GEMINI_API_KEY не задан — показаны только проверенные данные витрины.</i>"

    proxy_url = str(getattr(settings, "LOCAL_PROXY_URL", "") or "").strip()
    if not proxy_url:
        return "⚠️ <i>LOCAL_PROXY_URL не задан — прямой вызов Gemini отключён правилами безопасности.</i>"

    last_error = "нет ответа от моделей Gemini"
    connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
    session: Optional[aiohttp.ClientSession] = None
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            timeout=GEMINI_HTTP_TIMEOUT,
        ) as opened_session:
            session = opened_session
            models = await _discover_models_via_proxy(session, api_key, proxy_url)
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
            }
            for model_name in models:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model_name}:generateContent?key={api_key}"
                )
                try:
                    async with session.post(url, json=payload, proxy=proxy_url) as resp:
                        raw_text = await resp.text(encoding="utf-8", errors="replace")
                        if resp.status in (401, 403):
                            last_error = f"{model_name}: HTTP {resp.status}"
                            break
                        if resp.status != 200:
                            last_error = f"{model_name}: HTTP {resp.status}"
                            continue
                        data = json.loads(raw_text)
                        candidates = data.get("candidates") or []
                        if not candidates:
                            continue
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        text_out = "".join(
                            str(p.get("text") or "")
                            for p in parts
                            if isinstance(p, dict) and not p.get("thought", False)
                        ).strip()
                        if text_out:
                            return sanitize_telegram_html(text_out)
                except asyncio.TimeoutError:
                    last_error = f"{model_name}: таймаут запроса"
                    continue
                except (aiohttp.ClientError, json.JSONDecodeError, OSError) as exc:
                    last_error = f"{model_name}: {type(exc).__name__}"
                    continue
    except asyncio.TimeoutError:
        last_error = "таймаут подключения к локальному прокси"
    except (aiohttp.ClientError, OSError) as exc:
        last_error = f"ошибка прокси ({type(exc).__name__})"
    finally:
        if session is not None and not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()

    return f"⚠️ <i>ИИ-разбор временно недоступен ({html.escape(last_error)}).</i>"


async def run_full_competitor_deep_audit(category_query: str) -> Tuple[str, Dict[str, float]]:
    clean_query = str(category_query or "").strip()
    safe_query = html.escape(clean_query or "не задан")

    wb_res, oz_res = await asyncio.gather(
        _parse_wb_competitors_with_status(clean_query, count=15),
        _parse_ozon_competitors_with_status(clean_query, count=15),
        return_exceptions=True,
    )

    if isinstance(wb_res, Exception):
        wb_comps: List[Dict[str, Any]] = []
        wb_err: Optional[str] = f"{type(wb_res).__name__}: {wb_res}"
    else:
        wb_comps, wb_err = wb_res

    if isinstance(oz_res, Exception):
        oz_comps: List[Dict[str, Any]] = []
        oz_err: Optional[str] = f"{type(oz_res).__name__}: {oz_res}"
    else:
        oz_comps, oz_err = oz_res

    wb_prices = [float(c["buyer_price"]) for c in wb_comps if _safe_float(c.get("buyer_price")) > 0.0]
    oz_prices = [float(c["buyer_price"]) for c in oz_comps if _safe_float(c.get("buyer_price")) > 0.0]
    all_prices = wb_prices + oz_prices

    avg_wb = round(sum(wb_prices) / len(wb_prices), 2) if wb_prices else 0.0
    avg_oz = round(sum(oz_prices) / len(oz_prices), 2) if oz_prices else 0.0
    avg_total = round(sum(all_prices) / len(all_prices), 2) if all_prices else 0.0
    median_total = round(float(statistics.median(all_prices)), 2) if all_prices else 0.0

    prices_dict: Dict[str, float] = {
        "wb": avg_wb,
        "ozon": avg_oz,
        "total": avg_total,
        "median": median_total,
    }

    if not wb_comps and not oz_comps:
        diag_lines = [
            f"📊 <b>Анализ конкурентов: «{safe_query}»</b>\n",
            "⚠️ <b>Не удалось получить реальные карточки с витрин (фейковые данные отключены):</b>",
            f"• Wildberries: <code>{html.escape(wb_err or 'карточки не найдены')}</code>",
            f"• Ozon: <code>{html.escape(oz_err or 'карточки не найдены')}</code>",
        ]
        return "\n".join(diag_lines), prices_dict

    def _format_comp_lines(comps: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
        html_rows: List[str] = []
        plain_rows: List[str] = []
        for comp in comps:
            econ = calculate_competitor_unit_economy(float(comp["buyer_price"]), str(comp["mp"]))
            mp_lbl = html.escape(str(comp["mp"]))
            sku_lbl = html.escape(str(comp["sku"]))
            brand_lbl = html.escape(str(comp["brand"]))
            raw_name = str(comp["name"])
            short_name = (raw_name[:28] + "...") if len(raw_name) > 28 else raw_name
            name_lbl = html.escape(short_name)

            html_rows.append(
                f"• [{mp_lbl}] Арт. <code>{sku_lbl}</code> | <b>{brand_lbl}</b> — {name_lbl}\n"
                f"   Витрина: <b>{econ['buyer_price']:.0f} ₽</b> (оценка ЛК ~{econ['seller_price']:.0f} ₽) | "
                f"Расходы МП (с экв. 1.5%): -{econ['mp_expenses']:.0f} ₽ | УСН 6%: -{econ['tax']:.0f} ₽ | "
                f"Прибыль: <code>{econ['profit']:+.0f} ₽</code> (ROS {econ['ros']:.1f}%)"
            )
            plain_rows.append(
                f"[{comp['mp']}] SKU {comp['sku']} | {comp['brand']} — {short_name} | "
                f"витрина {econ['buyer_price']:.0f}₽, ЛК ~{econ['seller_price']:.0f}₽, "
                f"комиссия {econ['commission_rate']*100:.0f}% (-{econ['commission']:.0f}₽), "
                f"логистика -{econ['logistics']:.0f}₽, эквайринг -{econ['acquiring']:.1f}₽, "
                f"УСН -{econ['tax']:.1f}₽, прибыль {econ['profit']:+.0f}₽ (ROS {econ['ros']:.1f}%), "
                f"рейтинг {comp['rating']}, отзывов {comp['feedbacks']}"
            )
        return html_rows, plain_rows

    wb_html, wb_plain = _format_comp_lines(wb_comps)
    oz_html, oz_plain = _format_comp_lines(oz_comps)

    wb_price_str = f"<b>{avg_wb:.0f} ₽</b> ({len(wb_comps)} шт.)" if wb_comps else "<i>н/д (нет данных витрины)</i>"
    oz_price_str = f"<b>{avg_oz:.0f} ₽</b> ({len(oz_comps)} шт.)" if oz_comps else "<i>н/д (нет данных витрины)</i>"

    wb_section = (
        "\n".join(wb_html[:7])
        if wb_html
        else f"<i>Данные WB недоступны: <code>{html.escape(wb_err or 'пустая выдача')}</code></i>"
    )
    oz_section = (
        "\n".join(oz_html[:7])
        if oz_html
        else f"<i>Данные Ozon недоступны: <code>{html.escape(oz_err or 'пустая выдача')}</code> (фейковые карточки не подставляются)</i>"
    )

    sample_for_prompt = "\n".join([*wb_plain[:7], *oz_plain[:7]])
    wb_prompt_stat = f"{avg_wb:.0f} ₽ (карточек: {len(wb_comps)})" if wb_comps else f"недоступно ({wb_err})"
    oz_prompt_stat = f"{avg_oz:.0f} ₽ (карточек: {len(oz_comps)})" if oz_comps else f"недоступно ({oz_err})"

    prompt = (
        f"Ты — коммерческий директор бренда YaSoKo (гели для ногтей 15 г, производство г. Иваново, ИП УСН 6%).\n"
        f"Проведи анализ реально собранных карточек конкурентов по запросу «{clean_query}» "
        f"(всего получено: {len(wb_comps) + len(oz_comps)} шт.).\n\n"
        f"ФАКТИЧЕСКАЯ ЦЕНОВАЯ СТАТИСТИКА ВИТРИНЫ:\n"
        f"- Средняя цена Wildberries: {wb_prompt_stat}\n"
        f"- Средняя цена Ozon: {oz_prompt_stat}\n"
        f"- Средняя по выборке: {avg_total:.0f} ₽ | Истинная медиана: {median_total:.0f} ₽\n\n"
        f"РЕАЛЬНАЯ ВЫБОРКА КОНКУРЕНТОВ (оценочная себестоимость {DEFAULT_COMPETITOR_COGS_RUB:.0f} ₽, "
        f"логистика Ozon ~{DEFAULT_OZON_ACTUAL_LOGISTICS_RUB:.0f} ₽, эквайринг 1.5%, УСН 6%):\n"
        f"{sample_for_prompt}\n\n"
        "НАШИ ВВОДНЫЕ YASOKO:\n"
        "- Двойная жесткость: пластичный в тонком слое (без сколов), жесткий в толстом (держит углы квадрата без усадки).\n"
        "- Средне-вязкая консистенция (не затекает на валики), HEMA FREE, 11-FREE.\n"
        "- Тарифы Ozon: до 300 ₽ комиссия 22–23%, свыше 300 ₽ комиссия 52% (избегать ценовой ловушки 301–450 ₽).\n\n"
        "ТРЕБОВАНИЯ К ОТВЕТУ:\n"
        "- Без приветствий и воды. Не выдумывай данные по площадке, если она помечена как недоступная.\n"
        "- 1. Ценовое позиционирование Ozon (299 ₽ против выхода за порог 300 ₽ с комиссией 52%) и WB (с учетом СПП).\n"
        "- 2. Три незакрытые боли мастеров маникюра при выборе карточки в этой ценовой группе.\n"
        "- 3. Три конкретных шага по улучшению главного фото и второго слайда YaSoKo.\n"
        "- Используй только HTML-теги <b>, <i>, <code>, без Markdown (**, ###) и таблиц."
    )

    ai_verdict = await _call_gemini_competitor_verdict(prompt)

    total_found = len(wb_comps) + len(oz_comps)
    report_text = (
        f"📊 <b>Анализ конкурентов ({total_found} карточек): «{safe_query}»</b>\n"
        f"ℹ️ <i>Цены ЛК, СПП и себестоимость конкурентов ({DEFAULT_COMPETITOR_COGS_RUB:.0f} ₽) — расчётная оценка; "
        f"эквайринг 1.5%, УСН 6%, логистика Ozon ~{DEFAULT_OZON_ACTUAL_LOGISTICS_RUB:.0f} ₽.</i>\n\n"
        f"💵 <b>Цены на витринах:</b>\n"
        f"• Wildberries: {wb_price_str}\n"
        f"• Ozon: {oz_price_str}\n"
        f"• Средняя по выборке: <b>{avg_total:.0f} ₽</b> | Рыночная медиана: <b>{median_total:.0f} ₽</b>\n\n"
        f"<b>Выборка Wildberries:</b>\n{wb_section}\n\n"
        f"<b>Выборка Ozon:</b>\n{oz_section}\n\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🧠 <b>Аналитический вердикт и закрытие болей:</b>\n\n"
        f"{ai_verdict}"
    )

    return report_text, prices_dict
```
