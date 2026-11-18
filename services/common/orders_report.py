import asyncio
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from database.models import AsyncSessionLocal, OrderRecord, Product
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI
from products_catalog import PRODUCTS_CATALOG


async def sync_and_generate_orders_report(
    ozon_service: OzonSellerAPI,
    wb_service: WBStatisticsAPI,
    days: int = 7,
    custom_date_from: Optional[str] = None,
    custom_date_to: Optional[str] = None,
    report_title: str = "📊 Точный финансовый отчет (Данные ЛК Ozon и WB)",
) -> str:
    ozon_fin, wb_fin = await asyncio.gather(
        ozon_service.fetch_real_finance_breakdown(
            days=days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
        wb_service.fetch_real_finance_breakdown(
            days=days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
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
        products_by_sku: Dict[str, Product] = {p.sku: p for p in all_prods}
        for p in all_prods:
            if p.external_id:
                products_by_sku[p.external_id] = p

        all_new_orders = []
        if isinstance(ozon_fin, dict):
            all_new_orders.extend(ozon_fin.get("orders", []))
        if isinstance(wb_fin, dict):
            all_new_orders.extend(wb_fin.get("orders", []))

        for ord_data in all_new_orders:
            if not ord_data.get("order_id"):
                continue
            raw_sku = str(ord_data.get("sku", ""))
            clean_order = {
                "order_id": ord_data["order_id"],
                "marketplace": ord_data["marketplace"],
                "sku": ext_to_art.get(raw_sku, raw_sku),
                "created_at": ord_data["created_at"],
                "price": float(ord_data.get("price", 0.0)),
                "commission": float(ord_data.get("commission", 0.0)),
                "logistics": float(ord_data.get("logistics", 0.0)),
                "status": str(ord_data.get("status", "ordered")),
            }
            stmt = select(OrderRecord).where(OrderRecord.order_id == clean_order["order_id"])
            res = await session.execute(stmt)
            if not res.scalar_one_or_none():
                session.add(OrderRecord(**clean_order))
        await session.commit()

    def calc_cogs(orders_list: List[Dict[str, Any]]) -> float:
        total_cogs = 0.0
        for o in orders_list:
            raw_sku = str(o.get("sku", ""))
            lookup_sku = ext_to_art.get(raw_sku, raw_sku)
            p = products_by_sku.get(lookup_sku) or products_by_sku.get(raw_sku)
            if p:
                total_cogs += (p.cost_price + p.packaging_cost + p.first_mile_unit_cost)
        return total_cogs

    def pct(val: float, base: float) -> str:
        return f"{(val / base * 100):.1f}%" if base > 0 else "0.0%"

    # --- БЛОК OZON ---
    if isinstance(ozon_fin, Exception):
        ozon_block = f"🔵 <b>OZON:</b>\n❌ Ошибка API Ozon:\n<code>{ozon_fin}</code>\n\n"
        oz_sales = oz_net_profit = oz_cogs = oz_ded_tot = oz_tax = 0.0
        oz_count = 0
    else:
        oz_count = int(ozon_fin.get("orders_count", 0))
        oz_sales = float(ozon_fin.get("sales_total", 0.0))
        oz_comm = float(ozon_fin.get("commission", 0.0))

        oz_deliv = float(ozon_fin.get("deliv_total", 0.0))
        oz_d_dir = float(ozon_fin.get("deliv_direct", 0.0))
        oz_d_ret = float(ozon_fin.get("deliv_return", 0.0))
        oz_d_ho = float(ozon_fin.get("deliv_ozon_handover", 0.0))

        oz_part = float(ozon_fin.get("partners_total", 0.0))
        oz_dropoff = float(ozon_fin.get("partner_dropoff", 0.0))
        oz_pack = float(ozon_fin.get("partner_pack", 0.0))
        oz_pret = float(ozon_fin.get("partner_returns", 0.0))
        oz_acq = float(ozon_fin.get("acquiring", 0.0))
        oz_star = float(ozon_fin.get("star_products", 0.0))
        oz_plm = float(ozon_fin.get("partner_last_mile", 0.0))

        oz_fbo = float(ozon_fin.get("fbo_storage", 0.0))
        oz_ads = float(ozon_fin.get("ads_total", 0.0))
        oz_cpo = float(ozon_fin.get("promo_cpo", 0.0))
        oz_cpc = float(ozon_fin.get("promo_cpc", 0.0))
        oz_fines = float(ozon_fin.get("other_fines", 0.0))
        oz_decomp = float(ozon_fin.get("decompensations", 0.0))
        oz_mutual = float(ozon_fin.get("mutual_settlements", 0.0))
        oz_ded_tot = float(ozon_fin.get("total_deductions", 0.0))
        oz_payout = float(ozon_fin.get("total_payout", 0.0))

        oz_cogs = calc_cogs(ozon_fin.get("orders", []))
        oz_tax = oz_sales * 0.06
        oz_net_profit = oz_payout - oz_cogs - oz_tax

        ozon_block = (
            f"🔵 <b>OZON ({ozon_fin.get('source')}):</b>\n"
            f"• Заказано единиц: <b>{oz_count} шт.</b>\n"
            f"• <b>Продажи и возвраты: {oz_sales:,.0f} ₽</b> (100%)\n"
            f"• <b>Начисления МП (всего): -{oz_ded_tot:,.0f} ₽</b> ({pct(oz_ded_tot, oz_sales)})\n\n"
            f"<b>Детализация расходов Ozon:</b>\n"
            f"• <b>Вознаграждение Ozon: -{oz_comm:,.0f} ₽</b> ({pct(oz_comm, oz_sales)})\n"
            f"• <b>Услуги доставки: -{oz_deliv:,.0f} ₽</b> ({pct(oz_deliv, oz_sales)})\n"
            f"  ├ Логистика (прямая): -{oz_d_dir:,.0f} ₽\n"
            f"  ├ Обратная логистика: -{oz_d_ret:,.0f} ₽\n"
            f"  └ Доставка до места выдачи Ozon: -{oz_d_ho:,.0f} ₽\n"
            f"• <b>Услуги партнёров: -{oz_part:,.0f} ₽</b> ({pct(oz_part, oz_sales)})\n"
            f"  ├ Обработка Drop-off (сдача в ПВЗ): -{oz_dropoff:,.0f} ₽\n"
            f"  ├ Упаковка товара партнёрами: -{oz_pack:,.0f} ₽\n"
            f"  ├ Обработка возвратов и невыкупов: -{oz_pret:,.0f} ₽\n"
            f"  ├ Эквайринг: -{oz_acq:,.0f} ₽ ({pct(oz_acq, oz_sales)})\n"
            f"  ├ Звёздные товары: -{oz_star:,.0f} ₽\n"
            f"  └ Доставка до места выдачи партнёрами: -{oz_plm:,.0f} ₽\n"
            f"• <b>Услуги FBO (хранение на складе): -{oz_fbo:,.0f} ₽</b> ({pct(oz_fbo, oz_sales)})\n"
            f"• <b>Продвижение и реклама: -{oz_ads:,.0f} ₽</b> ({pct(oz_ads, oz_sales)})\n"
            f"  ├ Продвижение с оплатой за заказ: -{oz_cpo:,.0f} ₽\n"
            f"  └ Оплата за клик / Трафареты: -{oz_cpc:,.0f} ₽\n"
            f"• <b>Другие услуги и штрафы: -{oz_fines:,.0f} ₽</b> ({pct(oz_fines, oz_sales)})\n"
            f"• <b>Компенсации и декомпенсации: -{oz_decomp:,.0f} ₽</b>\n"
            f"• <b>Взаимозачет между договорами: -{oz_mutual:,.0f} ₽</b>\n\n"
            f"💳 <b>Всего к выплате Ozon: {oz_payout:,.0f} ₽</b>\n"
            f"📦 Себестоимость + Упаковка: <b>-{oz_cogs:,.0f} ₽</b> ({pct(oz_cogs, oz_sales)})\n"
            f"🏛 Налог УСН (6%): <b>-{oz_tax:,.0f} ₽</b> (6.0%)\n"
            f"👉 <b>Чистая прибыль Ozon:</b> <code>{oz_net_profit:,.0f} ₽</code> (<b>{pct(oz_net_profit, oz_sales)}</b>)\n\n"
        )

    # --- БЛОК WILDBERRIES ---
    if isinstance(wb_fin, Exception):
        wb_block = f"🟣 <b>WILDBERRIES:</b>\n❌ Ошибка API WB: <code>{wb_fin}</code>\n\n"
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = 0.0
        wb_count = 0
    else:
        wb_count = int(wb_fin.get("orders_count", 0))
        wb_rev_s = float(wb_fin.get("revenue_seller", 0.0))
        wb_rev_b = float(wb_fin.get("revenue_buyer", wb_rev_s))
        wb_ret = float(wb_fin.get("returns_amount", 0.0))
        wb_comm = float(wb_fin.get("commission", 0.0))
        wb_acq = float(wb_fin.get("acquiring", 0.0))
        wb_log = float(wb_fin.get("logistics", 0.0))
        wb_log_dir = float(wb_fin.get("logistics_direct", wb_log))
        wb_log_ret = float(wb_fin.get("logistics_return", 0.0))
        wb_stor = float(wb_fin.get("storage", 0.0))
        wb_acc = float(wb_fin.get("acceptance", 0.0))
        wb_ads = float(wb_fin.get("ads", 0.0))
        wb_other = float(wb_fin.get("other_services", 0.0))
        wb_ded_tot = float(wb_fin.get("total_deductions", wb_comm + wb_acq + wb_log + wb_stor + wb_acc + wb_ads + wb_other))
        wb_payout = float(wb_fin.get("total_payout", wb_rev_s - wb_ded_tot))

        wb_cogs = calc_cogs(wb_fin.get("orders", []))
        wb_tax = wb_rev_b * 0.06
        wb_net_profit = wb_payout - wb_cogs - wb_tax

        wb_block = (
            f"🟣 <b>WILDBERRIES ({wb_fin.get('source')}):</b>\n"
            f"• Заказано единиц: <b>{wb_count} шт.</b> (Отмены/возвраты: {wb_ret:,.0f} ₽)\n"
            f"• <b>Продажи в ценах ЛК (priceWithDisc): {wb_rev_s:,.0f} ₽</b> (100%)\n"
            f"• <b>Цена для покупателя с СПП (база УСН): {wb_rev_b:,.0f} ₽</b>\n"
            f"• <b>Удержания WB (всего): -{wb_ded_tot:,.0f} ₽</b> ({pct(wb_ded_tot, wb_rev_s)})\n\n"
            f"<b>Детализация расходов Wildberries:</b>\n"
            f"• <b>Вознаграждение WB (с НДС): -{wb_comm:,.0f} ₽</b> ({pct(wb_comm, wb_rev_s)})\n"
            f"• <b>Эквайринг WB: -{wb_acq:,.0f} ₽</b> ({pct(wb_acq, wb_rev_s)})\n"
            f"• <b>Услуги логистики WB: -{wb_log:,.0f} ₽</b> ({pct(wb_log, wb_rev_s)})\n"
            f"  ├ Прямая доставка до покупателя: -{wb_log_dir:,.0f} ₽\n"
            f"  └ Обратная логистика (возвраты/отмены): -{wb_log_ret:,.0f} ₽\n"
            f"• <b>Платное хранение на складах WB: -{wb_stor:,.0f} ₽</b> ({pct(wb_stor, wb_rev_s)})\n"
            f"• <b>Платная приёмка товара: -{wb_acc:,.0f} ₽</b> ({pct(wb_acc, wb_rev_s)})\n"
            f"• <b>ВБ.Продвижение (Реклама): -{wb_ads:,.0f} ₽</b> ({pct(wb_ads, wb_rev_s)})\n"
            f"• <b>Штрафы и прочие удержания: -{wb_other:,.0f} ₽</b> ({pct(wb_other, wb_rev_s)})\n\n"
            f"💳 <b>Всего к перечислению WB: {wb_payout:,.0f} ₽</b>\n"
            f"📦 Себестоимость + Упаковка: <b>-{wb_cogs:,.0f} ₽</b> ({pct(wb_cogs, wb_rev_s)})\n"
            f"🏛 Налог УСН 6% (с {wb_rev_b:,.0f} ₽): <b>-{wb_tax:,.0f} ₽</b>\n"
            f"👉 <b>Чистая прибыль WB:</b> <code>{wb_net_profit:,.0f} ₽</code> (<b>{pct(wb_net_profit, wb_rev_s)}</b>)\n\n"
        )

    tot_sales = oz_sales + wb_rev_s
    tot_ded = oz_ded_tot + wb_ded_tot
    tot_cogs = oz_cogs + wb_cogs
    tot_tax = oz_tax + wb_tax
    tot_profit = oz_net_profit + wb_net_profit

    return (
        f"<b>{report_title}</b>\n\n"
        f"{ozon_block}"
        f"{wb_block}"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏁 <b>СВОДНЫЙ ИТОГ (Ozon + WB):</b>\n"
        f"• Всего единиц: <b>{oz_count + wb_count} шт.</b>\n"
        f"• Общий оборот (в ценах ЛК): <b>{tot_sales:,.0f} ₽</b>\n"
        f"• Все удержания маркетплейсов: <b>-{tot_ded:,.0f} ₽</b> ({pct(tot_ded, tot_sales)})\n"
        f"• Себестоимость и упаковка: <b>-{tot_cogs:,.0f} ₽</b> ({pct(tot_cogs, tot_sales)})\n"
        f"• Налог УСН 6%: <b>-{tot_tax:,.0f} ₽</b>\n"
        f"💰 <b>ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <code>{tot_profit:,.0f} ₽</code> (<b>{pct(tot_profit, tot_sales)} ROS</b>)"
    )