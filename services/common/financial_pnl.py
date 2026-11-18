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
