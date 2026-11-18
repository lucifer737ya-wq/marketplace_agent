import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from database.models import AsyncSessionLocal, Product
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI
from services.common.orders_report import sync_and_generate_orders_report
from products_catalog import PRODUCTS_CATALOG

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def get_previous_month_bounds(ref_date: Optional[datetime] = None) -> Dict[str, str]:
    """Возвращает точные границы предыдущего календарного месяца (YYYY-MM-01 .. YYYY-MM-last)."""
    now = ref_date or datetime.utcnow()
    first_day_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day_prev = first_day_current - timedelta(days=1)
    first_day_prev = last_day_prev.replace(day=1)
    month_title = f"{MONTH_NAMES_RU.get(first_day_prev.month, '')} {first_day_prev.year}"
    return {
        "date_from": first_day_prev.strftime("%Y-%m-%d"),
        "date_to": last_day_prev.strftime("%Y-%m-%d"),
        "title": month_title,
        "days_count": (last_day_prev - first_day_prev).days + 1,
    }


async def generate_monthly_pnl_report(
    ozon_service: Optional[OzonSellerAPI] = None,
    wb_service: Optional[WBStatisticsAPI] = None,
) -> str:
    """
    Формирует полный P&L отчет по итогам ПРОШЛОГО МЕСЯЦА, забирая реальные расходы из Ozon и WB.
    Учитывает регламент публикации закрывающих отчетов в ЛК (5-го числа каждого месяца).
    """
    oz_api = ozon_service or OzonSellerAPI()
    wb_api = wb_service or WBStatisticsAPI()

    bounds = get_previous_month_bounds()
    now_day = datetime.utcnow().day

    publication_notice = ""
    if now_day < 5:
        publication_notice = (
            "⚠️ <i>Примечание: сейчас начало месяца ({now_day}-е число). Ozon и Wildberries публикуют "
            "финальные закрывающие акты и взаиморасчеты за {title} до 4–5 числа. "
            "Финальный сверенный отчет придет автоматически 5-го числа в 09:30.</i>\n\n"
        ).format(now_day=now_day, title=bounds["title"])

    report_body = await sync_and_generate_orders_report(
        ozon_service=oz_api,
        wb_service=wb_api,
        days=bounds["days_count"],
        custom_date_from=bounds["date_from"],
        custom_date_to=bounds["date_to"],
        report_title=f"📅 ИТОГОВЫЙ P&L ОТЧЕТ ЗА ПРОШЛЫЙ МЕСЯЦ: {bounds['title'].upper()} ({bounds['date_from']} .. {bounds['date_to']})",
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
    2. ТОП-5 убыточных / низкорентабельных товаров (с учетом всех расходов МП, себестоимости и УСН 6%).
    """
    oz_api = ozon_service or OzonSellerAPI()
    wb_api = wb_service or WBStatisticsAPI()

    oz_fin, wb_fin, oz_prices, wb_prices = await asyncio.gather(
        oz_api.fetch_real_finance_breakdown(days=days),
        wb_api.fetch_real_finance_breakdown(days=days),
        oz_api.fetch_stocks_and_prices(),
        wb_api.fetch_stocks_and_prices(),
        return_exceptions=True,
    )

    ext_to_art: Dict[str, str] = {}
    for art_key, meta in PRODUCTS_CATALOG.items():
        if meta.get("ozon_sku"):
            ext_to_art[str(meta["ozon_sku"])] = art_key
        if meta.get("wb_sku"):
            ext_to_art[str(meta["wb_sku"])] = art_key

    async with AsyncSessionLocal() as session:
        prods_res = await session.execute(select(Product))
        all_prods = prods_res.scalars().all()
        products_map: Dict[str, Product] = {f"{p.marketplace}_{p.sku}": p for p in all_prods}
        for p in all_prods:
            if p.sku not in products_map:
                products_map[p.sku] = p

    live_tariffs: Dict[str, Dict[str, Any]] = {}
    if isinstance(oz_prices, list):
        for it in oz_prices:
            live_tariffs[f"ozon_{it['sku']}"] = it
    if isinstance(wb_prices, list):
        for it in wb_prices:
            live_tariffs[f"wb_{it['sku']}"] = it

    # Доля общемагазинных расходов Ozon (доставка + партнеры + реклама + FBO), распределенная на выручку
    oz_overhead_ratio = 0.35
    if isinstance(oz_fin, dict) and float(oz_fin.get("sales_total", 0.0)) > 0:
        oz_sales = float(oz_fin["sales_total"])
        oz_ded = float(oz_fin.get("total_deductions", 0.0))
        oz_overhead_ratio = min(max(oz_ded / oz_sales, 0.20), 0.85)

    sku_stats: Dict[str, Dict[str, Any]] = {}

    all_orders = []
    if isinstance(oz_fin, dict):
        all_orders.extend(oz_fin.get("orders", []))
    if isinstance(wb_fin, dict):
        all_orders.extend(wb_fin.get("orders", []))

    for o in all_orders:
        mp = str(o.get("marketplace", "ozon"))
        raw_sku = str(o.get("sku", ""))
        art = ext_to_art.get(raw_sku, raw_sku)
        key = f"{mp}_{art}"

        price = float(o.get("price", 0.0))
        buyer_p = float(o.get("buyer_price") or price)

        p_db = products_map.get(key) or products_map.get(art)
        cogs = (p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost) if p_db else 70.0
        if cogs <= 0:
            cogs = 70.0

        t_live = live_tariffs.get(key, {})
        if mp == "ozon":
            mp_fee = price * oz_overhead_ratio
            if t_live.get("buyer_price"):
                buyer_p = float(t_live["buyer_price"])
        else:
            comm = float(o.get("commission", 0.0))
            log = float(o.get("logistics", 0.0))
            mp_fee = comm + log

        tax_usn = buyer_p * 0.06
        net_profit = price - cogs - mp_fee - tax_usn

        if key not in sku_stats:
            cat_meta = PRODUCTS_CATALOG.get(art, {})
            title = cat_meta.get("name") or (p_db.title if p_db and p_db.title else f"Арт. {art}")
            sku_stats[key] = {
                "mp": mp.upper(),
                "sku": art,
                "title": title,
                "qty": 0,
                "revenue": 0.0,
                "cogs": 0.0,
                "mp_fee": 0.0,
                "tax": 0.0,
                "profit": 0.0,
            }

        st = sku_stats[key]
        st["qty"] += 1
        st["revenue"] += price
        st["cogs"] += cogs
        st["mp_fee"] += mp_fee
        st["tax"] += tax_usn
        st["profit"] += net_profit

    if not sku_stats:
        return "📈 <b>Еженедельная сводка ТОП-5 товаров:</b> за последние 7 дней заказов не найдено."

    items_list = list(sku_stats.values())
    top_popular = sorted(items_list, key=lambda x: (x["qty"], x["revenue"]), reverse=True)[:5]
    top_unprofitable = sorted(items_list, key=lambda x: x["profit"])[:5]

    lines = [
        f"📊 <b>ЕЖЕНЕДЕЛЬНАЯ СВОДКА ТОВАРОВ (За {days} дней)</b>\n",
        "🏆 <b>ТОП-5 ПОПУЛЯРНЫХ ТОВАРОВ (Бестселлеры):</b>",
    ]
    for i, it in enumerate(top_popular, 1):
        ros = (it["profit"] / it["revenue"] * 100) if it["revenue"] > 0 else 0.0
        unit_prof = it["profit"] / max(it["qty"], 1)
        lines.append(
            f"{i}. <b>[{it['mp']}] Арт. <code>{it['sku']}</code></b> — {it['title'][:38]}\n"
            f"   • Продано: <b>{it['qty']} шт.</b> на <b>{it['revenue']:,.0f} ₽</b>\n"
            f"   • Чистая прибыль: <code>{it['profit']:,.0f} ₽</code> ({unit_prof:+.0f} ₽/шт. | ROS {ros:.1f}%)"
        )

    lines.append("\n🚨 <b>ТОП-5 УБЫТОЧНЫХ / НИЗКОМАРЖИНАЛЬНЫХ ТОВАРОВ:</b>")
    for i, it in enumerate(top_unprofitable, 1):
        ros = (it["profit"] / it["revenue"] * 100) if it["revenue"] > 0 else 0.0
        unit_prof = it["profit"] / max(it["qty"], 1)
        status_icon = "🔴 Убыток" if it["profit"] < 0 else "🟡 Низкая маржа"
        lines.append(
            f"{i}. {status_icon} <b>[{it['mp']}] Арт. <code>{it['sku']}</code></b> — {it['title'][:34]}\n"
            f"   • Заказов: <b>{it['qty']} шт.</b> | Выручка: {it['revenue']:,.0f} ₽ | Удержания МП: -{it['mp_fee']:,.0f} ₽\n"
            f"   • Себес: -{it['cogs']:,.0f} ₽ | Налог: -{it['tax']:,.0f} ₽ 👉 Итог: <code>{it['profit']:,.0f} ₽</code> ({unit_prof:+.0f} ₽/шт.)"
        )

    return "\n".join(lines)


async def generate_weekly_pnl_report() -> str:
    """Обратная совместимость: возвращает месячный P&L + еженедельный ТОП-5."""
    monthly = await generate_monthly_pnl_report()
    weekly_top = await generate_weekly_top_and_unprofitable_report()
    return f"{monthly}\n\n➖➖➖➖➖➖➖➖➖➖\n\n{weekly_top}"