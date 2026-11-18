import asyncio
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import settings
from services.common.market_api import APIStatusError, MarketplaceClient, read_versions


class WBStatisticsAPI(MarketplaceClient):
    STATS_URL = "https://statistics-api.wildberries.ru"
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

    async def _request(self, method: str, url: str, params=None, json_data=None) -> Any:
        return await self.request_json(
            method,
            url,
            {"Authorization": settings.WB_API_TOKEN},
            params=params,
            body=json_data,
        )

    async def fetch_wb_tariffs(self) -> dict:
        today = datetime.utcnow().date().isoformat()
        key = "tariffs:" + today
        async with self._tariffs_lock:
            cached = self._read_disk_cache(key, 21600)
            if cached is not None:
                return cached
            commissions = await self._request(
                "GET", self.COMMON_URL + "/api/v1/tariffs/commission"
            )
            boxes = await self._request(
                "GET", self.COMMON_URL + "/api/v1/tariffs/box", params={"date": today}
            )
            subjects = {
                str(r["subjectID"]): {
                    "fbo": r.get("paidStorageKgvp"),
                    "fbs": r.get("kgvpMarketplace"),
                }
                for r in commissions["report"]
            }
            warehouses = {
                w["warehouseName"]: w
                for w in boxes["response"]["data"]["warehouseList"]
            }
            result = {"subjects": subjects, "warehouses": warehouses}
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
            self._write_disk_cache(key, rows)
        return rows

    async def fetch_wb_ad_spend(self, date_from: str, date_to: str) -> float:
        return sum(
            float(r["updSum"]) for r in await self._ad_charges(date_from, date_to)
        )

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
            raw_orders = await self._request(
                "GET",
                f"{self.STATS_URL}/api/v1/supplier/orders",
                params={"dateFrom": date_7d, "flag": 0},
            )
            if not isinstance(raw_orders, list):
                raise RuntimeError("WB: неизвестный формат заказов")
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
        except RuntimeError:
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
            url = self.STATS_URL + "/api/v1/supplier/stocks"
            rows = await self._request("GET", url, params={"dateFrom": "2019-06-20"})
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
        url += "/tasks/" + task["data"]["taskId"]
        for _ in range(12):
            state = (await self._request("GET", url + "/status"))["data"]["status"]
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
        ids = sorted(
            {
                int(a["advertId"])
                for g in groups.get("adverts") or []
                if g["status"] in (4, 9, 11)
                for a in g["advert_list"]
            }
        )
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
                cid = int(row.get("advertId") or row["id"])
                params = [
                    row.get("autoParams") or {},
                    *row.get("unitedParams", []),
                    *row.get("params", []),
                ]
                nms = {
                    str(n["nm"] if isinstance(n, dict) else n)
                    for p in params
                    for n in p.get("nms", [])
                }
                nms.update(str(n["nm_id"]) for n in row.get("nm_settings", []))
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
            cid = int(r["advertId"])
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
            campaign["spend_rub"] += float(r["updSum"])
        result = list(campaigns.values())
        self._write_disk_cache(key, result)
        return result

    async def fetch_real_finance_breakdown(
        self,
        days: int = 7,
        custom_date_from: Optional[str] = None,
        custom_date_to: Optional[str] = None,
    ) -> dict:
        end = datetime.utcnow().date()
        date_from = custom_date_from or (end - timedelta(days=days - 1)).isoformat()
        date_to = custom_date_to or end.isoformat()
        key = f"finance:{date_from}:{date_to}"
        async with self._orders_lock:
            cached = self._read_disk_cache(key, 900)
            if cached is not None:
                return cached

            async def read_report(version):
                rows, cursor = [], 0
                while True:
                    params = {
                        "dateFrom": date_from,
                        "dateTo": date_to,
                        "rrdid": cursor,
                        "limit": 100000,
                    }
                    url = (
                        f"{self.STATS_URL}/api/{version}/supplier/reportDetailByPeriod"
                    )
                    try:
                        page = await self._request("GET", url, params=params)
                    except APIStatusError as exc:
                        if not cursor or exc.status != 429 or exc.retry_after > 60:
                            raise
                        # A working version can throttle the next page. Keep its cursor before
                        # considering a complete restart on another version.
                        await asyncio.sleep(exc.retry_after or 60)
                        page = await self._request("GET", url, params=params)
                    if page is None or page == []:
                        return rows
                    if not isinstance(page, list):
                        raise RuntimeError("WB: неверный формат финансового отчёта")
                    for r in page:
                        if int(r["rrd_id"]) > cursor:
                            rows.append(r)
                    nxt = int(page[-1]["rrd_id"])
                    if nxt <= cursor:
                        raise RuntimeError("WB: курсор rrd_id не продвигается")
                    cursor = nxt

            rows = await read_versions(("v5", "v3", "v1"), read_report)
            ads = await self.fetch_wb_ad_spend(date_from, date_to)
            report = self._finance_rows(rows, ads, date_from, date_to)
            self._write_disk_cache(key, report)
            return report

    @staticmethod
    def _finance_rows(rows: list, ads: float, start: str, end: str) -> dict:
        orders = []
        seller = buyer = payout = commission = acquiring = returns = 0.0
        delivery = reverse = storage = acceptance = other = 0.0
        estimated = False
        seen = set()
        for r in rows:
            rid = r["rrd_id"]
            if rid in seen:
                continue
            seen.add(rid)
            operation = str(r.get("supplier_oper_name") or "").lower()
            logistics = float(r.get("delivery_rub") or 0)
            if r.get("return_amount") or "возврат" in operation:
                reverse += logistics
            else:
                delivery += logistics
            storage += float(r.get("storage_fee") or 0)
            acceptance += float(r.get("acceptance") or r.get("acceptance_fee") or 0)
            # Identifiable advertising deductions are already reported by /upd.
            reason = str(r.get("bonus_type_name") or "").lower()
            other += float(r.get("penalty") or 0)
            if not any(word in reason for word in ("реклам", "продвижен")):
                other += float(r.get("deduction") or 0)
            if operation not in {"продажа", "возврат"}:
                continue
            qty = int(r.get("quantity", 1))
            if qty <= 0:
                continue
            unit = float(r["retail_price_withdisc_rub"])
            gross = unit * qty
            paid = float(
                r.get("retail_amount") if r.get("retail_amount") is not None else gross
            )
            transfer = float(r["ppvz_for_pay"])
            if r.get("acquiring_fee") is None:
                fee = round(paid * settings.ACQUIRING_RATE, 2)
                estimated = True
            else:
                fee = float(r["acquiring_fee"])  # acquiring_bank is a bank NAME
            sign = -1 if operation == "возврат" else 1
            seller += sign * gross
            buyer += sign * paid
            payout += sign * transfer
            acquiring += sign * fee
            comm = gross - transfer - fee  # fee is already deducted from ppvz_for_pay
            commission += sign * comm
            if sign < 0:
                returns += gross
                continue
            orders.append(
                {
                    "order_id": f"wbf_{rid}",
                    "marketplace": "wb",
                    "sku": str(r.get("sa_name") or r["nm_id"]),
                    "created_at": r.get("sale_dt") or r["rr_dt"],
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
