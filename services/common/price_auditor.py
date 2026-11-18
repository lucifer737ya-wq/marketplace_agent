from typing import Dict, Any, List, Tuple, Optional
from sqlalchemy import select
from database.models import AsyncSessionLocal, Product
from database.crud import create_pending_action
from bot.handlers.callbacks import build_approval_keyboard, build_dual_ozon_approval_keyboard
from products_catalog import PRODUCTS_CATALOG
from config import settings
from uuid import uuid4
import math
import html

async def send_tg_safe(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """Использует уже настроенный прокси и сессию основного бота."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception as exc:
        print(f"[Price audit] Ошибка Telegram: {exc}", flush=True)
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
    if not 0 < api_comm_rate_under_300 < 1:
        raise ValueError("Нет корректной комиссии Ozon из API")
    under_300_rate = api_comm_rate_under_300
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

    # Минимальная целая цена выше 300, обеспечивающая заданную прибыль.
    target_profit = max(m_299["net_profit"] + 25.0, 55.0)
    remaining = 1.0 - settings.OZON_HIGH_COMMISSION_RATE - settings.ACQUIRING_RATE - settings.TAX_RATE - ad_rate
    if remaining <= 0:
        raise ValueError("Доля удержаний >= 100%: прибыльную цену рассчитать нельзя")
    fixed = cogs + actual_delivery_per_order + weighted_pvz + overhead_per_order_rub
    price = max(301, math.ceil((fixed + target_profit + 0.02) / remaining))
    best_over300_m = calculate_ozon_unit_profit(float(price), cogs, actual_delivery_per_order,
                                               weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm)

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
    if denom <= 0:
        raise ValueError("Целевая рентабельность недостижима при текущих удержаниях WB")

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
        db_prods = {(p.marketplace, p.sku): p for p in res.scalars().all()}

    # 1. Сбор фактических расходов Ozon из недельного отчета (включая реальную доставку 6686 / 172 = 38.87 ₽!)
    oz_actual_delivery_per_order = None
    oz_overhead_per_order = 0.0
    oz_ad_rate = 0.0
    oz_dropoff_per_order = 0.0
    try:
        oz_fin = await ozon_api.fetch_real_finance_breakdown(days=7)
        oz_orders_cnt = int(oz_fin.get("orders_count") or 0)
        if oz_orders_cnt <= 0:
            raise ValueError("Нет числа заказов в отчёте Ozon")
        oz_sales_rub = float(oz_fin.get("sales_total") or 0.0)
        oz_ads_rub = float(oz_fin.get("ads_total") or 0.0)
        if oz_sales_rub > 0 and oz_ads_rub > 0:
            oz_ad_rate = round(oz_ads_rub / oz_sales_rub, 4)

        # Фактическая стоимость доставки = графа «Услуги доставки» / число заказов
        deliv_total_rub = float(oz_fin.get("deliv_total") or 0.0)
        if deliv_total_rub > 0:
            oz_actual_delivery_per_order = round(deliv_total_rub / oz_orders_cnt, 2)

        # Передача Ozon уже входит в deliv_total, не учитываем её повторно.
        dropoff_total = float(oz_fin.get("partner_dropoff") or 0.0)
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
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Финансы Ozon недоступны: {html.escape(str(exc))}")

    # 2. АУДИТ ЦЕН OZON (с двумя кнопками: порог 299 ₽ и цена свыше 300 ₽ + кнопка Отклонить)
    try:
        if oz_actual_delivery_per_order is None:
            raise ValueError("Нет фактической доставки на заказ; рекомендации отключены")
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
            p_db = db_prods.get(("ozon", sku))
            if not p_db or p_db.cost_price <= 0:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: не задана себестоимость, рекомендация пропущена")
                continue
            cogs = p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost

            dual = evaluate_ozon_dual_scenarios(
                current_price=cur_p,
                cogs=cogs,
                api_comm_fbo=float(item.get("commission_rate_fbo") or item.get("commission_rate") or 0.0),
                api_comm_fbs=float(item.get("commission_rate_fbs") or item.get("commission_rate") or 0.0),
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
                f"🔥 <b>Участие в акции:</b> Да ({html.escape(str(item.get('promo_names') or 'Акция Ozon'))})\n"
                f"⚠️ <i>Акция может ограничить цену. Проверьте её условия в кабинете; автоматического выхода нет.</i>\n"
                if in_promo
                else "🏷 <b>Участие в акции:</b> Нет (свободное управление ценой)\n"
            )

            group_id = uuid4().hex
            # Два варианта одной рекомендации блокируются совместно.
            act_299_id = await create_pending_action(
                marketplace="ozon",
                sku=sku,
                action_type="update_price",
                payload={
                    "product_id": item.get("external_id") or item.get("product_id"),
                    "offer_id": sku,
                    "group_id": group_id,
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
                    "group_id": group_id,
                    "new_price": tover["price"],
                },
            )

            msg_text = (
                f"🔵 <b>[Аудит цены Ozon] Артикул {html.escape(sku)}</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"⚖️ Схема: <i>{dual['wh_mode']}</i>\n"
                f"ℹ️ Средние расходы и доли FBO/FBS — оценка, не гарантия прибыли.\n"
                f"{promo_note}"
                f"💵 Текущая цена: <b>{cur_p:.0f} ₽</b> (прибыль сейчас: <b>{cur_m['net_profit']:+.0f} ₽/шт.</b>)\n\n"
                f"1️⃣ <b>Вариант 1: Максимальный порог 299 ₽ (Комиссия {t299['comm_pct']}%)</b>\n"
                f"• Вознаграждение Ozon ({t299['comm_pct']}%): <b>-{t299['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{t299['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{t299['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({t299['ad_pct']}%): <b>-{t299['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{t299['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{t299['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{t299['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при 299 ₽: {t299['net_profit']:+.0f} ₽/шт.</b>\n\n"
                f"2️⃣ <b>Вариант 2: Свыше 300 ₽ — {tover['price']:.0f} ₽ (Комиссия 52%)</b>\n"
                f"• Вознаграждение Ozon (52%): <b>-{tover['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{tover['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{tover['pvz_rub']:.0f} ₽</b>\n"
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
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен Ozon: <code>{html.escape(str(exc))}</code>")

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
            raw_nm = item.get("nm_id")
            nm_id = raw_nm if isinstance(raw_nm, int) and not isinstance(raw_nm, bool) else 0
            sku = str(item.get("sku") or nm_id).strip()
            if not nm_id or nm_id in seen_wb:
                continue
            seen_wb.add(nm_id)

            fbo_st = int(item.get("stock_fbo") or 0)
            fbs_st = int(item.get("stock_fbs") or 0)
            if fbo_st == 0 and fbs_st == 0:
                continue

            cur_p = float(item.get("current_price") or 0.0)
            p_db = db_prods.get(("wb", sku)) or db_prods.get(("wb", str(nm_id)))
            if not p_db or p_db.cost_price <= 0:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: не задана себестоимость, рекомендация пропущена")
                continue
            cogs = p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost

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
                f"🟣 <b>[Аудит цены WB] Артикул {html.escape(sku)} (nmID: {nm_id})</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"📈 Продажи: <b>{item.get('daily_sales', 0)} шт./день</b> ({calc['wh_mode']})\n"
                f"ℹ️ Тарифы и расходы могут быть оценочными; проверьте их в кабинете.\n"
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
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен WB: <code>{html.escape(str(exc))}</code>")