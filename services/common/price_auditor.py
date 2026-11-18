from typing import Dict, Any, List, Tuple, Optional
from sqlalchemy import select
from database.models import AsyncSessionLocal, Product
from database.crud import create_pending_action
from bot.handlers.callbacks import build_approval_keyboard, build_dual_ozon_approval_keyboard
from products_catalog import PRODUCTS_CATALOG

_WORKING_PROXY_URL: Optional[str] = "__UNSET__"


async def send_tg_safe(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """Мгновенно выводит отчет в консоль и отправляет в Telegram через запомненный рабочий прокси."""
    global _WORKING_PROXY_URL
    clean_console = (
        text.replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
        .replace("<code>", "")
        .replace("</code>", "")
    )
    print(f"\n{clean_console}\n" + "-" * 55)

    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from config import settings
    from services.common.ai_advisory_board import PROXY_CANDIDATES

    if _WORKING_PROXY_URL != "__UNSET__":
        try:
            session = AiohttpSession(proxy=_WORKING_PROXY_URL) if _WORKING_PROXY_URL else AiohttpSession()
            temp_bot = Bot(token=settings.TG_BOT_TOKEN, session=session)
            try:
                await temp_bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
                return True
            finally:
                await temp_bot.session.close()
        except Exception:
            _WORKING_PROXY_URL = "__UNSET__"

    sorted_candidates = sorted(PROXY_CANDIDATES, key=lambda x: 0 if x[1] else 1)
    for _, proxy_url, _ in sorted_candidates:
        try:
            session = AiohttpSession(proxy=proxy_url, timeout=8.0) if proxy_url else AiohttpSession(timeout=5.0)
            temp_bot = Bot(token=settings.TG_BOT_TOKEN, session=session)
            try:
                await temp_bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
                _WORKING_PROXY_URL = proxy_url
                return True
            finally:
                await temp_bot.session.close()
        except Exception:
            continue
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
    under_300_rate = api_comm_rate_under_300 if 0.10 <= api_comm_rate_under_300 <= 0.38 else 0.23
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

    # Сценарий 2: Оптимальная цена свыше 300 ₽ (комиссия 52%), дающая чистую прибыль выше чем на 299 ₽ + запас маржи
    best_over300_price = 650.0
    best_over300_m = calculate_ozon_unit_profit(
        650.0, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
    )
    for cand_p in range(520, 760, 10):
        m_cand = calculate_ozon_unit_profit(
            float(cand_p), cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
        )
        # Ищем цену > 300 ₽, где чистая прибыль с банки составляет ~60-85 ₽ (в 1.5-2 раза выше чем на 299 ₽)
        if m_cand["net_profit"] >= max(m_299["net_profit"] + 25.0, 55.0):
            best_over300_price = float(cand_p)
            best_over300_m = m_cand
            break
    else:
        best_over300_price = 740.0
        best_over300_m = calculate_ozon_unit_profit(
            740.0, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
        )

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
    denom = max(denom, 0.18)

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
        db_prods = {p.sku: p for p in res.scalars().all()}

    # 1. Сбор фактических расходов Ozon из недельного отчета (включая реальную доставку 6686 / 172 = 38.87 ₽!)
    oz_actual_delivery_per_order = 38.87
    oz_overhead_per_order = 17.0
    oz_ad_rate = 0.112
    oz_dropoff_per_order = 11.0
    try:
        oz_fin = await ozon_api.fetch_real_finance_breakdown(days=7)
        oz_orders_cnt = max(int(oz_fin.get("orders_count") or 172), 1)
        oz_sales_rub = float(oz_fin.get("sales_total") or 0.0)
        oz_ads_rub = float(oz_fin.get("ads_total") or 0.0)
        if oz_sales_rub > 0 and oz_ads_rub > 0:
            oz_ad_rate = round(oz_ads_rub / oz_sales_rub, 4)

        # Фактическая стоимость доставки = графа «Услуги доставки» / число заказов
        deliv_total_rub = float(oz_fin.get("deliv_total") or 0.0)
        if deliv_total_rub > 0:
            oz_actual_delivery_per_order = round(deliv_total_rub / oz_orders_cnt, 2)

        dropoff_total = float(oz_fin.get("partner_dropoff") or 0.0) + float(oz_fin.get("deliv_ozon_handover") or 0.0)
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
    except Exception:
        pass

    # 2. АУДИТ ЦЕН OZON (с двумя кнопками: порог 299 ₽ и цена свыше 300 ₽ + кнопка Отклонить)
    try:
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
            p_db = db_prods.get(sku)
            cogs = (p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost) if p_db else 67.0

            dual = evaluate_ozon_dual_scenarios(
                current_price=cur_p,
                cogs=cogs,
                api_comm_fbo=float(item.get("commission_rate_fbo") or item.get("commission_rate") or 0.23),
                api_comm_fbs=float(item.get("commission_rate_fbs") or item.get("commission_rate") or 0.23),
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
                f"🔥 <b>Участие в акции:</b> Да ({item.get('promo_names') or 'Акция Ozon'})\n"
                f"⚠️ <i>Для установки {tover['price']:.0f} ₽ потребуется выход из акции; порог 299 ₽ доступен без потери конверсии.</i>\n"
                if in_promo
                else "🏷 <b>Участие в акции:</b> Нет (свободное управление ценой)\n"
            )

            # Создаем 2 pending_action: для кнопки 299 ₽ и для кнопки свыше 300 ₽
            act_299_id = await create_pending_action(
                marketplace="ozon",
                sku=sku,
                action_type="update_price",
                payload={
                    "product_id": item.get("external_id") or item.get("product_id"),
                    "offer_id": sku,
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
                    "new_price": tover["price"],
                },
            )

            msg_text = (
                f"🔵 <b>[Аудит цены Ozon] Артикул {sku}</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"⚖️ Схема: <i>{dual['wh_mode']}</i>\n"
                f"{promo_note}"
                f"💵 Текущая цена: <b>{cur_p:.0f} ₽</b> (прибыль сейчас: <b>{cur_m['net_profit']:+.0f} ₽/шт.</b>)\n\n"
                f"1️⃣ <b>Вариант 1: Максимальный порог 299 ₽ (Комиссия {t299['comm_pct']}%)</b>\n"
                f"• Вознаграждение Ozon ({t299['comm_pct']}%): <b>-{t299['comm_rub']:.0f} ₽</b>\n"
                f"• Логистика (факт. по отчету): <b>-{t299['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{t299['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({t299['ad_pct']}%): <b>-{t299['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{t299['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{t299['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{t299['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при 299 ₽: {t299['net_profit']:+.0f} ₽/шт.</b>\n\n"
                f"2️⃣ <b>Вариант 2: Свыше 300 ₽ — {tover['price']:.0f} ₽ (Комиссия 52%)</b>\n"
                f"• Вознаграждение Ozon (52%): <b>-{tover['comm_rub']:.0f} ₽</b>\n"
                f"• Логистика (факт.): <b>-{tover['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{tover['pvz_rub']:.0f} ₽</b>\n"
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
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен Ozon: <code>{exc}</code>")

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
            nm_id = int(item.get("nm_id") or item.get("external_id") or 0)
            sku = str(item.get("sku") or nm_id).strip()
            if not nm_id or nm_id in seen_wb:
                continue
            seen_wb.add(nm_id)

            fbo_st = int(item.get("stock_fbo") or 0)
            fbs_st = int(item.get("stock_fbs") or 0)
            if fbo_st == 0 and fbs_st == 0:
                continue

            cur_p = float(item.get("current_price") or 0.0)
            p_db = db_prods.get(sku) or db_prods.get(str(nm_id))
            cogs = (p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost) if p_db else 67.0

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
                f"🟣 <b>[Аудит цены WB] Артикул {sku} (nmID: {nm_id})</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"📈 Продажи: <b>{item.get('daily_sales', 0)} шт./день</b> ({calc['wh_mode']})\n"
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
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен WB: <code>{exc}</code>")