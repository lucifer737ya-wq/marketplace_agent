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
