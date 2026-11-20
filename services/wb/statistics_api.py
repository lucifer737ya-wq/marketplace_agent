import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import settings
from services.common.timeutil import today_local
from services.common.market_api import APIStatusError, MarketplaceClient, read_versions

logger = logging.getLogger(__name__)


class AmbiguousFinancePeriod(RuntimeError):
    """Безопасная диагностическая строка о границах сводного отчёта."""




def _exc_label(exc: BaseException) -> str:
    if isinstance(exc, APIStatusError):
        return f"HTTP {exc.status}"
    if isinstance(exc, AmbiguousFinancePeriod):
        return str(exc)
    # Собственные диагностические строки модуля («WB: ...») не содержат ключей и
    # данных; раньше вместо них в отчёт попадало безликое «RuntimeError».
    if isinstance(exc, RuntimeError) and str(exc).startswith("WB"):
        return str(exc)[:200]
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
        # WB отключил legacy /api/v1/supplier/stocks и /api/v5/supplier/reportDetailByPeriod:
        # после 404 не дёргаем их 6 часов, чтобы не засорять журнал и самопроверку.
        self._fbo_legacy_retry_at = 0.0
        self._finance_legacy_retry_at = 0.0

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
        today = today_local().isoformat()
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
            "min_price_for_promotions": int(round(target * settings.WB_MIN_PRICE_RATIO)),
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
                for row in (st.get("stocks") or []) if isinstance(st, dict) else []:
                    if use_chrt:
                        raw_ident = row.get("chrtId") if row.get("chrtId") is not None else row.get("chrtID")
                        ident = int(raw_ident) if raw_ident is not None else None
                    else:
                        ident = str(row.get("sku") or "")
                    if ident not in mapping:
                        raise RuntimeError(
                            "WB FBS: не удалось сопоставить размер со складской позицией"
                        )
                    if ident in seen:
                        continue
                    seen.add(ident)
                    nm = mapping[ident]
                    fbs_stocks_map[nm] = fbs_stocks_map.get(nm, 0) + max(
                        0, int(row.get("amount") or 0)
                    )

        fbo_stocks_map, locations = await read_versions(
            ("supplier", "analytics"), self._fetch_fbo
        )

        buyer_price_map: Dict[int, float] = {}
        sales_count_7d = {}
        end = today_local()
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
            acquiring_val = round(max(buyer_price, seller_price) * settings.ACQUIRING_RATE, 2)

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
        service_wh_names = {
            "всего находится на складах",
            "в пути к получателю",
            "в пути возвраты на склад wb",
            "в пути до получателя",
            "в пути возвраты на склад",
        }
        download_rows = await self._request("GET", url + "/download")
        if not isinstance(download_rows, list):
            raise RuntimeError("WB FBO: неверный формат выгрузки warehouse_remains")
        for row in download_rows:
            if not isinstance(row, dict) or row.get("nmId") is None:
                continue
            nm = int(row["nmId"])
            warehouses_list = row.get("warehouses") or []
            totals = [
                w
                for w in warehouses_list
                if isinstance(w, dict)
                and str(w.get("warehouseName") or "").strip().lower()
                == "всего находится на складах"
            ]
            if len(totals) != 1 or nm in quantities:
                raise RuntimeError("WB FBO: неоднозначные остатки")
            quantities[nm] = max(0, int(totals[0].get("quantity") or 0))
            for w in warehouses_list:
                if not isinstance(w, dict):
                    continue
                wh_name = str(w.get("warehouseName") or "").strip()
                wh_qty = max(0, int(w.get("quantity") or 0))
                if wh_name and wh_qty > 0 and wh_name.lower() not in service_wh_names:
                    locations.setdefault(nm, set()).add(wh_name)
        return quantities, locations

    async def fetch_active_ad_campaigns(self, days: int = 7) -> list:
        end = today_local()
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
                # v3/v2 отдают {"adverts": [...]}; отсутствие ключа — не KeyError.
                rows = data.get("adverts") if isinstance(data, dict) else data
                return rows if isinstance(rows, list) else []

            # v3 — актуальная версия (v2 помечена WB как deprecated и будет отключена),
            # старый POST /adv/v1/promotion/adverts оставлен последним резервом.
            for row in await read_versions(
                (
                    "/api/advert/v3/adverts",
                    "/api/advert/v2/adverts",
                    "/adv/v1/promotion/adverts",
                ),
                details,
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
        if time.monotonic() < self._finance_legacy_retry_at:
            raise APIStatusError(
                404, "WB: legacy reportDetailByPeriod отключён (пауза 6 ч)"
            )
        rows: list = []
        cursor = 0
        for _ in range(200):
            try:
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
            except APIStatusError as exc:
                if exc.status in {404, 410}:
                    self._finance_legacy_retry_at = time.monotonic() + 6 * 3600
                    logger.warning(
                        "WB legacy /api/v5/supplier/reportDetailByPeriod недоступен (HTTP %s): "
                        "пауза повторных обращений 6 часов",
                        exc.status,
                    )
                raise
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
        end = today_local()
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
        end = today_local()
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
                    if isinstance(exc, APIStatusError) and exc.status == 429:
                        # Лимит на /sales-reports/detailed уже исчерпан — не добиваем daily.
                        break
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
                if not report.get("orders_count"):
                    # Строки есть, а продаж/возвратов нет: чаще всего неделя ещё не закрыта
                    # (отчёт реализации формируется после воскресенья) или WB сменил название
                    # операции. Показываем, что именно пришло, и сверяем с Sales API.
                    ops = report.get("operation_counts") or {}
                    shown = ", ".join(f"{k}×{v}" for k, v in sorted(ops.items())[:8]) or "нет"
                    report["source"] += f"; в отчёте нет строк «Продажа/Возврат» (операции: {shown})"
                    try:
                        hint = await self._sales_api_summary(date_from, date_to)
                    except (APIStatusError, RuntimeError, ValueError, TypeError) as exc:
                        report["source"] += f"; Sales API: {_exc_label(exc)}"
                    else:
                        report["sales_api"] = hint
                        if hint["count"]:
                            report["source"] += (
                                f"; ⚠ Sales API видит {hint['count']} продаж на "
                                f"{hint['price_with_disc']:.0f} ₽ — отчёт реализации ещё не закрыт"
                            )
            self._write_disk_cache(key, report)
            return report

    async def _sales_api_summary(self, date_from: str, date_to: str) -> dict:
        """Сверка: GET /api/v1/supplier/sales (оперативные продажи, без удержаний)."""
        rows = await self._request(
            "GET",
            self.STATS_URL + "/api/v1/supplier/sales",
            params={"dateFrom": date_from, "flag": 0},
        )
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise RuntimeError("WB: неверный формат Sales API")
        count = 0
        total = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            day = str(row.get("date") or "")[:10]
            if day and not (date_from <= day <= date_to):
                continue
            sale_id = str(row.get("saleID") or "")
            if sale_id.startswith("R"):  # возврат
                total -= _num(row.get("priceWithDisc"))
                count -= 1
                continue
            total += _num(row.get("priceWithDisc"))
            count += 1
        return {"count": max(count, 0), "price_with_disc": round(max(total, 0.0), 2)}

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
        op_counts: Dict[str, int] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = _rrd(r)
            if rid in seen:
                continue
            seen.add(rid)
            operation = str(_pick(r, "supplierOperName", "supplier_oper_name") or "").strip().lower()
            op_counts[operation or "—"] = op_counts.get(operation or "—", 0) + 1
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
            "operation_counts": op_counts,
            "rows_total": len(seen),
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
