import asyncio
import math
from collections import Counter, defaultdict
from decimal import Decimal
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

from config import settings
from products_catalog import PRODUCTS_CATALOG
from services.ozon.finance import (
    book_statement,
    book_transaction,
    category,
    complete_statement,
    totals,
)
from services.common.market_api import APIStatusError, MarketplaceClient, read_versions


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

    async def _fetch_cash_flow(
        self, from_date: str, to_date: str
    ) -> tuple[list, list, str]:
        """Primary finance source after v3/transaction/list was retired (2026)."""
        cash_flows: list = []
        details_list: list = []
        page = 1
        while True:
            cf = await self._request(
                "POST",
                "/v1/finance/cash-flow-statement/list",
                {
                    "date": {"from": from_date, "to": to_date},
                    "page": page,
                    "page_size": 100,
                    "with_details": True,
                },
            )
            result = cf.get("result") or cf
            flows = result.get("cash_flows") or []
            cash_flows.extend(flows)
            details = result.get("details") or []
            if isinstance(details, dict):
                details_list.append(details)
            elif isinstance(details, list):
                details_list.extend(details)
            page_count = int(result.get("page_count") or cf.get("page_count") or 1)
            if page >= page_count or not flows:
                break
            page += 1
        return cash_flows, details_list, "cash-flow-statement"

    async def _fetch_accrual_by_day(
        self, start: date, end: date
    ) -> tuple[dict, str]:
        """
        Fallback after /v3/finance/transaction/list was disabled.
        New official endpoints: /v1/finance/accrual/by-day (+ types).
        """
        book: dict = defaultdict(Decimal)
        current = start
        while current <= end:
            last_id = ""
            for _ in range(500):  # hard safety
                payload = {"date": current.isoformat()}
                if last_id:
                    payload["last_id"] = last_id
                try:
                    data = await self._request(
                        "POST", "/v1/finance/accrual/by-day", payload
                    )
                except APIStatusError as exc:
                    if exc.status in {400, 404, 410}:
                        # method may be unavailable for some accounts — stop fallback
                        raise
                    raise
                accruals = data.get("accruals") or data.get("result", {}).get("accruals") or []
                for item in accruals:
                    if not isinstance(item, dict):
                        continue
                    amount = Decimal(str(
                        (item.get("total_amount") or {}).get("amount")
                        or item.get("amount")
                        or 0
                    ))
                    type_name = str(
                        item.get("accrued_category")
                        or item.get("type")
                        or item.get("operation_type")
                        or "other"
                    )
                    # Map coarse categories into the ledger used by totals()
                    key = category(type_name, "other_fines")
                    if "sale" in type_name.lower() or "продаж" in type_name.lower():
                        book["sales_total"] += abs(amount)
                    elif "commission" in type_name.lower() or "комисс" in type_name.lower():
                        book["commission"] += abs(amount)
                    else:
                        book[key] += abs(amount)
                last_id = str(data.get("last_id") or "")
                if not last_id or not accruals:
                    break
            current += timedelta(days=1)
        return dict(book), "accrual/by-day"

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Актуальный финансовый срез (ноябрь 2026):
        1) /v1/finance/cash-flow-statement/list (with_details) — основной источник;
        2) /v1/finance/accrual/by-day — fallback после отключения /v3/finance/transaction/list.
        Старый /v3/finance/transaction/list больше не вызывается.
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
        cache_key = f"finance:{from_date}:{to_date}"

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
            source = ""
            orders_list: List[Dict[str, Any]] = []

            # 1) Primary: cash-flow-statement
            try:
                cash_flows, details_list, src = await self._fetch_cash_flow(
                    from_date, to_date
                )
                if cash_flows or details_list:
                    if complete_statement(
                        cash_flows, start_dt.date(), end_dt.date()
                    ) or details_list:
                        statement = totals(book_statement(cash_flows, details_list))
                        report_totals.update(statement)
                        source = src
                    else:
                        source = f"{src} (неполное покрытие периода)"
                        statement = totals(book_statement(cash_flows, details_list))
                        report_totals.update(statement)
                else:
                    source = "cash-flow пуст"
            except APIStatusError as exc:
                if exc.status not in {400, 404, 410, 429}:
                    raise
                source = f"cash-flow HTTP {exc.status}"

            # 2) Fallback: accrual/by-day (official replacement of v3/transaction/list)
            if report_totals.get("sales_total", 0) == 0 and report_totals.get(
                "total_deductions", 0
            ) == 0:
                try:
                    book, src = await self._fetch_accrual_by_day(
                        start_dt.date(), end_dt.date()
                    )
                    # Normalize into the same shape as totals()
                    normalized = totals(defaultdict(Decimal, book))
                    report_totals.update(normalized)
                    source = (source + "; " if source else "") + src
                except APIStatusError as exc:
                    source = (source + "; " if source else "") + f"accrual HTTP {exc.status}"
                except Exception as exc:
                    source = (source + "; " if source else "") + f"accrual {type(exc).__name__}"

            report = dict(
                report_totals,
                orders=orders_list,
                orders_count=len(orders_list),
                billed_skus=[],
                source=f"Ozon {source or 'нет данных'} ({period_label})",
            )
            self._write_disk_cache(cache_key, report)
            return report

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
        payload = {
            "prices": [
                {
                    "auto_action_enabled": "UNKNOWN",
                    "currency_code": "RUB",
                    "min_price": str(round(new_price * 0.7, 2)),
                    "offer_id": offer_id.strip(),
                    "old_price": "0",
                    "price": str(round(new_price, 2)),
                    "product_id": int(product_id),
                }
            ]
        }
        return await self._request("POST", "/v1/product/import/prices", payload)
