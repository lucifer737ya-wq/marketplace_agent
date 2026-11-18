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