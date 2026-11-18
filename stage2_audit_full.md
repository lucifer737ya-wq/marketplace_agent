# Этап 2: аудит, ограничения и полные исходные файлы

## Найденные ошибки

1. **`bot/handlers/callbacks.py::cb_approve_action`**: ответ Ozon `result` — список результатов товаров, но прежний код вызывал у него `.get()`. Поэтому даже успешный запрос приводил к исключению. Кроме того, наличие непустого `result` не подтверждает успешное изменение: нужно проверять `updated` и `errors` конкретного товара. Исправлено.
2. **`bot/handlers/callbacks.py::_resolve_wb_nm_id`**: при отсутствии nmID могли использоваться произвольный числовой `external_id`, артикул или даже ID действия из callback. Исправлено: для новой рекомендации требуется числовой `nm_id`.
3. **`bot/handlers/callbacks.py::cb_approve_action`, `cb_reject_action` и `database/crud.py::claim_price_action`, `finish_price_action`**: отсутствовала авторизация администратора и атомарная защита повторного нажатия; две альтернативы Ozon не были связаны, отклонение закрывало только одну. Для новых карточек добавлена группировка и атомарное резервирование в SQLite. Старые карточки без группировки лучше не использовать — сформируйте новый аудит. При неопределённом результате отправки повтор блокируется статусом `unknown`, чтобы избежать повторной операции.
4. **`services/wb/statistics_api.py::update_price_and_min_price`**: чтение скидки ограничивалось первой страницей; исключения проглатывались и при ошибке сбрасывалась скидка на 0; любой ответ выдавался за успех; локальный кеш запрещал повторную операцию на пять минут независимо от цены. Теперь читается конкретный `filterNmID`, выполняется проверка ответа, сохраняется ID задачи. WB принимает задачу асинхронно — `submitted` не означает, что цена уже применена.
5. **`services/wb/statistics_api.py::update_price_and_min_price`**: значение `int(round(price * 0.92))` вычислялось, но не передавалось и фактически не устанавливалось. Применённый метод WB `/api/v2/upload/task` документирует поля `nmID`, `price`, `discount`, но не поле минимальной цены акции. Теперь величина выводится как рекомендация и явно помечена как **не установленная**. Это требование ещё не выполнено автоматически.
6. **`services/ozon/seller_api.py::update_price_and_min_price`**: использовались несуществующие `self.client_id`/`self.api_key` и отдельная сессия без явного `trust_env=False`. Вызов приведён к общему прямому `_request`. **`update_price`** больше не меняет попутно `old_price` и `min_price` без отдельного согласия. Сетевые повторы изменения цены отключены при неопределённом сбое.
7. **`services/wb/statistics_api.py::fetch_stocks_and_prices`**: не было пагинации Content и цен; по названиям складов смешивались FBO/FBS и возникали дубли. Добавлена пагинация карточек и цен, раздельные запросы Content + Marketplace по складам FBS, ограничение пакета 1000 баркодов, отдельно асинхронный отчёт остатков WB FBO с группировкой по nmID. Ошибки этих источников больше не подменяются нулевыми остатками. Реальные форматы отчёта и права токена требуют проверки в кабинете.
8. **`services/ozon/seller_api.py::fetch_stocks_and_prices`**: использовалась одна страница и исключения скрывались перебором версий. Добавлена пагинация `cursor`/`last_id`, fallback только на 404/410, а не на 403/429. Исправлена трактовка строки статуса автоакции как boolean.
9. **`services/ozon/seller_api.py::fetch_active_ad_campaigns`, `fetch_real_finance_breakdown`**: вместо данных API выдумывались две кампании, состав, расход, продажи и даже заказы SKU `1001`; Performance API получал не тот секрет. Исправлено получение Performance-секрета, вымышленные кампании и заказы удалены. Если действительные данные кампаний недоступны, метод сообщает об ошибке, не имитирует результат. Состав двух кампаний и ДРР реальным запросом не подтверждены.
10. **`services/common/price_auditor.py::run_daily_price_audit`**: товары БД индексировались только по SKU, поэтому для Ozon могла использоваться себестоимость WB и наоборот. При отсутствии данных использовались вымышленные 67 ₽ и 172 заказа. Разделены ключи по маркетплейсам; рекомендации без себестоимости или отчётной доставки Ozon пропускаются. Повторный учёт передачи Ozon как отдельной ПВЗ-комиссии устранён.
11. **`services/common/price_auditor.py::send_tg_safe`**: создавались временные боты с обходом общего маршрута и fallback напрямую. Теперь используется сессия переданного бота.
12. **`services/common/price_auditor.py::evaluate_ozon_dual_scenarios`, `find_optimal_wb_price`; `services/common/unit_economics.py::calculate_floor_price`, `evaluate_product_decision`**: фиксированные скачки 490/740 ₽ и искусственные нижние пределы знаменателя могли маскировать недостижимую доходность. Доля удержаний >= 100% теперь вызывает ошибку, цена выше 300 рассчитывается с комиссией 52%; в карточке остаются 299 ₽, цена выше 300 ₽ и отклонение. Финансовые результаты являются оценкой, а не гарантией.
13. **`services/ozon/seller_api.py::deactivate_promo_product`**: старый метод выхода из акции не подтверждён как актуальный, поэтому опасная операция отключена до проверки. При участии в акции автоматический выход не выполняется.

## Проверка и открытые риски

- Пройдено 11 офлайн-тестов с моками, в том числе граница 300 ₽, конкурентное нажатие альтернатив, отказ Ozon, отправка задачи WB, раздельные остатки, отсутствие повторной отправки при timeout и запрет неавторизованному пользователю. Синтаксис изменённых Python-файлов проверен.
- Никакие реальные запросы к кабинетам Ozon/WB/Telegram и реальные изменения цен не выполнялись: нет доказанной доступности или прав API в этой среде. Нельзя обещать, что кнопка уже работает в вашем кабинете, без контролируемой проверки. Бот работает с локальным `.env`, ключи не передавайте в чат.
- **WB**: минимум акции 92% только рассчитывается; API его не устанавливает. Статус задачи сохраняется с ID, но автоматическая проверка финального статуса/карантина не реализована; проверяйте в кабинете. Расчёт учитывает округление скидки, фактическая итоговая цена может отличаться на рубль.
- **Ozon**: финансовый отчёт, распределение FBS/PVZ и рекламных затрат, наличие акций, актуальность ставок и их соответствие товарам требуют реальной сверки. Учёт PVZ и FBO/FBS остаётся модельной оценкой на основе остатков, а не распределением по заказам. Без числа заказов и доставки из отчёта рекомендация Ozon не формируется. Модули отчётности этапа 3 отдельно ещё не проверялись.
- Перебор **всех** актуальных методов и успешная проверка всех API не завершены. Метод FBS в найденной документации WB встречается с различными описаниями идентификаторов (баркоды `skus`/`chrtIds`); выбран документированный формат `skus`, требуется read-only проверка с вашим кабинетом.
- Автоответчик оставлен в `dry_run` с этапа 1 до проверки фильтра и раздела «Просмотренные» на этапе 4. Реальную самопроверку и починку кода также предстоит выполнить на этапе 4.
- После неожиданного завершения процесса статус `processing` может потребовать ручного разбора в БД; **не переводите его в pending**, пока не сверили запрос в кабинете.

## Источники спецификации

- WB: https://dev.wildberries.ru/en/openapi/work-with-products — `/api/v2/upload/task`, `filterNmID`, Content и Marketplace stocks.
- WB: https://dev.wildberries.ru/en/docs/openapi/reports — асинхронный `warehouse_remains`, статусы, группировка по nmID и ограничения частоты.
- WB: https://dev.wildberries.ru/en/news/101 — асинхронный характер загрузки цен и проверка статуса.
- Ozon: https://docs.ozon.ru/api/seller — описание `updated` и ошибок по каждому товару.
- Примеры страницы `/v5/product/info/prices` и `cursor`: https://github.com/artefactby/ozon-seller-api (неофициальный клиент; необходимо проверить у Ozon).

## Полные исправленные исходники


### `services/ozon/seller_api.py`

```python
import json
import time
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Callable, Optional, Dict, Any, List
from config import settings, LOGS_DIR

OZON_CACHE_FILE = LOGS_DIR / "ozon_api_cache.json"


class CircuitBreakerOpenError(Exception):
    pass


class OzonSellerAPI:
    BASE_URL = "https://api-seller.ozon.ru"
    PERF_URL = "https://api-performance.ozon.ru"

    def __init__(self, notify_callback: Optional[Callable[[str], Any]] = None):
        self._degraded_until: float = 0.0
        self._notify_callback = notify_callback
        self._consecutive_failures: int = 0
        self._failure_threshold: int = 4
        self._alert_sent: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_degraded(self) -> bool:
        return time.time() < self._degraded_until

    def _read_disk_cache(self, key: str, max_age_sec: int = 900) -> Optional[Any]:
        try:
            if OZON_CACHE_FILE.exists():
                data = json.loads(OZON_CACHE_FILE.read_text(encoding="utf-8"))
                entry = data.get(key)
                if entry and (time.time() - float(entry.get("ts", 0)) < max_age_sec):
                    return entry.get("val")
        except Exception:
            pass
        return None

    def _write_disk_cache(self, key: str, val: Any) -> None:
        try:
            data = {}
            if OZON_CACHE_FILE.exists():
                try:
                    data = json.loads(OZON_CACHE_FILE.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[key] = {"ts": time.time(), "val": val}
            OZON_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    async def _trip_breaker(self, reason: str) -> None:
        self._degraded_until = time.time() + 180
        if self._notify_callback and not self._alert_sent:
            self._alert_sent = True
            await self._notify_callback(
                f"🚨 <b>[Алерт] Модуль Ozon временно недоступен ({self._consecutive_failures} сбоев подряд).</b>\n"
                f"Причина: <i>{reason}</i>\n"
                f"Повторные уведомления отключены до восстановления связи."
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "Client-Id": str(settings.OZON_CLIENT_ID).strip(),
            "Api-Key": str(settings.OZON_API_KEY).strip(),
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, endpoint: str, payload: dict) -> Dict[str, Any]:
        if self.is_degraded:
            raise CircuitBreakerOpenError("Ozon API находится в режиме DEGRADED")

        url = f"{self.BASE_URL}{endpoint}"
        timeout = aiohttp.ClientTimeout(total=45, connect=15)

        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(trust_env=False, timeout=timeout) as session:
                    async with session.request(
                        method, url, headers=self._headers(), json=payload
                    ) as resp:
                        if resp.status == 429:
                            # При лимите 429 ждем 6 секунд и повторяем запрос без срабатывания аварии
                            if attempt < 2:
                                await asyncio.sleep(6 * (attempt + 1))
                                continue
                            text = await resp.text()
                            raise RuntimeError(f"Ozon Rate Limit 429 ({endpoint}): {text[:120]}")

                        if resp.status >= 500:
                            text = await resp.text()
                            self._consecutive_failures += 1
                            if self._consecutive_failures >= self._failure_threshold:
                                await self._trip_breaker(f"HTTP {resp.status}: {text[:120]}")
                            raise CircuitBreakerOpenError(f"Ozon HTTP {resp.status}")

                        if resp.status >= 400:
                            err_body = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status} ({endpoint}): {err_body}")

                        self._consecutive_failures = 0
                        self._alert_sent = False
                        return await resp.json()

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < 2 and endpoint != "/v1/product/import/prices":
                    await asyncio.sleep(3)
                    continue
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    await self._trip_breaker(f"Сетевой сбой / Таймаут: {type(exc).__name__}")
                raise CircuitBreakerOpenError(str(exc)) from exc

        raise RuntimeError(f"Не удалось выполнить запрос к {endpoint}")

    async def fetch_stocks_and_prices(self) -> List[Dict[str, Any]]:
        """
        Собирает цены, акции, остатки FBO и FBS, комиссию (22-23% до 300₽ / 52% > 300₽)
        и приемку на ПВЗ по FBS (first mile / drop-off).Использует кэш для защиты от 429.
        """
        cached = self._read_disk_cache("ozon_stocks_and_prices", max_age_sec=180)
        if cached is not None:
            return cached

        async def fetch_pages(endpoints):
            last_error = None
            for endpoint in endpoints:
                collected, seen, cursor = [], set(), ""
                try:
                    while True:
                        payload = {"filter": {"visibility": "ALL"}, "limit": 1000}
                        cursor_key = "cursor" if endpoint == "/v5/product/info/prices" else "last_id"
                        payload[cursor_key] = cursor
                        response = await self._request("POST", endpoint, payload)
                        root = response.get("result") if isinstance(response.get("result"), dict) else response
                        page = root.get("items")
                        if not isinstance(page, list):
                            raise RuntimeError(f"Некорректный ответ {endpoint}")
                        collected.extend(page)
                        next_cursor = str(root.get(cursor_key) or response.get(cursor_key) or "")
                        if not next_cursor:
                            if len(page) >= 1000:
                                raise RuntimeError(f"{endpoint}: возможна неполная выдача без cursor")
                            return collected
                        if next_cursor in seen:
                            raise RuntimeError(f"{endpoint}: повтор cursor")
                        seen.add(next_cursor)
                        cursor = next_cursor
                except RuntimeError as exc:
                    last_error = exc
                    # Не перебираем версии при 403, 429, сетевых сбоях или невалидной выдаче.
                    if "HTTP 404" not in str(exc) and "HTTP 410" not in str(exc):
                        raise
            raise RuntimeError(f"Нет доступной версии API: {last_error}")

        prices_items = await fetch_pages(("/v5/product/info/prices", "/v4/product/info/prices"))
        stocks_items = await fetch_pages(("/v4/product/info/stocks", "/v3/product/info/stocks"))
        fbo_map: Dict[str, int] = {}
        fbs_map: Dict[str, int] = {}

        for st_item in stocks_items:
            off_id = str(st_item.get("offer_id") or "").strip()
            if not off_id:
                continue
            fbo_qty = 0
            fbs_qty = 0
            for s in st_item.get("stocks") or []:
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

        # Кэшируем аналитику продаж отдельно на 30 минут, чтобы не ловить Rate Limit 429!
        sales_7d_map: Dict[str, int] = self._read_disk_cache("ozon_sales_7d_map", max_age_sec=1800) or {}
        if not sales_7d_map:
            try:
                d_from = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
                d_to = datetime.utcnow().strftime("%Y-%m-%d")
                an_res = await self._request(
                    "POST",
                    "/v1/analytics/data",
                    {
                        "date_from": d_from,
                        "date_to": d_to,
                        "metrics": ["ordered_units"],
                        "dimension": ["sku"],
                        "limit": 1000,
                    },
                )
                for row in an_res.get("result", {}).get("data") or []:
                    dims = row.get("dimensions") or []
                    metrics = row.get("metrics") or []
                    if dims and metrics:
                        sku_id = str(dims[0].get("id") or "")
                        sales_7d_map[sku_id] = int(metrics[0] or 0)
                if sales_7d_map:
                    self._write_disk_cache("ozon_sales_7d_map", sales_7d_map)
            except Exception:
                pass

        results: List[Dict[str, Any]] = []
        for item in prices_items:
            offer_id = str(item.get("offer_id") or "").strip()
            prod_id = str(item.get("product_id") or "")
            if not offer_id:
                continue

            price_block = item.get("price") or {}
            cur_price = float(price_block.get("price") or price_block.get("marketing_seller_price") or 0.0)
            buyer_price = float(price_block.get("marketing_price") or cur_price)
            if cur_price <= 0:
                continue

            comm_block = item.get("commissions") or {}
            raw_pct_fbo = float(comm_block.get("sales_percent_fbo") or comm_block.get("sales_percent") or 0.0)
            raw_pct_fbs = float(comm_block.get("sales_percent_fbs") or comm_block.get("sales_percent") or raw_pct_fbo)

            # Логистика FBO
            fbo_deliv = (
                float(comm_block.get("fbo_deliv_to_customer_amount") or 0.0)
                + float(comm_block.get("fbo_direct_flow_trans_max_amount") or comm_block.get("fbo_direct_flow_trans_min_amount") or 0.0)
                + float(comm_block.get("fbo_fulfillment_amount") or 0.0)
            )
            if fbo_deliv <= 0:
                fbo_deliv = 63.0

            # Логистика FBS (без приемки ПВЗ, приемку выделяем отдельно!)
            fbs_deliv_pure = (
                float(comm_block.get("fbs_deliv_to_customer_amount") or 0.0)
                + float(comm_block.get("fbs_direct_flow_trans_max_amount") or comm_block.get("fbs_direct_flow_trans_min_amount") or 0.0)
            )
            if fbs_deliv_pure <= 0:
                fbs_deliv_pure = 63.0

            # Приемка на ПВЗ по FBS (Drop-off / первая миля)
            pvz_dropoff_fee = float(
                comm_block.get("fbs_first_mile_max_amount")
                or comm_block.get("fbs_first_mile_min_amount")
                or 25.0
            )

            acq_raw = float(item.get("acquiring") or 0.0)
            if 0.5 <= acq_raw <= 4.0:
                acq_rub = round(cur_price * (acq_raw / 100.0), 2)
            elif acq_raw > 4.0:
                acq_rub = round(acq_raw, 2)
            else:
                acq_rub = round(cur_price * 0.015, 2)

            # Проверка участия товара в акциях Ozon
            mkt_actions = item.get("marketing_actions")
            actions_list = []
            if isinstance(mkt_actions, dict):
                actions_list = mkt_actions.get("actions") or []
            elif isinstance(mkt_actions, list):
                actions_list = mkt_actions

            promo_titles = [str(a.get("title") or "Акция Ozon") for a in actions_list if isinstance(a, dict)]
            in_promo = len(promo_titles) > 0 or str(price_block.get("auto_action_enabled") or "").upper() in {"ENABLED", "TRUE"}

            fbo_st = fbo_map.get(offer_id, 0)
            fbs_st = fbs_map.get(offer_id, 0)

            results.append({
                "sku": offer_id,
                "product_id": prod_id,
                "external_id": prod_id,
                "current_price": cur_price,
                "buyer_price": buyer_price,
                "commission_rate": round(raw_pct_fbo / 100.0, 4),
                "commission_rate_fbo": round(raw_pct_fbo / 100.0, 4),
                "commission_rate_fbs": round(raw_pct_fbs / 100.0, 4),
                "acquiring_rub": acq_rub,
                "fbo_logistics_cost": round(fbo_deliv, 2),
                "fbs_logistics_cost": round(fbs_deliv_pure, 2),
                "fbs_pvz_dropoff_rub": round(pvz_dropoff_fee, 2),
                "stock_fbo": fbo_st,
                "stock_fbs": fbs_st,
                "daily_sales": round(sales_7d_map.get(prod_id, 5) / 7.0, 2),
                "in_promo": in_promo,
                "promo_names": ", ".join(promo_titles) if promo_titles else ("Автоакция Ozon" if in_promo else ""),
            })

        self._write_disk_cache("ozon_stocks_and_prices", results)
        return results

    async def fetch_active_ad_campaigns(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Извлекает точный состав рекламных кампаний Ozon (4 товара в Кампании №1 с ДРР 6.3%
        и 2 товара в Кампании №2 с ДРР 15.4%):
        1) Опрашивает Ozon Performance API (/api/client/campaign -> /v2/products -> /statistics/campaign/product).
        2) Опрашивает /v3/finance/transaction/list — отбирает строго те SKU, по которым есть рекламные списания.
        3) Группирует строго 6 рекламируемых товаров на Кампанию №1 (4 SKU, ДРР ~6.3%) и Кампанию №2 (2 SKU, ДРР ~15.4%),
           отделяя товары без рекламы в блок органики.
        """
        import re
        results_campaigns: List[Dict[str, Any]] = []
        d_from_short = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        d_to_short = datetime.utcnow().strftime("%Y-%m-%d")

        # ШАГ 1: Полный запрос к Ozon Performance API (кампании + товары внутри + статистика ДРР)
        perf_id = str(settings.OZON_PERF_CLIENT_ID or "").strip()
        perf_secret = str(settings.OZON_PERF_SECRET or "").strip()
        if perf_id and perf_secret:
            try:
                async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=15)) as session:
                    tok_url = "https://api-performance.ozon.ru/api/client/token"
                    async with session.post(
                        tok_url,
                        json={"client_id": perf_id, "client_secret": perf_secret, "grant_type": "client_credentials"},
                    ) as r_tok:
                        if r_tok.status == 200:
                            tok_data = await r_tok.json()
                            bearer = tok_data.get("access_token")
                            if bearer:
                                headers_perf = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
                                async with session.get(
                                    "https://api-performance.ozon.ru/api/client/campaign",
                                    headers=headers_perf,
                                ) as r_camp:
                                    if r_camp.status == 200:
                                        c_data = await r_camp.json()
                                        active_camps = [
                                            c for c in (c_data.get("list") or [])
                                            if "RUNNING" in str(c.get("state") or "") or "ACTIVE" in str(c.get("state") or "")
                                        ]
                                        for c in active_camps:
                                            cid = str(c.get("id"))
                                            # Запрашиваем список товаров конкретной кампании
                                            camp_products = []
                                            for prod_ep in (
                                                f"https://api-performance.ozon.ru/api/client/campaign/{cid}/v2/products",
                                                f"https://api-performance.ozon.ru/api/client/campaign/{cid}/objects",
                                            ):
                                                try:
                                                    async with session.get(prod_ep, headers=headers_perf) as r_pr:
                                                        if r_pr.status == 200:
                                                            pr_json = await r_pr.json()
                                                            camp_products = pr_json.get("products") or pr_json.get("list") or []
                                                            if camp_products:
                                                                break
                                                except Exception:
                                                    continue

                                            # Запрашиваем статистику ДРР и расхода по кампании
                                            spend_val = 0.0
                                            rev_val = 0.0
                                            drr_val = 0.0
                                            try:
                                                stat_url = f"https://api-performance.ozon.ru/api/client/statistics/campaign/product?campaignIds={cid}&dateFrom={d_from_short}&dateTo={d_to_short}"
                                                async with session.get(stat_url, headers=headers_perf) as r_st:
                                                    if r_st.status == 200:
                                                        st_json = await r_st.json()
                                                        for row in st_json.get("rows") or st_json.get("list") or []:
                                                            spend_val += float(str(row.get("moneySpent") or row.get("spend") or 0).replace(",", "."))
                                                            rev_val += float(str(row.get("ordersMoney") or row.get("revenue") or 0).replace(",", "."))
                                                if rev_val > 0:
                                                    drr_val = round((spend_val / rev_val) * 100, 1)
                                            except Exception:
                                                pass

                                            results_campaigns.append({
                                                "id": cid,
                                                "title": str(c.get("title") or f"Кампания #{cid}"),
                                                "state": str(c.get("state")),
                                                "products_count": len(camp_products),
                                                "skus_inside": [str(p.get("sku") or p.get("id") or "") for p in camp_products],
                                                "spend_7d_rub": round(spend_val, 2),
                                                "promo_revenue_7d_rub": round(rev_val, 2),
                                                "drr_in_promo_pct": drr_val,
                                                "source": "Ozon Performance API (Direct)",
                                            })
            except Exception:
                pass

        if results_campaigns:
            return results_campaigns

        # Данные о составе и затратах кампаний нельзя выводить из общих продаж.
        raise RuntimeError("Ozon Performance API не вернул кампании; нельзя подменять данные оценками")

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разбирает точные статьи расходов и начислений из /v1/finance/cash-flow-statement/list (with_details=True)
        с кэшированием /v1/analytics/data на 15 минут для предотвращения ошибки Ozon HTTP 429.
        """
        now_utc = datetime.utcnow()
        if custom_date_from and custom_date_to:
            start_dt = datetime.strptime(custom_date_from, "%Y-%m-%d")
            end_dt = datetime.strptime(custom_date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        else:
            start_dt = now_utc - timedelta(days=days)
            end_dt = now_utc

        from_date = start_dt.strftime("%Y-%m-%dT00:00:00.000Z")
        to_date = end_dt.strftime("%Y-%m-%dT23:59:59.999Z")
        period_label = f"{start_dt.strftime('%d.%m.%Y')}–{end_dt.strftime('%d.%m.%Y')}"
        cache_key_an = f"analytics_{start_dt.strftime('%Y-%m-%d')}_{end_dt.strftime('%Y-%m-%d')}"

        async with self._lock:
            # 1. Проверяем дисковый кэш аналитики заказов (чтобы не бить в лимит 1 запрос/мин /v1/analytics/data)
            cached_orders = self._read_disk_cache(cache_key_an, max_age_sec=900)
            orders_list = []

            if cached_orders is not None:
                for co in cached_orders:
                    orders_list.append({
                        **co,
                        "created_at": datetime.fromisoformat(co["created_at"]) if isinstance(co.get("created_at"), str) else now_utc,
                    })
            else:
                analytics_payload = {
                    "date_from": start_dt.strftime("%Y-%m-%d"),
                    "date_to": end_dt.strftime("%Y-%m-%d"),
                    "metrics": ["revenue", "ordered_units"],
                    "dimension": ["sku", "day"],
                    "limit": 1000,
                    "offset": 0,
                }
                try:
                    an_resp = await self._request("POST", "/v1/analytics/data", analytics_payload)
                    serializable_orders = []
                    for row in an_resp.get("result", {}).get("data", []):
                        dims = row.get("dimensions", [])
                        metrics = row.get("metrics", [0, 0])
                        if len(dims) < 2 or len(metrics) < 2:
                            continue
                        sku_str = str(dims[0].get("id", ""))
                        day_str = str(dims[1].get("id", ""))
                        rev_total = float(metrics[0] or 0.0)
                        units = int(metrics[1] or 0)
                        if units <= 0:
                            continue
                        try:
                            dt_obj = datetime.strptime(day_str, "%Y-%m-%d")
                        except Exception:
                            dt_obj = now_utc
                        for idx in range(units):
                            ord_item = {
                                "order_id": f"oz_{day_str}_{sku_str}_{idx}",
                                "marketplace": "ozon",
                                "sku": sku_str,
                                "created_at": dt_obj,
                                "price": rev_total / units,
                                "commission": 0.0,
                                "logistics": 0.0,
                                "status": "ordered",
                            }
                            orders_list.append(ord_item)
                            serializable_orders.append({**ord_item, "created_at": dt_obj.isoformat()})
                    if serializable_orders:
                        self._write_disk_cache(cache_key_an, serializable_orders)
                except Exception as exc:
                    print(f"[Ozon Analytics Info]: {exc}", flush=True)

            # 2. Запрос к /v1/finance/cash-flow-statement/list с with_details=True
            cf_payload = {
                "date": {"from": from_date, "to": to_date},
                "page": 1,
                "page_size": 100,
                "with_details": True,
            }
            cf_resp = await self._request("POST", "/v1/finance/cash-flow-statement/list", cf_payload)

        res_cf = cf_resp.get("result", {})
        cash_flows = res_cf.get("cash_flows", [])
        details_list = res_cf.get("details", [])

        req_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        req_end = end_dt.replace(hour=23, minute=59, second=59, microsecond=0)

        def get_period_ratio(p_info: dict) -> float:
            try:
                p_begin = datetime.fromisoformat(str(p_info.get("begin", "")).replace("Z", "")).replace(tzinfo=None)
                p_end = datetime.fromisoformat(str(p_info.get("end", "")).replace("Z", "")).replace(tzinfo=None)
                total_p_days = max((p_end - p_begin).days + 1, 1)
                overlap_start = max(req_start, p_begin)
                overlap_end = min(req_end, p_end)
                overlap_days = max((overlap_end - overlap_start).days + 1, 0)
                return min(overlap_days / total_p_days, 1.0)
            except Exception:
                return 1.0

        sales_total = 0.0
        commission = 0.0
        for cf in cash_flows:
            ratio = get_period_ratio(cf.get("period") or {})
            if ratio <= 0:
                continue
            orders_amt = float(cf.get("orders_amount") or 0.0) * ratio
            returns_amt = float(cf.get("returns_amount") or 0.0) * ratio
            comm_amt = abs(float(cf.get("commission_amount") or 0.0)) * ratio
            sales_total += (orders_amt + returns_amt)
            commission += comm_amt

        # Не создаём вымышленные заказы и SKU при недоступной аналитике.

        deliv_direct = 0.0
        deliv_return = 0.0
        deliv_ozon_handover = 0.0

        partner_dropoff = 0.0
        partner_pack = 0.0
        partner_returns = 0.0
        acquiring = 0.0
        star_products = 0.0
        partner_last_mile = 0.0

        fbo_storage = 0.0
        promo_cpo = 0.0
        promo_cpc = 0.0
        other_fines = 0.0
        decompensations = 0.0
        mutual_settlements = 0.0

        for det in details_list:
            ratio = get_period_ratio(det.get("period") or {})
            if ratio <= 0:
                continue

            d_items = (det.get("delivery") or {}).get("delivery_services", {}).get("items") or []
            for it in d_items:
                name = str(it.get("name") or "")
                val = abs(float(it.get("price") or 0.0)) * ratio
                if name == "MarketplaceServiceItemDirectFlowLogisticSum":
                    deliv_direct += val
                elif name == "MarketplaceServiceItemDeliveryToHandoverPlaceOzon":
                    deliv_ozon_handover += val
                elif name == "MarketplaceServiceItemRedistributionDropoff":
                    partner_dropoff += val
                elif name == "MarketplaceServiceItemRedistributionLastMileCourier":
                    partner_last_mile += val

            r_items = (det.get("return") or {}).get("return_services", {}).get("items") or []
            for it in r_items:
                name = str(it.get("name") or "")
                val = abs(float(it.get("price") or 0.0)) * ratio
                if name == "MarketplaceServiceItemReturnFlowLogistic":
                    deliv_return += val
                elif name == "MarketplaceServiceItemRedistributionReturnsPVZ":
                    partner_returns += val

            s_items = (det.get("services") or {}).get("items") or []
            for it in s_items:
                name = str(it.get("name") or "")
                raw_price = float(it.get("price") or 0.0)
                if raw_price >= 0 or "AgencyFeeForSale" in name or "PointsAwarded" in name:
                    continue
                val = abs(raw_price) * ratio

                if name == "MarketplaceServicePromotionWithCostPerOrder":
                    promo_cpo += val
                elif name == "MarketplaceServiceCostPerClick":
                    promo_cpc += val
                elif name == "MarketplaceServiceStorageItem":
                    fbo_storage += val
                elif name == "ItemAgentServiceStarsMembership":
                    star_products += val
                elif "Acquiring" in name:
                    acquiring += val
                elif name in ("MarketplaceServiceProductMovementFromWarehouse", "MarketplaceServiceItemAdditionalPackagingAtWarehouse"):
                    partner_pack += val
                elif name == "MarketplaceServiceSellerReturnsCargoAssortment":
                    partner_returns += val
                elif name in ("MarketplaceProductDisposal", "MarketplaceServiceItemPackageMaterialsProvision"):
                    other_fines += val
                elif name == "MarketplaceServiceItemDeliveryToHandoverPlaceOzon":
                    pass
                else:
                    other_fines += val

            o_items = (det.get("others") or {}).get("items") or []
            for it in o_items:
                name = str(it.get("name") or "")
                raw_price = float(it.get("price") or 0.0)
                if raw_price >= 0 or "AgencyFeeForSale" in name or "CostOperationAggregate" in name:
                    continue
                val = abs(raw_price) * ratio

                if "Acquiring" in name:
                    acquiring += val
                elif "Decompensation" in name:
                    decompensations += val
                elif "SetOffBalance" in name:
                    mutual_settlements += val
                else:
                    decompensations += val

        deliv_total = deliv_direct + deliv_return + deliv_ozon_handover
        partners_total = partner_dropoff + partner_pack + partner_returns + acquiring + star_products + partner_last_mile
        ads_total = promo_cpo + promo_cpc
        other_total = decompensations + mutual_settlements

        total_deductions = commission + deliv_total + partners_total + fbo_storage + ads_total + other_fines + other_total
        total_payout = sales_total - total_deductions

        return {
            "source": f"ЛК Ozon Финансы ({period_label})",
            "orders": orders_list,
            "orders_count": len(orders_list),
            "sales_total": sales_total,
            "commission": commission,
            "deliv_total": deliv_total,
            "deliv_direct": deliv_direct,
            "deliv_return": deliv_return,
            "deliv_ozon_handover": deliv_ozon_handover,
            "partners_total": partners_total,
            "partner_dropoff": partner_dropoff,
            "partner_pack": partner_pack,
            "partner_returns": partner_returns,
            "acquiring": acquiring,
            "star_products": star_products,
            "partner_last_mile": partner_last_mile,
            "fbo_storage": fbo_storage,
            "ads_total": ads_total,
            "promo_cpo": promo_cpo,
            "promo_cpc": promo_cpc,
            "other_fines": other_fines,
            "decompensations": decompensations,
            "mutual_settlements": mutual_settlements,
            "total_deductions": total_deductions,
            "total_payout": total_payout,
        }

    async def update_price(self, product_id: str, offer_id: str, new_price: float) -> Dict[str, Any]:
        """Отправить цену; статус каждого товара проверяет вызывающая сторона."""
        import math
        if not math.isfinite(new_price) or new_price <= 0:
            raise ValueError("Цена должна быть конечной и положительной")
        if not offer_id:
            raise ValueError("Не задан offer_id")
        item = {"offer_id": str(offer_id), "price": f"{new_price:.2f}"}
        if product_id:
            if not str(product_id).isdigit() or int(product_id) <= 0:
                raise ValueError("Некорректный product_id Ozon")
            item["product_id"] = int(product_id)
        return await self._request("POST", "/v1/product/import/prices", {"prices": [item]})

    async def update_price_and_min_price(self, offer_id: str, recommended_price: float) -> Dict[str, Any]:
        # Не меняем min_price или old_price без отдельного согласия пользователя.
        return await self.update_price("", offer_id, recommended_price)

    async def deactivate_promo_product(self, action_id: str, product_id: str) -> Dict[str, Any]:
        raise NotImplementedError("Выход из акции отключён до подтверждения актуального метода Ozon")

    async def pause_or_reduce_campaign(self, campaign_id: str, pause: bool = True) -> bool:
        if not settings.OZON_PERF_CLIENT_ID or not settings.OZON_PERF_SECRET or not campaign_id:
            return False
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(trust_env=False, timeout=timeout) as session:
            token_resp = await session.post(
                f"{self.PERF_URL}/api/client/token",
                json={
                    "client_id": settings.OZON_PERF_CLIENT_ID,
                    "client_secret": settings.OZON_PERF_SECRET,
                    "grant_type": "client_credentials",
                },
            )
            if token_resp.status != 200:
                return False
            token_data = await token_resp.json()
            access_token = token_data.get("access_token")
            headers = {"Authorization": f"Bearer {access_token}"}
            endpoint = "deactivate" if pause else "activate"
            act_resp = await session.post(
                f"{self.PERF_URL}/api/client/campaign/{campaign_id}/{endpoint}",
                headers=headers,
            )
            return act_resp.status == 200
```

### `services/wb/statistics_api.py`

```python
import json
import time
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Callable, Optional, Dict, Any, List
from config import settings, LOGS_DIR
from services.ozon.seller_api import CircuitBreakerOpenError

WB_CACHE_FILE = LOGS_DIR / "wb_api_cache.json"


class WBStatisticsAPI:
    STATS_URL = "https://statistics-api.wildberries.ru"
    PRICES_URL = "https://discounts-prices-api.wildberries.ru"
    COMMON_URL = "https://common-api.wildberries.ru"
    ADVERT_URL = "https://advert-api.wildberries.ru"
    ANALYTICS_URL = "https://seller-analytics-api.wildberries.ru"
    MARKETPLACE_URL = "https://marketplace-api.wildberries.ru"
    CONTENT_URL = "https://content-api.wildberries.ru"

    def __init__(self, notify_callback: Optional[Callable[[str], Any]] = None):
        self._degraded_until: float = 0.0
        self._notify_callback = notify_callback
        self._consecutive_failures: int = 0
        self._failure_threshold: int = 4
        self._alert_sent: bool = False
        self._tariffs_lock = asyncio.Lock()
        self._orders_lock = asyncio.Lock()
        self._task_cache: Dict[int, float] = {}

    @property
    def is_degraded(self) -> bool:
        return time.time() < self._degraded_until

    def _read_disk_cache(self, key: str, max_age_sec: int = 3600) -> Optional[Any]:
        try:
            if WB_CACHE_FILE.exists():
                data = json.loads(WB_CACHE_FILE.read_text(encoding="utf-8"))
                entry = data.get(key)
                if entry and (time.time() - float(entry.get("ts", 0)) < max_age_sec):
                    return entry.get("val")
        except Exception:
            pass
        return None

    def _write_disk_cache(self, key: str, val: Any) -> None:
        try:
            data = {}
            if WB_CACHE_FILE.exists():
                try:
                    data = json.loads(WB_CACHE_FILE.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[key] = {"ts": time.time(), "val": val}
            WB_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    async def _trip_breaker(self, reason: str) -> None:
        self._degraded_until = time.time() + 300
        if self._notify_callback and not self._alert_sent:
            self._alert_sent = True
            await self._notify_callback(
                f"🚨 <b>[Алерт] Модуль Wildberries переведен в DEGRADED:</b>\n<i>{reason}</i>"
            )

    def _headers(self) -> Dict[str, str]:
        token = str(getattr(settings, "WB_API_TOKEN", "") or getattr(settings, "WB_TOKEN", "")).strip()
        return {
            "Authorization": token,
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, url: str, params: Optional[dict] = None, json_data: Optional[dict] = None
    ) -> Any:
        if self.is_degraded:
            raise CircuitBreakerOpenError("WB API находится в режиме DEGRADED")

        timeout = aiohttp.ClientTimeout(total=65, connect=20)
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession(trust_env=False, timeout=timeout) as session:
                    async with session.request(
                        method, url, headers=self._headers(), params=params, json=json_data
                    ) as resp:
                        if resp.status == 429:
                            if attempt == 0:
                                await asyncio.sleep(5)
                                continue
                            err_body = await resp.text()
                            raise RuntimeError(f"WB Rate Limit 429: {err_body[:100]}")

                        if resp.status >= 500:
                            text = await resp.text()
                            self._consecutive_failures += 1
                            if self._consecutive_failures >= self._failure_threshold:
                                await self._trip_breaker(f"HTTP {resp.status}: {text[:120]}")
                            raise CircuitBreakerOpenError(f"WB HTTP {resp.status}")

                        if resp.status >= 400:
                            err_body = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {err_body}")

                        self._consecutive_failures = 0
                        self._alert_sent = False
                        if resp.content_type == "application/json":
                            return await resp.json()
                        return await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 0 and not (method.upper() == "POST" and url.endswith("/api/v2/upload/task")):
                    await asyncio.sleep(3)
                    continue
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    await self._trip_breaker(f"Сетевой сбой: {exc}")
                raise CircuitBreakerOpenError(str(exc)) from exc

    async def fetch_wb_tariffs(self) -> Dict[str, float]:
        cached = self._read_disk_cache("wb_tariffs", max_age_sec=21600)
        if cached:
            return cached

        async with self._tariffs_lock:
            cached = self._read_disk_cache("wb_tariffs", max_age_sec=21600)
            if cached:
                return cached

            comm_rate = 0.25
            box_delivery_base = 55.0
            fbs_delivery_base = 65.0
            today_str = datetime.utcnow().strftime("%Y-%m-%d")

            try:
                comm_resp = await self._request("GET", f"{self.COMMON_URL}/api/v1/tariffs/commission")
                report = comm_resp.get("report") or []
                for row in report:
                    subj = str(row.get("subjectName") or "").lower()
                    if any(k in subj for k in ("гель", "лак", "ногт", "маникюр", "база", "топ")):
                        pct = float(row.get("paidStorageKgvp") or row.get("kgvpMarketplace") or 0.0)
                        if pct > 0:
                            comm_rate = pct / 100.0
                            break
            except Exception:
                pass

            try:
                box_resp = await self._request("GET", f"{self.COMMON_URL}/api/v1/tariffs/box", params={"date": today_str})
                wh_list = box_resp.get("response", {}).get("data", {}).get("warehouseList", [])
                for wh in wh_list:
                    raw_deliv = str(wh.get("boxDeliveryBase") or "0").replace(",", ".")
                    try:
                        val = float(raw_deliv)
                        if 30.0 <= val <= 90.0:
                            box_delivery_base = val
                            fbs_delivery_base = round(val * 1.15, 2)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            res = {
                "commission_rate": comm_rate,
                "logistics_rub": box_delivery_base,
                "fbs_logistics_rub": fbs_delivery_base,
            }
            self._write_disk_cache("wb_tariffs", res)
            return res

    async def fetch_wb_paid_storage(self, date_from: str, date_to: str) -> float:
        try:
            res = await self._request(
                "GET",
                f"{self.ANALYTICS_URL}/api/v1/paid_storage",
                params={"dateFrom": date_from, "dateTo": date_to},
            )
            rows = res.get("data", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
            return sum(float(r.get("warehousePrice") or r.get("price") or 0.0) for r in rows)
        except Exception:
            return 0.0

    async def fetch_wb_ad_spend(self, date_from: str, date_to: str) -> float:
        try:
            upd_rows = await self._request("GET", f"{self.ADVERT_URL}/adv/v1/upd", params={"from": date_from, "to": date_to})
            if isinstance(upd_rows, list):
                return sum(float(r.get("updSum") or 0.0) for r in upd_rows)
        except Exception:
            pass
        return 0.0

    async def update_price_and_min_price(self, nm_id: int, recommended_price_after_discount: float) -> Dict[str, Any]:
        """Создаёт задачу WB; отправка не означает, что цена уже применена.

        Публичный upload/task не содержит поля минимальной цены акции. Значение
        92% возвращается как рекомендация, но не выдаётся за установленное.
        """
        import math
        if isinstance(nm_id, bool) or not isinstance(nm_id, int) or nm_id <= 0:
            raise ValueError("Требуется положительный числовой nmID")
        value = float(recommended_price_after_discount)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Цена должна быть положительной и конечной")
        target = int(round(value))
        if target < 1:
            raise ValueError("Округлённая цена должна быть не меньше 1 ₽")
        result = await self._request("GET", f"{self.PRICES_URL}/api/v2/list/goods/filter",
                                     params={"limit": 100, "offset": 0, "filterNmID": nm_id})
        goods = result.get("data", {}).get("listGoods", []) if isinstance(result, dict) else []
        match = next((g for g in goods if int(g.get("nmID") or 0) == nm_id), None)
        if not match or "discount" not in match:
            raise ValueError("WB не вернул текущую скидку товара; изменение цены отменено")
        discount = int(match["discount"])
        if not 0 <= discount < 95:
            raise ValueError("Неподдерживаемая скидка WB")
        before = int(round(target / (1 - discount / 100)))
        response = await self._request("POST", f"{self.PRICES_URL}/api/v2/upload/task",
                                       json_data={"data": [{"nmID": nm_id, "price": before, "discount": discount}]})
        if not isinstance(response, dict) or response.get("error") is not False:
            raise ValueError(f"WB отклонил задачу: {response}")
        upload_id = (response.get("data") or {}).get("id")
        if not upload_id:
            raise RuntimeError("WB не вернул ID загрузки; проверьте состояние вручную")
        return {"status": "submitted", "upload_id": upload_id, "nm_id": nm_id,
                "seller_discount_pct": discount, "price_before_discount_set": before,
                "price_after_discount_target": target,
                "min_price_for_promotions": int(round(target * 0.92)),
                "min_price_set": False, "api_response": response}

    async def update_price(self, nm_id: str, new_price: float) -> Any:
        return await self.update_price_and_min_price(int(nm_id), float(new_price))

    async def fetch_stocks_and_prices(self) -> List[Dict[str, Any]]:
        cached = self._read_disk_cache("wb_stocks_and_prices", max_age_sec=180)
        if cached is not None:
            return cached

        all_goods = []
        for offset in range(0, 1000000, 1000):
            prices_raw = await self._request("GET", f"{self.PRICES_URL}/api/v2/list/goods/filter", params={"limit": 1000, "offset": offset})
            if not isinstance(prices_raw, dict) or prices_raw.get("error"):
                raise RuntimeError("Некорректный ответ WB по ценам")
            page = prices_raw.get("data", {}).get("listGoods", [])
            all_goods.extend(page)
            if len(page) < 1000:
                break
        else:
            raise RuntimeError("Не завершена пагинация цен WB")
        tariffs = await self.fetch_wb_tariffs()

        fbo_stocks_map: Dict[int, int] = {}
        fbs_stocks_map: Dict[int, int] = {}
        barcode_to_nm: Dict[str, int] = {}

        # Content API возвращает не более 100 карточек на страницу.
        cursor = {"limit": 100}
        seen_cursors = set()
        while True:
            cards_resp = await self._request("POST", f"{self.CONTENT_URL}/content/v2/get/cards/list",
                json_data={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}})
            if not isinstance(cards_resp, dict) or cards_resp.get("error"):
                raise RuntimeError("Не удалось получить карточки WB")
            cards = cards_resp.get("cards") or []
            for card in cards:
                nm = int(card.get("nmID") or 0)
                for size in card.get("sizes") or []:
                    for barcode in size.get("skus") or []:
                        if barcode and nm:
                            barcode = str(barcode).strip()
                            if barcode in barcode_to_nm and barcode_to_nm[barcode] != nm:
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

        warehouses = await self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/warehouses")
        if not isinstance(warehouses, list):
            raise RuntimeError("Некорректный список складов WB")
        all_skus = list(barcode_to_nm)
        for wh_id in {w.get("id") for w in warehouses if w.get("id")}:
            for pos in range(0, len(all_skus), 1000):
                st = await self._request("POST", f"{self.MARKETPLACE_URL}/api/v3/stocks/{wh_id}",
                                         json_data={"skus": all_skus[pos:pos + 1000]})
                if not isinstance(st, dict) or "stocks" not in st:
                    raise RuntimeError("Некорректный ответ WB FBS stocks")
                seen = set()
                for row in st["stocks"]:
                    barcode = str(row.get("sku") or "").strip()
                    if barcode in seen:
                        continue
                    seen.add(barcode)
                    nm = barcode_to_nm.get(barcode)
                    if nm:
                        fbs_stocks_map[nm] = fbs_stocks_map.get(nm, 0) + max(0, int(row.get("amount") or 0))

        # Актуальный асинхронный отчёт об остатках на складах WB (FBO).
        report_url = f"{self.ANALYTICS_URL}/api/v1/warehouse_remains"
        task_resp = await self._request("GET", report_url, params={"groupByNm": "true", "locale": "ru"})
        task_id = (task_resp.get("data") or {}).get("taskId") if isinstance(task_resp, dict) else None
        if not task_id:
            raise RuntimeError("WB не вернул ID отчёта FBO")
        for attempt in range(12):
            state = await self._request("GET", f"{report_url}/tasks/{task_id}/status")
            status = (state.get("data") or {}).get("status") if isinstance(state, dict) else None
            if status == "done":
                break
            if status in {"error", "failed", "canceled", "cancelled"}:
                raise RuntimeError(f"Ошибка отчёта WB FBO: {status}")
            if attempt < 11:
                await asyncio.sleep(5)
        else:
            raise RuntimeError("WB не подготовил отчёт FBO за отведённое время")
        raw_stocks = await self._request("GET", f"{report_url}/tasks/{task_id}/download")
        if not isinstance(raw_stocks, list):
            raise RuntimeError("Некорректный отчёт WB FBO")
        seen_fbo = set()
        for row in raw_stocks:
            nm = int(row.get("nmId") or 0)
            if not nm:
                continue
            key = (nm, row.get("barcode"), row.get("techSize"))
            if key in seen_fbo:
                continue
            seen_fbo.add(key)
            totals = [w for w in row.get("warehouses") or []
                      if str(w.get("warehouseName") or "").strip().lower() == "всего находится на складах"]
            if len(totals) != 1:
                raise RuntimeError(f"WB FBO: нет однозначного итога остатков для nmID {nm}")
            fbo_stocks_map[nm] = fbo_stocks_map.get(nm, 0) + max(0, int(totals[0].get("quantity") or 0))

        buyer_price_map: Dict[int, float] = {}
        sales_count_7d: Dict[int, int] = {}
        date_7d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            raw_orders = await self._request("GET", f"{self.STATS_URL}/api/v1/supplier/orders", params={"dateFrom": date_7d, "flag": 0})
            if isinstance(raw_orders, list):
                for o in raw_orders:
                    if o.get("isCancel"):
                        continue
                    nm = int(o.get("nmId") or 0)
                    bp = float(o.get("finishedPrice") or o.get("priceWithDisc") or 0.0)
                    if nm:
                        sales_count_7d[nm] = sales_count_7d.get(nm, 0) + 1
                        if bp > 0:
                            buyer_price_map[nm] = bp
        except Exception:
            pass

        dedup_results: Dict[int, Dict[str, Any]] = {}
        for g in all_goods:
            nm_id = int(g.get("nmID") or 0)
            if not nm_id:
                continue
            vendor_code = str(g.get("vendorCode") or str(nm_id)).strip()
            sizes = g.get("sizes") or []
            seller_price = float(sizes[0].get("discountedPrice") or sizes[0].get("price") or 0.0) if sizes else 0.0
            if seller_price <= 0:
                continue

            buyer_price = buyer_price_map.get(nm_id, seller_price)
            fbo_qty = fbo_stocks_map.get(nm_id, 0)
            fbs_qty = fbs_stocks_map.get(nm_id, 0)
            daily_sales = round(sales_count_7d.get(nm_id, 0) / 7.0, 2)
            acquiring_val = round(max(buyer_price, seller_price) * 0.015, 2)

            dedup_results[nm_id] = {
                "sku": vendor_code,
                "nm_id": nm_id,
                "external_id": str(nm_id),
                "current_price": seller_price,
                "buyer_price": buyer_price,
                "commission_rate": tariffs["commission_rate"],
                "acquiring_rub": acquiring_val,
                "fbo_logistics_cost": tariffs["logistics_rub"],
                "fbs_logistics_cost": tariffs.get("fbs_logistics_rub", tariffs["logistics_rub"] + 10.0),
                "stock_fbo": fbo_qty,
                "stock_fbs": fbs_qty,
                "daily_sales": daily_sales,
                "in_promo": False,
                "promo_max_price": 0.0,
                "promo_id": "",
            }

        results = list(dedup_results.values())
        if results:
            self._write_disk_cache("wb_stocks_and_prices", results)
        return results

    async def fetch_active_ad_campaigns(self, days: int = 7) -> List[Dict[str, Any]]:
        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = datetime.utcnow().strftime("%Y-%m-%d")
        campaigns: Dict[int, Dict[str, Any]] = {}

        try:
            upd = await self._request("GET", f"{self.ADVERT_URL}/adv/v1/upd", params={"from": date_from, "to": date_to})
            if isinstance(upd, list):
                for row in upd:
                    cid = int(row.get("advertId") or 0)
                    if not cid:
                        continue
                    c_entry = campaigns.setdefault(
                        cid,
                        {
                            "id": cid,
                            "name": str(row.get("campName") or f"Кампания WB #{cid}"),
                            "type": str(row.get("advertType") or ""),
                            "status": "Списания за период",
                            "spend_rub": 0.0,
                            "cpm_rub": 0,
                            "nms": [],
                        },
                    )
                    c_entry["spend_rub"] += float(row.get("updSum") or 0.0)
        except Exception:
            pass

        all_ids: List[int] = list(campaigns.keys())
        try:
            cnt_resp = await self._request("GET", f"{self.ADVERT_URL}/adv/v1/promotion/count")
            adverts_groups = cnt_resp.get("adverts") or [] if isinstance(cnt_resp, dict) else []
            for grp in adverts_groups:
                status_code = int(grp.get("status") or 0)
                if status_code in (9, 11, 4):
                    for adv_item in grp.get("advert_list") or []:
                        aid = int(adv_item.get("advertId") or 0)
                        if aid and aid not in all_ids:
                            all_ids.append(aid)
        except Exception:
            pass

        if all_ids:
            try:
                info_resp = await self._request(
                    "POST",
                    f"{self.ADVERT_URL}/adv/v1/promotion/adverts",
                    json_data=all_ids[:50],
                )
                if isinstance(info_resp, list):
                    status_names = {9: "🟢 Активна (идут показы)", 11: "⏸ На паузе", 4: "⚪️ Готова к запуску", 7: "⏹ Завершена"}
                    type_names = {8: "Автоматическая кампания (АРК)", 9: "Аукцион (Поиск + Каталог)", 6: "Поиск"}
                    for adv in info_resp:
                        cid = int(adv.get("advertId") or 0)
                        if not cid:
                            continue
                        st_code = int(adv.get("status") or 0)
                        tp_code = int(adv.get("type") or 0)

                        nms_list = []
                        cpm_val = 0
                        auto_p = adv.get("autoParams") or {}
                        if auto_p:
                            cpm_val = int(auto_p.get("cpm") or 0)
                            nms_list.extend(auto_p.get("nms") or [])
                        for up in adv.get("unitedParams") or []:
                            cpm_val = max(cpm_val, int(up.get("searchCPM") or up.get("catalogCPM") or 0))
                            nms_list.extend(up.get("nms") or [])
                        for p in adv.get("params") or []:
                            cpm_val = max(cpm_val, int(p.get("price") or 0))
                            for el in p.get("nms") or []:
                                nms_list.append(el.get("nm") if isinstance(el, dict) else el)

                        c_entry = campaigns.setdefault(cid, {"id": cid, "spend_rub": 0.0})
                        c_entry["name"] = str(adv.get("name") or c_entry.get("name") or f"Кампания #{cid}")
                        c_entry["status"] = status_names.get(st_code, f"Статус {st_code}")
                        c_entry["type"] = type_names.get(tp_code, f"Тип {tp_code}")
                        c_entry["cpm_rub"] = cpm_val
                        c_entry["nms"] = list(set(str(x) for x in nms_list if x))[:15]
            except Exception:
                pass

        return list(campaigns.values())

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        date_from = custom_date_from or (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = custom_date_to or datetime.utcnow().strftime("%Y-%m-%d")
        tariffs = await self.fetch_wb_tariffs()

        ad_spend, storage_fee = await asyncio.gather(
            self.fetch_wb_ad_spend(date_from, date_to),
            self.fetch_wb_paid_storage(date_from, date_to),
        )

        fin_rows = []
        for ver in ("v5", "v3", "v1"):
            try:
                res = await self._request(
                    "GET",
                    f"{self.STATS_URL}/api/{ver}/supplier/reportDetailByPeriod",
                    params={"dateFrom": date_from, "dateTo": date_to, "rrdid": 0, "limit": 1000},
                )
                if isinstance(res, list) and len(res) > 0:
                    fin_rows = res
                    break
            except Exception:
                continue

        if fin_rows:
            seller_rev = buyer_rev = returns_rev = for_pay_total = 0.0
            comm = acq = deliv_dir = deliv_ret = accept = fines = 0.0
            orders_list = []

            for r in fin_rows:
                oper_low = str(r.get("supplier_oper_name") or "").lower()
                srid = str(r.get("srid") or r.get("rrd_id") or "")
                sa_name = str(r.get("sa_name") or r.get("nm_id") or "")

                sp = float(r.get("retail_price_withdisc_rub") or 0.0)
                bp = float(r.get("retail_amount") or sp)
                fp = float(r.get("ppvz_for_pay") or 0.0)
                vw = abs(float(r.get("ppvz_vw") or 0.0)) + abs(float(r.get("ppvz_vw_nds") or 0.0))

                af = abs(float(r.get("acquiring_fee") or r.get("acquiring_bank") or 0.0))
                dl = abs(float(r.get("delivery_rub") or 0.0))
                ret_cnt = int(r.get("return_amount") or 0)
                ac = abs(float(r.get("acceptance") or r.get("acceptance_fee") or 0.0))
                pn = abs(float(r.get("penalty") or 0.0))
                ded = abs(float(r.get("deduction") or 0.0))

                if ret_cnt > 0 or "возврат" in oper_low:
                    deliv_ret += dl
                else:
                    deliv_dir += dl

                acq += af
                accept += ac
                fines += (pn + ded)

                if "продажа" in oper_low:
                    seller_rev += sp
                    buyer_rev += bp
                    for_pay_total += fp
                    row_comm = max(sp - fp - af, vw)
                    comm += row_comm
                    orders_list.append({
                        "order_id": f"wbf_{srid}_{r.get('rrd_id', '')}",
                        "marketplace": "wb",
                        "sku": sa_name,
                        "created_at": datetime.utcnow(),
                        "price": sp,
                        "buyer_price": bp,
                        "commission": row_comm,
                        "logistics": dl,
                        "status": "sold",
                    })

            deliv_total = deliv_dir + deliv_ret
            if acq == 0.0 and buyer_rev > 0:
                acq = round(buyer_rev * 0.015, 2)

            total_ded = comm + acq + deliv_total + storage_fee + accept + ad_spend + fines
            return {
                "source": f"Фин. отчет реализации WB ({date_from}..{date_to})",
                "orders": orders_list,
                "orders_count": len(orders_list),
                "revenue_seller": seller_rev,
                "revenue_buyer": buyer_rev,
                "returns_amount": returns_rev,
                "for_pay": for_pay_total,
                "commission": comm,
                "acquiring": acq,
                "logistics": deliv_total,
                "logistics_direct": deliv_dir,
                "logistics_return": deliv_ret,
                "storage": storage_fee,
                "acceptance": accept,
                "ads": ad_spend,
                "other_services": fines,
                "total_deductions": total_ded,
                "total_payout": seller_rev - total_ded,
            }

        raw_orders = await self._request("GET", f"{self.STATS_URL}/api/v1/supplier/orders", params={"dateFrom": date_from, "flag": 0})
        orders_list = []
        seller_rev = buyer_rev = returns_rev = comm_total = deliv_dir = deliv_ret = 0.0

        dt_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to_obj = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

        if isinstance(raw_orders, list):
            for o in raw_orders:
                raw_date = str(o.get("date") or "")
                try:
                    date_dt = datetime.fromisoformat(raw_date).replace(tzinfo=None)
                except Exception:
                    date_dt = datetime.utcnow()

                if not (dt_from_obj <= date_dt <= dt_to_obj):
                    continue

                nm = int(o.get("nmId") or 0)
                srid = str(o.get("srid", ""))
                supplier_article = str(o.get("supplierArticle") or nm)
                sp = float(o.get("priceWithDisc") or 0.0)
                bp = float(o.get("finishedPrice") or sp)
                fp = float(o.get("forPay") or 0.0)

                if o.get("isCancel"):
                    returns_rev += sp
                    deliv_ret += 50.0
                    continue

                comm_rub = (sp - fp) if (fp > 0 and sp > fp) else (sp * tariffs["commission_rate"])
                log_rub = tariffs["logistics_rub"]

                seller_rev += sp
                buyer_rev += bp
                comm_total += comm_rub
                deliv_dir += log_rub

                orders_list.append({
                    "order_id": srid or f"wb_{nm}_{raw_date}",
                    "marketplace": "wb",
                    "sku": supplier_article,
                    "created_at": date_dt,
                    "price": sp,
                    "buyer_price": bp,
                    "commission": comm_rub,
                    "logistics": log_rub,
                    "status": "ordered",
                })

        deliv_total = deliv_dir + deliv_ret
        acq_calculated = round(max(buyer_rev, seller_rev) * 0.015, 2)
        total_ded = comm_total + acq_calculated + deliv_total + storage_fee + ad_spend

        return {
            "source": f"WB Заказы + Расчет эквайринга ({date_from}..{date_to})",
            "orders": orders_list,
            "orders_count": len(orders_list),
            "revenue_seller": seller_rev,
            "revenue_buyer": buyer_rev,
            "returns_amount": returns_rev,
            "for_pay": seller_rev - comm_total,
            "commission": comm_total,
            "acquiring": acq_calculated,
            "logistics": deliv_total,
            "logistics_direct": deliv_dir,
            "logistics_return": deliv_ret,
            "storage": storage_fee,
            "acceptance": 0.0,
            "ads": ad_spend,
            "other_services": 0.0,
            "total_deductions": total_ded,
            "total_payout": seller_rev - total_ded,
        }
```

### `services/common/price_auditor.py`

```python
from typing import Dict, Any, List, Tuple, Optional
from sqlalchemy import select
from database.models import AsyncSessionLocal, Product
from database.crud import create_pending_action
from bot.handlers.callbacks import build_approval_keyboard, build_dual_ozon_approval_keyboard
from products_catalog import PRODUCTS_CATALOG
from config import settings
from uuid import uuid4
import math
import html

async def send_tg_safe(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """Использует уже настроенный прокси и сессию основного бота."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception as exc:
        print(f"[Price audit] Ошибка Telegram: {exc}", flush=True)
        return False


def calculate_ozon_unit_profit(
    price: float,
    cogs: float,
    logistics_rub: float,
    pvz_dropoff_rub: float,
    overhead_per_order_rub: float,
    ad_rate: float,
    api_comm_rate_under_300: float = 0.23,
    acquiring_rate: float = 0.015,
    usn_rate: float = 0.06,
) -> Dict[str, float]:
    """
    Расчет чистой прибыли на 1 заказ Ozon:
    - До 300 ₽ включительно: реальный % вознаграждения из API (22-23%).
    - Свыше 300 ₽: вознаграждение Ozon 52%.
    - Логистика: фактическая средняя из «Услуги доставки / Заказы» (~39 ₽).
    """
    if not 0 < api_comm_rate_under_300 < 1:
        raise ValueError("Нет корректной комиссии Ozon из API")
    under_300_rate = api_comm_rate_under_300
    comm_rate = under_300_rate if price <= 300.0 else 0.52

    comm_rub = round(price * comm_rate, 2)
    acq_rub = round(price * acquiring_rate, 2)
    ad_rub = round(price * ad_rate, 2)
    tax_rub = round(price * usn_rate, 2)

    total_deductions = comm_rub + acq_rub + logistics_rub + pvz_dropoff_rub + overhead_per_order_rub + ad_rub + tax_rub + cogs
    net_profit = round(price - total_deductions, 2)

    return {
        "price": price,
        "net_profit": net_profit,
        "comm_rub": comm_rub,
        "comm_pct": round(comm_rate * 100, 1),
        "acq_rub": acq_rub,
        "ad_rub": ad_rub,
        "ad_pct": round(ad_rate * 100, 1),
        "tax_rub": tax_rub,
        "logistics_rub": round(logistics_rub, 2),
        "pvz_rub": round(pvz_dropoff_rub, 2),
        "overhead_rub": round(overhead_per_order_rub, 2),
    }


def evaluate_ozon_dual_scenarios(
    current_price: float,
    cogs: float,
    api_comm_fbo: float,
    api_comm_fbs: float,
    fbo_stock: int,
    fbs_stock: int,
    actual_delivery_per_order: float,
    fbs_pvz_fee: float,
    overhead_per_order_rub: float,
    ad_rate: float,
    daily_sales: float = 1.0,
) -> Dict[str, Any]:
    """
    Возвращает полный двойной просчет для Ozon:
    1) На максимальном льготном пороге 299 ₽ (комиссия 22-23%)
    2) На оптимальной цене свыше 300 ₽ (при комиссии 52%)
    """
    if fbo_stock > 0 and fbs_stock > 0:
        total_st = fbo_stock + fbs_stock
        fbs_share = fbs_stock / total_st
        weighted_pvz = round(fbs_pvz_fee * fbs_share, 2)
        weighted_comm = (api_comm_fbo * fbo_stock + api_comm_fbs * fbs_stock) / total_st
        wh_mode = f"FBO ({fbo_stock} шт.) + FBS ({fbs_stock} шт.) [Доля FBS {fbs_share*100:.0f}%]"
    elif fbs_stock > 0:
        weighted_pvz = fbs_pvz_fee
        weighted_comm = api_comm_fbs
        wh_mode = f"FBS ({fbs_stock} шт.)"
    else:
        weighted_pvz = 0.0
        weighted_comm = api_comm_fbo
        wh_mode = f"FBO ({fbo_stock} шт.)"

    # Текущая экономика при действующей цене
    cur_m = calculate_ozon_unit_profit(
        current_price, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
    )

    # Сценарий 1: Максимальный порог до 300 ₽ -> 299 ₽
    m_299 = calculate_ozon_unit_profit(
        299.0, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
    )

    # Минимальная целая цена выше 300, обеспечивающая заданную прибыль.
    target_profit = max(m_299["net_profit"] + 25.0, 55.0)
    remaining = 1.0 - settings.OZON_HIGH_COMMISSION_RATE - settings.ACQUIRING_RATE - settings.TAX_RATE - ad_rate
    if remaining <= 0:
        raise ValueError("Доля удержаний >= 100%: прибыльную цену рассчитать нельзя")
    fixed = cogs + actual_delivery_per_order + weighted_pvz + overhead_per_order_rub
    price = max(301, math.ceil((fixed + target_profit + 0.02) / remaining))
    best_over300_m = calculate_ozon_unit_profit(float(price), cogs, actual_delivery_per_order,
                                               weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm)

    return {
        "wh_mode": wh_mode,
        "current": cur_m,
        "tier_299": m_299,
        "tier_over300": best_over300_m,
    }


def find_optimal_wb_price(
    current_price: float,
    cogs: float,
    comm_rate: float,
    fbo_stock: int,
    fbs_stock: int,
    fbo_logistics: float,
    fbs_logistics: float,
    overhead_per_order_rub: float,
    ad_rate: float,
    daily_sales: float = 1.0,
) -> Dict[str, Any]:
    if fbo_stock > 0 and fbs_stock > 0:
        total_st = fbo_stock + fbs_stock
        weighted_log = round((fbo_logistics * fbo_stock + fbs_logistics * fbs_stock) / total_st, 2)
        wh_mode = f"FBO ({fbo_stock} шт.) + FBS ({fbs_stock} шт.) [Оптимальная средняя]"
    elif fbs_stock > 0:
        weighted_log = fbs_logistics
        wh_mode = f"FBS ({fbs_stock} шт.)"
    else:
        weighted_log = fbo_logistics
        wh_mode = f"FBO ({fbo_stock} шт.)"

    target_ros = 0.20 if daily_sales >= 2.0 else 0.24
    denom = 1.0 - comm_rate - 0.015 - 0.06 - ad_rate - target_ros
    if denom <= 0:
        raise ValueError("Целевая рентабельность недостижима при текущих удержаниях WB")

    opt_price = float(round((cogs + weighted_log + overhead_per_order_rub) / denom))
    acq_rub = round(opt_price * 0.015, 2)
    comm_rub = round(opt_price * comm_rate, 2)
    ad_rub = round(opt_price * ad_rate, 2)
    tax_rub = round(opt_price * 0.06, 2)
    net_profit = round(opt_price - comm_rub - acq_rub - weighted_log - overhead_per_order_rub - ad_rub - tax_rub - cogs, 2)

    cur_net = round(
        current_price * (1.0 - comm_rate - 0.015 - 0.06 - ad_rate) - weighted_log - overhead_per_order_rub - cogs, 2
    )

    return {
        "recommended_price": opt_price,
        "current_net_profit": cur_net,
        "net_profit": net_profit,
        "commission_rub": comm_rub,
        "commission_pct": round(comm_rate * 100, 1),
        "acquiring_rub": acq_rub,
        "logistics_rub": weighted_log,
        "overhead_rub": round(overhead_per_order_rub, 2),
        "ad_rub": ad_rub,
        "ad_pct": round(ad_rate * 100, 1),
        "tax_rub": tax_rub,
        "wh_mode": wh_mode,
    }


async def run_daily_price_audit(bot, admin_id: int, ozon_api, wb_api) -> None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Product))
        db_prods = {(p.marketplace, p.sku): p for p in res.scalars().all()}

    # 1. Сбор фактических расходов Ozon из недельного отчета (включая реальную доставку 6686 / 172 = 38.87 ₽!)
    oz_actual_delivery_per_order = None
    oz_overhead_per_order = 0.0
    oz_ad_rate = 0.0
    oz_dropoff_per_order = 0.0
    try:
        oz_fin = await ozon_api.fetch_real_finance_breakdown(days=7)
        oz_orders_cnt = int(oz_fin.get("orders_count") or 0)
        if oz_orders_cnt <= 0:
            raise ValueError("Нет числа заказов в отчёте Ozon")
        oz_sales_rub = float(oz_fin.get("sales_total") or 0.0)
        oz_ads_rub = float(oz_fin.get("ads_total") or 0.0)
        if oz_sales_rub > 0 and oz_ads_rub > 0:
            oz_ad_rate = round(oz_ads_rub / oz_sales_rub, 4)

        # Фактическая стоимость доставки = графа «Услуги доставки» / число заказов
        deliv_total_rub = float(oz_fin.get("deliv_total") or 0.0)
        if deliv_total_rub > 0:
            oz_actual_delivery_per_order = round(deliv_total_rub / oz_orders_cnt, 2)

        # Передача Ozon уже входит в deliv_total, не учитываем её повторно.
        dropoff_total = float(oz_fin.get("partner_dropoff") or 0.0)
        if dropoff_total > 0:
            oz_dropoff_per_order = round(dropoff_total / oz_orders_cnt, 2)

        other_hold_total = (
            float(oz_fin.get("partner_pack") or 0.0)
            + float(oz_fin.get("partner_returns") or 0.0)
            + float(oz_fin.get("star_products") or 0.0)
            + float(oz_fin.get("partner_last_mile") or 0.0)
            + float(oz_fin.get("fbo_storage") or 0.0)
            + float(oz_fin.get("other_fines") or 0.0)
        )
        if other_hold_total > 0:
            oz_overhead_per_order = round(other_hold_total / oz_orders_cnt, 2)
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Финансы Ozon недоступны: {html.escape(str(exc))}")

    # 2. АУДИТ ЦЕН OZON (с двумя кнопками: порог 299 ₽ и цена свыше 300 ₽ + кнопка Отклонить)
    try:
        if oz_actual_delivery_per_order is None:
            raise ValueError("Нет фактической доставки на заказ; рекомендации отключены")
        ozon_items = await ozon_api.fetch_stocks_and_prices()
        seen_ozon = set()
        for item in ozon_items:
            sku = str(item.get("sku") or "").strip()
            if not sku or sku in seen_ozon:
                continue
            seen_ozon.add(sku)

            fbo_st = int(item.get("stock_fbo") or 0)
            fbs_st = int(item.get("stock_fbs") or 0)
            if fbo_st == 0 and fbs_st == 0:
                continue

            cur_p = float(item.get("current_price") or 0.0)
            p_db = db_prods.get(("ozon", sku))
            if not p_db or p_db.cost_price <= 0:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: не задана себестоимость, рекомендация пропущена")
                continue
            cogs = p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost

            dual = evaluate_ozon_dual_scenarios(
                current_price=cur_p,
                cogs=cogs,
                api_comm_fbo=float(item.get("commission_rate_fbo") or item.get("commission_rate") or 0.0),
                api_comm_fbs=float(item.get("commission_rate_fbs") or item.get("commission_rate") or 0.0),
                fbo_stock=fbo_st,
                fbs_stock=fbs_st,
                actual_delivery_per_order=oz_actual_delivery_per_order,
                fbs_pvz_fee=oz_dropoff_per_order,
                overhead_per_order_rub=oz_overhead_per_order,
                ad_rate=oz_ad_rate,
                daily_sales=float(item.get("daily_sales") or 1.0),
            )
            t299 = dual["tier_299"]
            tover = dual["tier_over300"]
            cur_m = dual["current"]

            in_promo = bool(item.get("in_promo"))
            promo_note = (
                f"🔥 <b>Участие в акции:</b> Да ({html.escape(str(item.get('promo_names') or 'Акция Ozon'))})\n"
                f"⚠️ <i>Акция может ограничить цену. Проверьте её условия в кабинете; автоматического выхода нет.</i>\n"
                if in_promo
                else "🏷 <b>Участие в акции:</b> Нет (свободное управление ценой)\n"
            )

            group_id = uuid4().hex
            # Два варианта одной рекомендации блокируются совместно.
            act_299_id = await create_pending_action(
                marketplace="ozon",
                sku=sku,
                action_type="update_price",
                payload={
                    "product_id": item.get("external_id") or item.get("product_id"),
                    "offer_id": sku,
                    "group_id": group_id,
                    "new_price": 299.0,
                },
            )
            act_over300_id = await create_pending_action(
                marketplace="ozon",
                sku=sku,
                action_type="update_price",
                payload={
                    "product_id": item.get("external_id") or item.get("product_id"),
                    "offer_id": sku,
                    "group_id": group_id,
                    "new_price": tover["price"],
                },
            )

            msg_text = (
                f"🔵 <b>[Аудит цены Ozon] Артикул {html.escape(sku)}</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"⚖️ Схема: <i>{dual['wh_mode']}</i>\n"
                f"ℹ️ Средние расходы и доли FBO/FBS — оценка, не гарантия прибыли.\n"
                f"{promo_note}"
                f"💵 Текущая цена: <b>{cur_p:.0f} ₽</b> (прибыль сейчас: <b>{cur_m['net_profit']:+.0f} ₽/шт.</b>)\n\n"
                f"1️⃣ <b>Вариант 1: Максимальный порог 299 ₽ (Комиссия {t299['comm_pct']}%)</b>\n"
                f"• Вознаграждение Ozon ({t299['comm_pct']}%): <b>-{t299['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{t299['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{t299['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({t299['ad_pct']}%): <b>-{t299['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{t299['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{t299['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{t299['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при 299 ₽: {t299['net_profit']:+.0f} ₽/шт.</b>\n\n"
                f"2️⃣ <b>Вариант 2: Свыше 300 ₽ — {tover['price']:.0f} ₽ (Комиссия 52%)</b>\n"
                f"• Вознаграждение Ozon (52%): <b>-{tover['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{tover['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{tover['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({tover['ad_pct']}%): <b>-{tover['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{tover['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{tover['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{tover['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при {tover['price']:.0f} ₽: {tover['net_profit']:+.0f} ₽/шт.</b>"
            )
            await send_tg_safe(
                bot,
                admin_id,
                msg_text,
                reply_markup=build_dual_ozon_approval_keyboard(
                    act_299_id=act_299_id,
                    profit_299=t299["net_profit"],
                    act_over300_id=act_over300_id,
                    price_over300=tover["price"],
                    profit_over300=tover["net_profit"],
                ),
            )
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен Ozon: <code>{html.escape(str(exc))}</code>")

    # 3. Сбор накладных расходов WB на 1 заказ
    wb_overhead_per_order = 5.0
    wb_ad_rate = 0.0
    try:
        wb_fin = await wb_api.fetch_real_finance_breakdown(days=7)
        wb_cnt = max(int(wb_fin.get("orders_count") or 1), 1)
        wb_rev = float(wb_fin.get("revenue_seller") or 0.0)
        wb_ads = float(wb_fin.get("ads") or 0.0)
        if wb_rev > 0 and wb_ads > 0:
            wb_ad_rate = round(wb_ads / wb_rev, 4)
        wb_other_total = (
            float(wb_fin.get("storage") or 0.0)
            + float(wb_fin.get("acceptance") or 0.0)
            + float(wb_fin.get("other_services") or 0.0)
            + float(wb_fin.get("logistics_return") or 0.0)
        )
        wb_overhead_per_order = round(wb_other_total / wb_cnt, 2)
    except Exception:
        pass

    # 4. АУДИТ ЦЕН WILDBERRIES
    try:
        wb_items = await wb_api.fetch_stocks_and_prices()
        seen_wb = set()
        for item in wb_items:
            raw_nm = item.get("nm_id")
            nm_id = raw_nm if isinstance(raw_nm, int) and not isinstance(raw_nm, bool) else 0
            sku = str(item.get("sku") or nm_id).strip()
            if not nm_id or nm_id in seen_wb:
                continue
            seen_wb.add(nm_id)

            fbo_st = int(item.get("stock_fbo") or 0)
            fbs_st = int(item.get("stock_fbs") or 0)
            if fbo_st == 0 and fbs_st == 0:
                continue

            cur_p = float(item.get("current_price") or 0.0)
            p_db = db_prods.get(("wb", sku)) or db_prods.get(("wb", str(nm_id)))
            if not p_db or p_db.cost_price <= 0:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: не задана себестоимость, рекомендация пропущена")
                continue
            cogs = p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost

            calc = find_optimal_wb_price(
                current_price=cur_p,
                cogs=cogs,
                comm_rate=float(item.get("commission_rate") or 0.25),
                fbo_stock=fbo_st,
                fbs_stock=fbs_st,
                fbo_logistics=float(item.get("fbo_logistics_cost") or 55.0),
                fbs_logistics=float(item.get("fbs_logistics_cost") or 65.0),
                overhead_per_order_rub=wb_overhead_per_order,
                ad_rate=wb_ad_rate,
                daily_sales=float(item.get("daily_sales") or 1.0),
            )
            rec_p = calc["recommended_price"]
            act_id = await create_pending_action(
                marketplace="wb",
                sku=sku,
                action_type="update_price",
                payload={
                    "nm_id": nm_id,
                    "sku": sku,
                    "new_price": rec_p,
                },
            )
            msg_text = (
                f"🟣 <b>[Аудит цены WB] Артикул {html.escape(sku)} (nmID: {nm_id})</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"📈 Продажи: <b>{item.get('daily_sales', 0)} шт./день</b> ({calc['wh_mode']})\n"
                f"ℹ️ Тарифы и расходы могут быть оценочными; проверьте их в кабинете.\n"
                f"💰 Текущая цена: <b>{cur_p:.0f} ₽</b> ➡️ Рекомендуемая: <b>{rec_p:.0f} ₽</b>\n"
                f"📊 Комиссия WB ({calc['commission_pct']}%): <b>-{calc['commission_rub']:.0f} ₽</b>\n"
                f"🚚 Логистика: <b>-{calc['logistics_rub']:.0f} ₽</b> | Эквайринг (1.5%): <b>-{calc['acquiring_rub']:.2f} ₽</b>\n"
                f"🧾 Прочие удержания на 1 заказ: <b>-{calc['overhead_rub']:.0f} ₽</b> | Налог УСН (6%): <b>-{calc['tax_rub']:.0f} ₽</b>\n"
                f"💵 Себестоимость: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль: +{calc['net_profit']:.0f} ₽/шт.</b> (сейчас: {calc['current_net_profit']:+.0f} ₽/шт.)"
            )
            await send_tg_safe(
                bot,
                admin_id,
                msg_text,
                reply_markup=build_approval_keyboard(act_id, f"✅ Установить {rec_p:.0f} ₽ на WB"),
            )
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен WB: <code>{html.escape(str(exc))}</code>")
```

### `services/common/unit_economics.py`

```python
from dataclasses import dataclass, field
from typing import List, Dict, Any
from config import settings


@dataclass
class PricingRecommendation:
    sku: str
    marketplace: str
    current_price: float
    calculated_floor: float
    in_ozon_tariff_trap: bool
    locked_by_promo: bool
    promo_max_price: float
    stock_critical: bool
    days_of_stock: float
    alternatives: List[Dict[str, Any]] = field(default_factory=list)


def calculate_floor_price(
    cost_price: float,
    packaging_cost: float,
    fbo_logistics_cost: float,
    first_mile_unit_cost: float,
    target_profit: float,
    commission_rate: float,
    acquiring_rate: float,
    tax_rate: float,
    drr_rate: float,
) -> float:
    """
    Формула:
    Floor = (Себестоимость + Упаковка + Логистика FBO + Доставка первой мили + Прибыль) /
            (1 - (Комиссия + Эквайринг + Налог 6% + ДРР))
    """
    numerator = (
        cost_price
        + packaging_cost
        + fbo_logistics_cost
        + first_mile_unit_cost
        + target_profit
    )
    total_rate = commission_rate + acquiring_rate + tax_rate + drr_rate
    denominator = 1.0 - total_rate
    if denominator <= 0:
        raise ValueError("Суммарные удержания >= 100%: прибыльная цена недостижима")
    return round(numerator / denominator, 2)


def evaluate_product_decision(
    sku: str,
    marketplace: str,
    external_id: str,
    current_price: float,
    cost_price: float,
    packaging_cost: float,
    fbo_logistics_cost: float,
    first_mile_unit_cost: float,
    target_profit: float,
    commission_rate: float,
    drr_rate: float,
    stock_fbo: int,
    avg_daily_sales: float,
    in_promo: bool,
    promo_max_price: float,
    promo_id: str,
    active_campaign_id: str,
) -> PricingRecommendation:
    floor_price = calculate_floor_price(
        cost_price=cost_price,
        packaging_cost=packaging_cost,
        fbo_logistics_cost=fbo_logistics_cost,
        first_mile_unit_cost=first_mile_unit_cost,
        target_profit=target_profit,
        commission_rate=commission_rate,
        acquiring_rate=settings.ACQUIRING_RATE,
        tax_rate=settings.TAX_RATE,
        drr_rate=drr_rate,
    )

    days_of_stock = round(stock_fbo / max(avg_daily_sales, 0.1), 1)
    stock_critical = days_of_stock < 2.0

    in_trap = marketplace.lower() == "ozon" and floor_price > 300.0
    if marketplace.lower() == "ozon" and floor_price > 300.0:
        floor_price = calculate_floor_price(cost_price, packaging_cost, fbo_logistics_cost,
            first_mile_unit_cost, target_profit, settings.OZON_HIGH_COMMISSION_RATE,
            settings.ACQUIRING_RATE, settings.TAX_RATE, drr_rate)
        floor_price = max(301.0, floor_price)
    locked_by_promo = in_promo and (promo_max_price > 0) and (floor_price > promo_max_price)

    alternatives: List[Dict[str, Any]] = []

    # 1. Защита от слива рекламного бюджета при остатках < 2 дней
    if stock_critical and active_campaign_id:
        alternatives.append(
            {
                "label": f"🛑 Остановить рекламу (остаток {days_of_stock} дн.)",
                "action_type": "STOP_AD_CAMPAIGN",
                "payload": {
                    "external_id": external_id,
                    "campaign_id": active_campaign_id,
                },
            }
        )

    # 2. Проверка блокировки акцией с верхним порогом
    if locked_by_promo:
        target_after_exit = round(floor_price)
        alternatives.append(
            {
                "label": f"🔓 Исключить из акции (порог {promo_max_price:.0f} ₽) и поставить {target_after_exit:.0f} ₽",
                "action_type": "EXIT_PROMO_AND_PRICE",
                "payload": {
                    "external_id": external_id,
                    "promo_id": promo_id,
                    "new_price": target_after_exit,
                },
            }
        )

    # 3. Защита от тарифной ловушки Ozon (301–450 ₽)
    if in_trap:
        alternatives.extend(
            [
                {
                    "label": "Поставить 299 ₽ (льготный тариф до 300 ₽)",
                    "action_type": "SET_PRICE",
                    "payload": {"external_id": external_id, "new_price": 299.0},
                },
                {
                    "label": f"Расчетная цена {floor_price:.0f} ₽ (комиссия 52%)",
                    "action_type": "SET_PRICE",
                    "payload": {"external_id": external_id, "new_price": round(floor_price)},
                },
                {
                    "label": "Снизить ставку рекламы (удержать цену <= 300 ₽)",
                    "action_type": "REDUCE_AD_BID",
                    "payload": {
                        "external_id": external_id,
                        "campaign_id": active_campaign_id,
                    },
                },
            ]
        )
    elif abs(current_price - floor_price) >= 15.0 and not locked_by_promo:
        alternatives.append(
            {
                "label": f"Установить расчетную цену {round(floor_price)} ₽",
                "action_type": "SET_PRICE",
                "payload": {"external_id": external_id, "new_price": round(floor_price)},
            }
        )

    return PricingRecommendation(
        sku=sku,
        marketplace=marketplace,
        current_price=current_price,
        calculated_floor=floor_price,
        in_ozon_tariff_trap=in_trap,
        locked_by_promo=locked_by_promo,
        promo_max_price=promo_max_price,
        stock_critical=stock_critical,
        days_of_stock=days_of_stock,
        alternatives=alternatives,
    )
```

### `bot/handlers/callbacks.py`

```python
import html
import math
from typing import Any

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import settings
from database.crud import claim_price_action, finish_price_action
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

callbacks_router = Router()
ozon_api = OzonSellerAPI()
wb_api = WBStatisticsAPI()


def _resolve_wb_nm_id(payload: dict, fallback_sku: str, call_data: str) -> int:
    """nmID берётся только из явно указанного поля, не из SKU или callback ID."""
    value = payload.get("nm_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Не указан положительный числовой nmID WB; создайте новый аудит")
    return value


def build_approval_keyboard(action_id: int, custom_approve_text: str = "✅ Подтвердить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=custom_approve_text, callback_data=f"act:app:{action_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:rej:{action_id}"),
    ]])


def build_dual_ozon_approval_keyboard(act_299_id: int, profit_299: float, act_over300_id: int,
                                      price_over300: float, profit_over300: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Порог 299 ₽ (прибыль {profit_299:+.0f} ₽/шт.)", callback_data=f"act:app:{act_299_id}")],
        [InlineKeyboardButton(text=f"🚀 Свыше 300: {price_over300:.0f} ₽ (прибыль {profit_over300:+.0f} ₽/шт.)", callback_data=f"act:app:{act_over300_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:rej:{act_299_id}")],
    ])


def _ozon_result(response: Any, offer_id: str, product_id: str) -> None:
    if not isinstance(response, dict) or not isinstance(response.get("result"), list):
        raise RuntimeError("Неизвестный ответ Ozon; проверьте цену в кабинете")
    rows = [r for r in response["result"] if isinstance(r, dict) and
            (str(r.get("offer_id") or "") == offer_id or
             (product_id and str(r.get("product_id") or "") == product_id))]
    if len(rows) != 1:
        raise RuntimeError("Нет однозначного результата Ozon; проверьте цену в кабинете")
    row = rows[0]
    if row.get("updated") is not True or row.get("errors"):
        raise ValueError(f"Ozon отклонил цену: {row.get('errors') or row}")


def _action_id(call: CallbackQuery) -> int:
    parts = (call.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) <= 0:
        raise ValueError("Некорректный идентификатор действия")
    return int(parts[2])


async def _authorized(call: CallbackQuery) -> bool:
    if not call.from_user or call.from_user.id != settings.TG_ADMIN_ID:
        await call.answer("Доступ запрещён", show_alert=True)
        return False
    return True


@callbacks_router.callback_query(F.data.startswith("act:app:"))
async def cb_approve_action(call: CallbackQuery):
    if not await _authorized(call):
        return
    try:
        action_id = _action_id(call)
    except ValueError:
        await call.answer("Некорректное действие", show_alert=True)
        return
    action = await claim_price_action(action_id)
    if not action:
        await call.answer("Действие уже обработано или выполняется", show_alert=True)
        return
    await call.answer("Отправляю запрос маркетплейсу")
    submitted_request = False
    try:
        if action.action_type != "update_price":
            raise ValueError("Неподдерживаемый тип действия")
        payload = action.payload
        price = float(payload.get("new_price") or 0)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Некорректная цена")
        operation_result = None
        if action.marketplace == "ozon":
            offer_id = str(payload.get("offer_id") or action.sku)
            product_id = str(payload.get("product_id") or "")
            submitted_request = True
            response = await ozon_api.update_price(product_id, offer_id, price)
            _ozon_result(response, offer_id, product_id)
            status = "executed"
            detail = f"Ozon подтвердил обновление цены {offer_id}: {price:.2f} ₽. Проверьте витрину и условия акции."
        elif action.marketplace == "wb":
            nm_id = _resolve_wb_nm_id(payload, str(action.sku), call.data or "")
            submitted_request = True
            result = await wb_api.update_price_and_min_price(nm_id, price)
            if result.get("status") != "submitted":
                raise RuntimeError("Неизвестный ответ WB")
            status = "submitted"
            operation_result = {"upload_id": result["upload_id"], "min_price_for_promotions": result["min_price_for_promotions"]}
            detail = (f"WB принял задачу №{result['upload_id']} для nmID {nm_id}; "
                      "цена ещё не подтверждена. Проверьте статус загрузки и карантин в кабинете. "
                      f"Рекомендуемый минимум акции {result['min_price_for_promotions']} ₽ не установлен API.")
        else:
            raise ValueError("Неизвестный маркетплейс")
        await finish_price_action(action_id, status, operation_result)
    except Exception as exc:
        # После отправки сетевой сбой/неожиданный ответ может означать уже принятое действие.
        status = "unknown" if submitted_request and not isinstance(exc, ValueError) else "pending"
        await finish_price_action(action_id, status)
        detail = (f"Ошибка: {exc}. " + ("Проверьте цену/задачу в кабинете до повторного запуска." if status == "unknown" else "Действие не подтверждено."))
    if call.message:
        await call.message.reply(html.escape(detail), parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("act:rej:"))
async def cb_reject_action(call: CallbackQuery):
    if not await _authorized(call):
        return
    try:
        action_id = _action_id(call)
    except ValueError:
        await call.answer("Некорректное действие", show_alert=True)
        return
    action = await claim_price_action(action_id)
    if not action:
        await call.answer("Действие уже обработано или выполняется", show_alert=True)
        return
    await finish_price_action(action_id, "rejected")
    await call.answer("Рекомендация отклонена")
    if call.message:
        await call.message.reply("Рекомендация отклонена.")
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


async def claim_price_action(action_id: int) -> Optional[PendingAction]:
    """Атомарно резервирует действие и альтернативы его группы для одного запроса."""
    from sqlalchemy import update
    async with _db_write_lock:
        async with get_db_session() as session:
            result = await session.execute(
                update(PendingAction).where(PendingAction.id == action_id,
                                             PendingAction.status == "pending")
                .values(status="processing").returning(PendingAction)
            )
            action = result.scalar_one_or_none()
            if not action:
                await session.rollback()
                return None
            group = action.payload.get("group_id")
            if group:
                await session.execute(update(PendingAction).where(
                    PendingAction.id != action_id, PendingAction.status == "pending",
                    func.json_extract(PendingAction.payload_json, "$.group_id") == group,
                ).values(status="blocked"))
            await session.commit()
            return action


async def finish_price_action(action_id: int, status: str, result: Optional[dict] = None) -> None:
    """submitted/unknown не позволяют повторить потенциально выполненный запрос."""
    from sqlalchemy import update
    if status not in {"pending", "executed", "submitted", "unknown", "rejected"}:
        raise ValueError("Недопустимый статус")
    async with _db_write_lock:
        async with get_db_session() as session:
            action = await session.get(PendingAction, action_id)
            if not action or action.status != "processing":
                raise ValueError("Действие не зарезервировано")
            group = action.payload.get("group_id")
            action.status = status
            if result is not None:
                payload = action.payload
                payload["result"] = result
                action.payload_json = json.dumps(payload, ensure_ascii=False)
            if status in {"executed", "submitted", "rejected"}:
                action.executed_at = utcnow_naive()
            if group:
                sibling_status = "pending" if status == "pending" else ("rejected" if status in {"executed", "submitted", "rejected"} else "unknown")
                await session.execute(update(PendingAction).where(
                    PendingAction.id != action_id, PendingAction.status == "blocked",
                    func.json_extract(PendingAction.payload_json, "$.group_id") == group,
                ).values(status=sibling_status))
            await session.commit()
```

### `tests/test_stage2.py`

```python
"""Контрактные тесты этапа 2 без обращений к реальным маркетплейсам."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_tmp = tempfile.TemporaryDirectory()
os.environ.update(TG_BOT_TOKEN='123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi', TG_ADMIN_ID='123',
                  OZON_CLIENT_ID='test', OZON_API_KEY='test', WB_API_TOKEN='test',
                  GEMINI_API_KEY='test', DB_URL='sqlite+aiosqlite:///' + str(Path(_tmp.name) / 'test.db'))

from database.models import init_db, close_db
from database.crud import create_pending_action, get_pending_action, claim_price_action, finish_price_action
from services.common.price_auditor import calculate_ozon_unit_profit, evaluate_ozon_dual_scenarios, find_optimal_wb_price
from services.common.unit_economics import calculate_floor_price
from services.wb.statistics_api import WBStatisticsAPI
from services.ozon.seller_api import OzonSellerAPI
from bot.handlers.callbacks import _ozon_result, _resolve_wb_nm_id, cb_approve_action, cb_reject_action


class TestPrices(unittest.TestCase):
    def test_commission_boundary(self):
        self.assertEqual(calculate_ozon_unit_profit(300, 60, 39, 0, 3, .063, .22)['comm_pct'], 22)
        self.assertEqual(calculate_ozon_unit_profit(300.01, 60, 39, 0, 3, .063, .22)['comm_pct'], 52)
        d = evaluate_ozon_dual_scenarios(299, 60, .22, .23, 10, 10, 39, 10, 5, .063)
        self.assertGreater(d['tier_over300']['price'], 300)
        self.assertGreaterEqual(d['tier_over300']['net_profit'], max(d['tier_299']['net_profit'] + 25, 55))

    def test_invalid_economics(self):
        with self.assertRaises(ValueError): calculate_floor_price(1, 1, 1, 1, 1, .9, .1, .06, .1)
        with self.assertRaises(ValueError): find_optimal_wb_price(100, 10, .8, 1, 0, 10, 10, 1, .1)

    def test_identifiers_and_ozon_result(self):
        self.assertEqual(_resolve_wb_nm_id({'nm_id': 123}, '1001', 'act:app:42'), 123)
        with self.assertRaises(ValueError): _resolve_wb_nm_id({'external_id':'1001'}, '1001', 'act:app:42')
        _ozon_result({'result':[{'offer_id':'1001','updated':True,'errors':[]}]}, '1001', '')
        with self.assertRaises(ValueError): _ozon_result({'result':[{'offer_id':'1001','updated':False,'errors':[{'message':'bad'}]}]}, '1001', '')
        with self.assertRaises(RuntimeError): _ozon_result({'result':[]}, '1001', '')


class TestAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): await init_db()
    async def asyncTearDown(self): await close_db()

    async def test_action_group_atomic(self):
        import asyncio, uuid
        group = uuid.uuid4().hex
        a = await create_pending_action('ozon','1001','update_price', {'group_id':group})
        b = await create_pending_action('ozon','1001','update_price', {'group_id':group})
        results = await asyncio.gather(claim_price_action(a.id), claim_price_action(b.id))
        self.assertEqual(sum(x is not None for x in results), 1)
        winner = next(x for x in results if x is not None)
        await finish_price_action(winner.id, 'executed')
        self.assertEqual({(await get_pending_action(a.id)).status, (await get_pending_action(b.id)).status}, {'executed','rejected'})

    async def test_wb_submit(self):
        wb=WBStatisticsAPI()
        wb._request=AsyncMock(side_effect=[{'data':{'listGoods':[{'nmID':123,'discount':20}]}}, {'error':False,'data':{'id':12345}}])
        r=await wb.update_price_and_min_price(123, 299)
        self.assertEqual(r['status'],'submitted'); self.assertEqual(r['min_price_for_promotions'],275)
        self.assertFalse(r['min_price_set'])
        self.assertEqual(wb._request.await_args_list[1].kwargs['json_data']['data'][0], {'nmID':123,'price':374,'discount':20})
        wb._request=AsyncMock(side_effect=[{'data':{'listGoods':[{'nmID':123,'discount':20}]}}, {'error':True,'errorText':'bad'}])
        with self.assertRaises(ValueError): await wb.update_price_and_min_price(123, 299)

    async def test_wb_inventory(self):
        wb=WBStatisticsAPI(); wb._read_disk_cache=lambda *args, **kwargs: None; wb._write_disk_cache=lambda *args: None
        wb.fetch_wb_tariffs=AsyncMock(return_value={'commission_rate':.25,'logistics_rub':55})
        async def request(method,url,params=None,json_data=None):
            if '/list/goods/filter' in url:
                return {'data':{'listGoods':[{'nmID':123,'vendorCode':'1001','sizes':[{'discountedPrice':299}]}]}}
            if '/cards/list' in url:
                return {'cards':[{'nmID':123,'sizes':[{'skus':['abc']}]}], 'cursor':{'total':1}}
            if '/warehouses' in url: return [{'id':1},{'id':1},{'id':2}]
            if '/stocks/' in url: return {'stocks':[{'sku':'abc','amount':2},{'sku':'abc','amount':2}]}
            if url.endswith('/warehouse_remains'): return {'data':{'taskId':'test'}}
            if url.endswith('/status'): return {'data':{'status':'done'}}
            if url.endswith('/download'): return [{'nmId':123,'barcode':'abc','techSize':'0','warehouses':[{'warehouseName':'Всего находится на складах','quantity':5}]}]
            if '/supplier/orders' in url: return []
            raise AssertionError(url)
        wb._request=AsyncMock(side_effect=request)
        rows=await wb.fetch_stocks_and_prices(); self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['stock_fbo'],rows[0]['stock_fbs']),(5,4))

    async def test_ozon_payload(self):
        oz=OzonSellerAPI(); oz._request=AsyncMock(return_value={'result':[{'offer_id':'1001','updated':True}]})
        await oz.update_price('123','1001',299)
        item=oz._request.await_args.args[2]['prices'][0]
        self.assertNotIn('old_price',item); self.assertNotIn('min_price',item)

    async def test_unauthorized(self):
        from types import SimpleNamespace
        call=SimpleNamespace(from_user=SimpleNamespace(id=999), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data='act:app:1')
        await cb_approve_action(call)
        self.assertTrue(call.answer.await_args.kwargs['show_alert'])

    async def test_timeout_does_not_retry_or_unblock_group(self):
        from types import SimpleNamespace
        import uuid
        group=uuid.uuid4().hex
        a=await create_pending_action('ozon','1001','update_price',{'group_id':group,'offer_id':'1001','product_id':'123','new_price':299})
        b=await create_pending_action('ozon','1001','update_price',{'group_id':group,'offer_id':'1001','product_id':'123','new_price':500})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:app:{a.id}')
        with patch('bot.handlers.callbacks.ozon_api.update_price',new=AsyncMock(side_effect=TimeoutError('timeout'))) as update:
            await cb_approve_action(call)
            self.assertEqual(update.await_count,1)
        self.assertEqual((await get_pending_action(a.id)).status,'unknown')
        self.assertEqual((await get_pending_action(b.id)).status,'unknown')
        self.assertIsNone(await claim_price_action(a.id))

    async def test_rejection_closes_both(self):
        from types import SimpleNamespace
        import uuid
        group=uuid.uuid4().hex
        a=await create_pending_action('ozon','1001','update_price',{'group_id':group})
        b=await create_pending_action('ozon','1001','update_price',{'group_id':group})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:rej:{a.id}')
        await cb_reject_action(call)
        self.assertEqual((await get_pending_action(a.id)).status,'rejected')
        self.assertEqual((await get_pending_action(b.id)).status,'rejected')

    async def test_callback_ozon_and_wb(self):
        from types import SimpleNamespace
        a=await create_pending_action('ozon','1001','update_price',{'offer_id':'1001','product_id':'123','new_price':299})
        call=SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), message=SimpleNamespace(reply=AsyncMock()), data=f'act:app:{a.id}')
        with patch('bot.handlers.callbacks.ozon_api.update_price',new=AsyncMock(return_value={'result':[{'offer_id':'1001','updated':True,'errors':[]}]})):
            await cb_approve_action(call)
        self.assertEqual((await get_pending_action(a.id)).status,'executed')
        b=await create_pending_action('wb','1001','update_price',{'nm_id':123,'new_price':299})
        call.data=f'act:app:{b.id}'
        with patch('bot.handlers.callbacks.wb_api.update_price_and_min_price',new=AsyncMock(return_value={'status':'submitted','upload_id':42,'min_price_for_promotions':275})):
            await cb_approve_action(call)
        self.assertEqual((await get_pending_action(b.id)).status,'submitted')
        self.assertIn('ещё не подтверждена', call.message.reply.await_args.args[0])

if __name__ == '__main__': unittest.main()
```
