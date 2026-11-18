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
                if attempt == 0:
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
        target_after_discount = int(round(float(recommended_price_after_discount)))
        
        # Проверка кэша задач, чтобы избежать дублирования запросов в короткий промежуток
        last_time = self._task_cache.get(nm_id, 0)
        if time.time() - last_time < 300:
            return {"status": "already_processed", "nm_id": nm_id, "message": "Действие было обработано недавно"}

        min_action_price = int(round(target_after_discount * 0.92))
        current_discount = 0
        try:
            filter_resp = await self._request("GET", f"{self.PRICES_URL}/api/v2/list/goods/filter", params={"limit": 1000, "offset": 0})
            goods = filter_resp.get("data", {}).get("listGoods", []) if isinstance(filter_resp, dict) else []
            for g in goods:
                if int(g.get("nmID", 0)) == int(nm_id):
                    current_discount = int(g.get("discount", 0))
                    break
        except Exception:
            pass

        if 0 < current_discount < 95:
            price_before_discount = int(round(target_after_discount / (1.0 - (current_discount / 100.0))))
        else:
            price_before_discount = target_after_discount

        payload = {
            "data": [
                {
                    "nmID": int(nm_id),
                    "price": int(price_before_discount),
                    "discount": int(current_discount),
                }
            ]
        }

        res = await self._request("POST", f"{self.PRICES_URL}/api/v2/upload/task", json_data=payload)
        self._task_cache[nm_id] = time.time()
        
        return {
            "status": "success",
            "nm_id": int(nm_id),
            "seller_discount_pct": current_discount,
            "price_before_discount_set": int(price_before_discount),
            "price_after_discount_target": int(target_after_discount),
            "min_price_for_promotions": int(min_action_price),
            "api_response": res,
        }

    async def update_price(self, nm_id: str, new_price: float) -> Any:
        return await self.update_price_and_min_price(int(nm_id), float(new_price))

    async def fetch_stocks_and_prices(self) -> List[Dict[str, Any]]:
        cached = self._read_disk_cache("wb_stocks_and_prices", max_age_sec=180)
        if cached is not None:
            return cached

        prices_raw = await self._request("GET", f"{self.PRICES_URL}/api/v2/list/goods/filter", params={"limit": 1000, "offset": 0})
        tariffs = await self.fetch_wb_tariffs()

        fbo_stocks_map: Dict[int, int] = {}
        fbs_stocks_map: Dict[int, int] = {}
        barcode_to_nm: Dict[str, int] = {}

        try:
            cards_resp = await self._request(
                "POST",
                f"{self.CONTENT_URL}/content/v2/get/cards/list",
                json_data={"settings": {"cursor": {"limit": 100}, "filter": {"withPhoto": -1}}},
            )
            for card in (cards_resp.get("cards") or []) if isinstance(cards_resp, dict) else []:
                nm = int(card.get("nmID") or 0)
                for sz in card.get("sizes") or []:
                    for sk in sz.get("skus") or []:
                        if sk and nm:
                            barcode_to_nm[str(sk).strip()] = nm
        except Exception:
            pass

        try:
            wh_resp = await self._request("GET", f"{self.MARKETPLACE_URL}/api/v3/warehouses")
            if isinstance(wh_resp, list) and barcode_to_nm:
                all_skus = list(barcode_to_nm.keys())
                for w in wh_resp:
                    wh_id = w.get("id")
                    if not wh_id:
                        continue
                    try:
                        st_resp = await self._request(
                            "POST",
                            f"{self.MARKETPLACE_URL}/api/v3/stocks/{wh_id}",
                            json_data={"skus": all_skus},
                        )
                        for st_row in (st_resp.get("stocks") or []) if isinstance(st_resp, dict) else []:
                            bc = str(st_row.get("sku") or "").strip()
                            amt = int(st_row.get("amount") or 0)
                            nm = barcode_to_nm.get(bc)
                            if nm and amt > 0:
                                fbs_stocks_map[nm] = fbs_stocks_map.get(nm, 0) + amt
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            raw_stocks = await self._request("GET", f"{self.STATS_URL}/api/v1/supplier/stocks", params={"dateFrom": "2024-01-01"})
            if isinstance(raw_stocks, list):
                for s in raw_stocks:
                    nm = int(s.get("nmId") or 0)
                    qty = int(s.get("quantity") or 0)
                    if not nm or qty <= 0:
                        continue
                    wh_name = str(s.get("warehouseName") or "").lower().strip()
                    if nm in fbs_stocks_map and fbs_stocks_map[nm] > 0:
                        if not any(k in wh_name for k in ("маркетплейс", "продавц", "fbs", "офис", "дом", "собствен")) and s.get("isSupply") is True:
                            if any(w in wh_name for w in ("коледино", "электросталь", "казань", "подольск", "тула", "невинномысск", "краснодар", "екатеринбург")):
                                fbo_stocks_map[nm] = fbo_stocks_map.get(nm, 0) + qty
                    else:
                        if any(w in wh_name for w in ("коледино", "электросталь", "казань", "подольск", "тула", "невинномысск", "краснодар", "екатеринбург")):
                            fbo_stocks_map[nm] = fbo_stocks_map.get(nm, 0) + qty
                        else:
                            fbs_stocks_map[nm] = fbs_stocks_map.get(nm, 0) + qty
        except Exception:
            pass

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
        goods = prices_raw.get("data", {}).get("listGoods", []) if isinstance(prices_raw, dict) else []
        for g in goods:
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