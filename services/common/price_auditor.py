from typing import Dict, Any, Optional
from sqlalchemy import select
from aiogram.exceptions import TelegramBadRequest
from database.models import AsyncSessionLocal, Product
from database.crud import create_pending_action, create_pending_actions
from services.common.orders_report import _mul_rub, _round_rub
from bot.handlers.callbacks import build_approval_keyboard, build_dual_ozon_approval_keyboard
from services.common.ad_recommendations import analyze_ozon_campaigns
from config import settings
from services.common.cogs import ozon_tax_base_ratio, roi_base_of, unit_cogs_of
from services.common.ozon_tariffs import ozon_logistics_cost
from services.common.pricing_policy import load_competitor_reference, recommend_price
from services.competitors.economics import spp_from_items
from uuid import uuid4
import html


async def send_tg_safe(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """Использует уже настроенный прокси и сессию основного бота."""
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        print(f"[Price audit] HTML-ошибка Telegram ({exc}), повторяю без разметки", flush=True)
        try:
            await bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
            return True
        except Exception as fallback_exc:  # noqa: BLE001
            print(f"[Price audit] Ошибка Telegram: {fallback_exc}", flush=True)
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"[Price audit] Ошибка Telegram: {exc}", flush=True)
        return False


def _ozon_competitor_lk(item: Dict[str, Any], ref: Dict[str, Any], disc: float) -> Optional[float]:
    """Цена конкурентов Ozon в пересчёте на цену ЛК: витрина конкурента / (1 − скидка Ozon)."""
    showcase = ref.get("ozon_median_buyer") or item.get("competitor_min_ozon") or 0.0
    try:
        showcase = float(showcase)
    except (TypeError, ValueError):
        return None
    if showcase <= 0:
        return None
    return showcase / (1.0 - disc) if disc < 1 else None


def calculate_ozon_unit_profit(
    price: float,
    cogs: float,
    logistics_rub: float,
    pvz_dropoff_rub: float,
    overhead_per_order_rub: float,
    ad_rate: float,
    api_comm_rate_under_300: float = 0.23,
    acquiring_rate: Optional[float] = None,
    usn_rate: Optional[float] = None,
    roi_base: Optional[float] = None,
) -> Dict[str, float]:
    """
    Расчет чистой прибыли на 1 заказ Ozon:
    - До 300 ₽ включительно: реальный % вознаграждения из API (22-23%).
    - Свыше 300 ₽: вознаграждение Ozon 52%.
    - Логистика: фактическая средняя из «Услуги доставки / Заказы».
    """
    if not 0 < api_comm_rate_under_300 < 1:
        raise ValueError("Нет корректной комиссии Ozon из API")
    acq_rate = settings.ACQUIRING_RATE if acquiring_rate is None else acquiring_rate
    tax_rate = settings.TAX_RATE if usn_rate is None else usn_rate
    # At an existing high-tier price the API returns the high commission. Use the
    # conservative edge of the specified 22–23% low tariff for the 299 scenario.
    under_300_rate = api_comm_rate_under_300 if 0.22 <= api_comm_rate_under_300 <= 0.23 else 0.23
    comm_rate = under_300_rate if price <= settings.OZON_LOW_PRICE_LIMIT else settings.OZON_HIGH_COMMISSION_RATE

    # Логистика Ozon зависит от объёма и цены ЛК (до 300 ₽ — 25 ₽, выше — 97 ₽ для банки 0.18 л).
    # Средняя «из отчёта» смешивает оба тарифа и занижала расходы на ценах выше 300 ₽.
    if settings.OZON_LOGISTICS_BY_FORMULA:
        formula_log = ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, price, settings.OZON_LOW_PRICE_LIMIT)
        if formula_log > 0:
            logistics_rub = formula_log

    comm_rub = _mul_rub(price, comm_rate)
    acq_rub = _mul_rub(price, acq_rate)
    ad_rub = _mul_rub(price, ad_rate)
    # УСН 6% — со стоимости, по которой берёт покупатель (витрина), а не с цены ЛК.
    tax_rub = _mul_rub(price * ozon_tax_base_ratio(), tax_rate)

    total_deductions = comm_rub + acq_rub + logistics_rub + pvz_dropoff_rub + overhead_per_order_rub + ad_rub + tax_rub + cogs
    net_profit = _round_rub(price - total_deductions)

    return {
        "price": price,
        "net_profit": net_profit,
        "comm_rub": comm_rub,
        "comm_pct": round(comm_rate * 100, 1),
        "acq_rub": acq_rub,
        "ad_rub": ad_rub,
        "ad_pct": round(ad_rate * 100, 1),
        "tax_rub": tax_rub,
        "roi_pct": round(net_profit / (roi_base or cogs) * 100, 1) if (roi_base or cogs) > 0 else 0.0,
        "margin_pct": round(net_profit / price * 100, 1) if price > 0 else 0.0,
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
    competitor_lk: Optional[float] = None,
    roi_base: Optional[float] = None,
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
        current_price, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm,
        roi_base=roi_base,
    )

    # Сценарий 1: Максимальный порог до 300 ₽ -> 299 ₽
    m_299 = calculate_ozon_unit_profit(
        299.0, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm,
        roi_base=roi_base,
    )

    # Цена выше 300 ₽: коридор чистой прибыли 30–100% к себестоимости + оглядка на цену конкурентов.
    def _pf(p: float) -> float:
        return calculate_ozon_unit_profit(
            p, cogs, actual_delivery_per_order, weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm
        )["net_profit"]

    limit_int = int(settings.OZON_LOW_PRICE_LIMIT)
    rec = recommend_price(_pf, cogs, lo=limit_int + 1, hi=6000, competitor_lk=competitor_lk, roi_base=roi_base)
    if rec is None:
        raise ValueError("Прибыльная цена выше 300 ₽ недостижима при текущих удержаниях (комиссия 52% + реклама)")
    best_over300_m = calculate_ozon_unit_profit(
        float(rec.price), cogs, actual_delivery_per_order,
        weighted_pvz, overhead_per_order_rub, ad_rate, weighted_comm,
        roi_base=roi_base,
    )

    return {
        "wh_mode": wh_mode,
        "current": cur_m,
        "tier_299": m_299,
        "tier_over300": best_over300_m,
        "policy": rec,
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
    spp_rate: float = 0.0,
    competitor_buyer_price: Optional[float] = None,
    roi_base: Optional[float] = None,
) -> Dict[str, Any]:
    """Рекомендованная цена WB: ROI 30–100%, налог 6% с цены покупателя (после СПП), не дороже конкурентов."""
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

    acq_rate = settings.ACQUIRING_RATE
    tax_rate = settings.TAX_RATE
    spp = min(max(spp_rate, 0.0), 0.6)
    buyer_ratio = 1.0 - spp
    denom = 1.0 - comm_rate - acq_rate - tax_rate * buyer_ratio - ad_rate
    if denom <= 0:
        raise ValueError("Целевая рентабельность недостижима при текущих удержаниях WB")

    def profit_at(price: float) -> float:
        return price * denom - weighted_log - overhead_per_order_rub - cogs

    # Конкуренты на витрине показывают цену покупателя (с СПП); переводим в цену ЛК.
    comp_lk = competitor_buyer_price / buyer_ratio if competitor_buyer_price and buyer_ratio > 0 else None
    rec = recommend_price(profit_at, cogs, lo=max(int(cogs), 1), hi=6000, competitor_lk=comp_lk, roi_base=roi_base)
    if rec is None:
        raise ValueError("Целевая рентабельность недостижима при текущих удержаниях WB")
    opt_price = float(rec.price)
    acq_rub = _mul_rub(opt_price, acq_rate)
    comm_rub = _mul_rub(opt_price, comm_rate)
    ad_rub = _mul_rub(opt_price, ad_rate)
    tax_rub = _mul_rub(opt_price * buyer_ratio, tax_rate)
    net_profit = _round_rub(
        opt_price - comm_rub - acq_rub - weighted_log - overhead_per_order_rub - ad_rub - tax_rub - cogs
    )
    cur_net = _round_rub(profit_at(current_price)) if current_price > 0 else 0.0

    return {
        "recommended_price": opt_price,
        "current_net_profit": cur_net,
        "net_profit": net_profit,
        "roi_pct": round(net_profit / (roi_base or cogs) * 100, 1) if (roi_base or cogs) > 0 else 0.0,
        "margin_pct": round(net_profit / opt_price * 100, 1) if opt_price else 0.0,
        "verdict": rec.verdict,
        "status": rec.status,
        "buyer_price": round(opt_price * buyer_ratio),
        "spp_pct": round(spp * 100, 1),
        "competitor_lk": comp_lk,
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

    # 1. Сбор фактических расходов Ozon из недельного отчета
    oz_fin: Dict[str, Any] = {}
    oz_actual_delivery_per_order = None
    oz_overhead_per_order = 0.0
    try:
        oz_fin = await ozon_api.fetch_real_finance_breakdown(days=7)
        oz_orders_cnt = int(oz_fin.get("orders_count") or 0)
        if oz_orders_cnt <= 0:
            raise ValueError("Нет числа заказов в отчёте Ozon")
        # Фактическая стоимость доставки = графа «Услуги доставки» / число заказов
        deliv_total_rub = float(oz_fin.get("deliv_total") or 0.0)
        if deliv_total_rub > 0:
            oz_actual_delivery_per_order = round(deliv_total_rub / oz_orders_cnt, 2)

        other_hold_total = (
            float(oz_fin.get("partner_pack") or 0.0)
            + float(oz_fin.get("partner_returns") or 0.0)
            + float(oz_fin.get("star_products") or 0.0)
            + float(oz_fin.get("partner_last_mile") or 0.0)
            + float(oz_fin.get("fbo_storage") or 0.0)
            + float(oz_fin.get("other_fines") or 0.0)
            + float(oz_fin.get("decompensations") or 0.0)
            + float(oz_fin.get("mutual_settlements") or 0.0)
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
        # Use campaign-attributed revenue, not store TACoS. Billing alone cannot assign a campaign.
        try:
            campaigns = await ozon_api.fetch_active_ad_campaigns(days=7)
        except RuntimeError:
            if oz_fin.get("ads_total") != 0 or oz_fin.get("billed_skus"):
                raise
            # No expenses to allocate in this billing period; not a claim of organic traffic.
            campaigns = []
            await send_tg_safe(bot, admin_id, "Ozon: состав РК недоступен. В расчёте за период рекламных списаний нет.")
        ads = analyze_ozon_campaigns(campaigns, ozon_items, oz_fin, list(db_prods.values()))
        # Ставка рекламы магазина (ДРР) — запасной вариант, когда по SKU нет атрибуции кампаний:
        # кампании «оплата за заказ» не отдают ДРР по API, из-за этого раньше SKU пропускались без кнопок.
        oz_sales_total = abs(float(oz_fin.get("sales_total") or 0.0))
        store_ad_rate = min(abs(float(oz_fin.get("ads_total") or 0.0)) / oz_sales_total, 0.5) if oz_sales_total > 0 else 0.0
        if any(c["products_missing"] for c in ads["campaigns"]):
            await send_tg_safe(
                bot, admin_id,
                "ℹ️ Ozon: состав части кампаний недоступен — для SKU без атрибуции взят средний ДРР магазина "
                f"{store_ad_rate * 100:.1f}%."
            )
        rates = {card["sku"]: 0.0 for card in ads["organic_items"]}
        for campaign in ads["campaigns"]:
            if campaign["drr_in_promo_pct"] is not None:
                rates.update({sku: campaign["drr_in_promo_pct"] / 100 for sku in campaign["skus"]})
        ref = load_competitor_reference()
        oz_disc = min(max(float(settings.COMPETITOR_OZON_DISCOUNT_RATE), 0.0), 0.6)
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
            cogs = unit_cogs_of(p_db)

            product_id = str(item.get("product_id") or "")
            fee = item.get("fbs_pvz_dropoff_rub")
            data_notes = []
            if not product_id.isdecimal() or int(product_id) <= 0:
                await send_tg_safe(
                    bot, admin_id,
                    f"⚠️ {html.escape(sku)}: Ozon не вернул числовой product_id — цену через API не поменять, кнопки не созданы"
                )
                continue
            if sku not in rates or sku in ads["overlapping_skus"]:
                rates[sku] = store_ad_rate
                data_notes.append(f"ДРР: средний по магазину {store_ad_rate * 100:.1f}% (по SKU нет атрибуции кампаний)")
            if fbs_st and fee is None:
                fee = 0.0
                data_notes.append("ПВЗ/drop-off FBS: Ozon не отдал ставку — принято 0 ₽, проверьте в ЛК")
            try:
                dual = evaluate_ozon_dual_scenarios(
                    current_price=cur_p,
                    cogs=cogs,
                    api_comm_fbo=float(item.get("commission_rate_fbo") or item.get("commission_rate") or 0.0),
                    api_comm_fbs=float(item.get("commission_rate_fbs") or item.get("commission_rate") or 0.0),
                    fbo_stock=fbo_st,
                    fbs_stock=fbs_st,
                    actual_delivery_per_order=oz_actual_delivery_per_order,
                    fbs_pvz_fee=float(fee or 0),
                    overhead_per_order_rub=oz_overhead_per_order,
                    ad_rate=rates[sku],
                    daily_sales=float(item.get("daily_sales") or 0.0),
                    competitor_lk=_ozon_competitor_lk(item, ref, oz_disc),
                    roi_base=roi_base_of(p_db),
                )
            except ValueError as exc:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: {html.escape(str(exc))}")
                continue
            policy = dual["policy"]
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

            common = {"product_id": int(product_id), "offer_id": sku, "group_id": uuid4().hex}
            threshold, optimal = await create_pending_actions(
                "ozon",
                sku,
                "update_price",
                [dict(common, new_price=299.0), dict(common, new_price=tover["price"])],
            )

            msg_text = (
                f"🔵 <b>[Аудит цены Ozon] Артикул {html.escape(sku)}</b>\n"
                f"📦 Остатки: <b>FBO: {fbo_st} шт. | FBS: {fbs_st} шт.</b>\n"
                f"⚖️ Схема: <i>{dual['wh_mode']}</i>\n"
                f"ℹ️ Средние расходы — оценка. Для 299 ₽ при недоступном льготном тарифе использована верхняя ставка 23%.\n"
                f"{promo_note}"
                f"💵 Текущая цена: <b>{cur_p:.0f} ₽</b> (прибыль сейчас: <b>{cur_m['net_profit']:+.0f} ₽/шт.</b>)\n\n"
                f"1️⃣ <b>Вариант 1: Максимальный порог 299 ₽ (Комиссия {t299['comm_pct']}%)</b>\n"
                f"• Вознаграждение Ozon ({t299['comm_pct']}%): <b>-{t299['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{t299['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{t299['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({t299['ad_pct']}%): <b>-{t299['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{t299['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{t299['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{t299['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при 299 ₽: {t299['net_profit']:+.0f} ₽/шт.</b> "
                f"(ROI к закупке {t299['roi_pct']:.0f}%, маржа {t299['margin_pct']:.0f}%)\n\n"
                f"2️⃣ <b>Вариант 2: Свыше 300 ₽ — {tover['price']:.0f} ₽ (Комиссия 52%)</b>\n"
                f"• Вознаграждение Ozon (52%): <b>-{tover['comm_rub']:.0f} ₽</b>\n"
                f"• Доставка (из отчёта): <b>-{tover['logistics_rub']:.0f} ₽</b> | ПВЗ (FBS): <b>-{tover['pvz_rub']:.0f} ₽</b>\n"
                f"• Реклама ({tover['ad_pct']}%): <b>-{tover['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{tover['overhead_rub']:.0f} ₽</b>\n"
                f"• Эквайринг: <b>-{tover['acq_rub']:.1f} ₽</b> | Налог УСН (6%): <b>-{tover['tax_rub']:.0f} ₽</b> | Себест.: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль при {tover['price']:.0f} ₽: {tover['net_profit']:+.0f} ₽/шт.</b> "
                f"(ROI {tover['roi_pct']:.0f}%, маржа {tover['margin_pct']:.0f}%)\n"
                f"🎯 {html.escape(policy.verdict)}\n"
                + (f"ℹ️ Неполные данные заменены оценкой: {html.escape('; '.join(data_notes))}\n" if data_notes else "")
                + f"🧾 Налог 6% считан со стоимости для покупателя (≈{ozon_tax_base_ratio() * 100:.0f}% цены ЛК)."
            )
            await send_tg_safe(
                bot,
                admin_id,
                msg_text,
                reply_markup=build_dual_ozon_approval_keyboard(
                    act_299_id=threshold.id,
                    profit_299=t299["net_profit"],
                    act_over300_id=optimal.id,
                    price_over300=tover["price"],
                    profit_over300=tover["net_profit"],
                ),
            )
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен Ozon: <code>{html.escape(str(exc))}</code>")

    # 3. Сбор накладных расходов WB на 1 заказ
    wb_ad_rate = 0.0
    try:
        # Окно 30 дней, а не 7: при 1 продаже за неделю хранение/приёмка делились на 1 заказ и давали
        # «прочие удержания 57 ₽» на каждый артикул (реально ≈ 22 ₽ по месяцу), цены завышались вдвое.
        wb_fin = await wb_api.fetch_real_finance_breakdown(days=int(settings.PRICE_CALC_PERIOD_DAYS))
        wb_cnt = int(wb_fin.get("orders_count") or 0)
        if wb_cnt <= 0:
            raise ValueError("Нет базы расходов на заказ WB")
        wb_rev = float(wb_fin.get("revenue_seller") or 0.0)
        wb_ads = float(wb_fin.get("ads") or 0.0)
        if wb_ads > 0 and wb_rev <= 0:
            raise ValueError("Нет выручки для распределения рекламных расходов WB")
        if wb_rev > 0 and wb_ads > 0:
            wb_ad_rate = round(wb_ads / wb_rev, 4)
        wb_other_total = (
            float(wb_fin.get("storage") or 0.0)
            + float(wb_fin.get("acceptance") or 0.0)
            + float(wb_fin.get("other_services") or 0.0)
            + float(wb_fin.get("logistics_return") or 0.0)
        )
        wb_overhead_per_order = round(wb_other_total / wb_cnt, 2)
        wb_fact_log = None
        if wb_cnt >= 10 and float(wb_fin.get("logistics_direct") or 0.0) > 0:
            wb_fact_log = round(float(wb_fin["logistics_direct"]) / wb_cnt, 2)
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Финансы WB недоступны, кнопки не созданы: {html.escape(str(exc))}")
        return

    # 4. АУДИТ ЦЕН WILDBERRIES
    try:
        wb_items = await wb_api.fetch_stocks_and_prices()
        wb_ref = load_competitor_reference()
        wb_spp = spp_from_items(wb_items, float(settings.COMPETITOR_WB_SPP_RATE))
        wb_comp_buyer = wb_ref.get("wb_median_buyer")
        comm_pool = [float(i["commission_rate"]) for i in wb_items if i.get("commission_rate")]
        fbo_pool = [float(i["fbo_logistics_cost"]) for i in wb_items if i.get("fbo_logistics_cost")]
        fbs_pool = [float(i["fbs_logistics_cost"]) for i in wb_items if i.get("fbs_logistics_cost")]
        comm_fallback = sorted(comm_pool)[len(comm_pool) // 2] if comm_pool else 0.42
        fbo_fallback = sorted(fbo_pool)[len(fbo_pool) // 2] if fbo_pool else 78.0
        fbs_fallback = sorted(fbs_pool)[len(fbs_pool) // 2] if fbs_pool else fbo_fallback
        seen_wb = set()
        for item in wb_items:
            raw_nm = item.get("nm_id")
            nm_id = raw_nm if isinstance(raw_nm, int) and not isinstance(raw_nm, bool) else 0
            sku = str(item.get("sku") or nm_id).strip()
            if nm_id <= 0 or nm_id in seen_wb:
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
            cogs = unit_cogs_of(p_db)

            # Неполные данные WB (нет тарифа предмета/логистики/продаж) больше не отменяют рекомендацию:
            # берутся медианы по остальным SKU магазина, а в карточке прямо написано, что это оценка.
            wb_notes = []
            comm_val = item.get("commission_rate")
            if comm_val is None:
                comm_val = comm_fallback
                wb_notes.append(f"комиссия предмета не пришла из тарифов WB — медиана магазина {comm_fallback * 100:.1f}%")
            fbo_log = item.get("fbo_logistics_cost")
            fbs_log = item.get("fbs_logistics_cost")
            if fbo_log is None:
                fbo_log = fbo_fallback
                if fbo_st:
                    wb_notes.append(f"логистика FBO н/д — медиана {fbo_fallback:.0f} ₽")
            if fbs_log is None:
                fbs_log = fbs_fallback
                if fbs_st:
                    wb_notes.append(f"логистика FBS н/д — медиана {fbs_fallback:.0f} ₽")
            if item.get("daily_sales") is None:
                wb_notes.append("продажи за 7 дн. н/д")
            log_src = "базовый тариф коробки (верхняя оценка)"
            if wb_fact_log:
                # Фактическая логистика из отчёта реализации вместо базового тарифа склада (≈78 ₽ против ≈40 ₽).
                fbo_log, fbs_log = min(float(fbo_log or wb_fact_log), wb_fact_log), min(float(fbs_log or wb_fact_log), wb_fact_log)
                log_src = f"факт из отчёта WB за {int(settings.PRICE_CALC_PERIOD_DAYS)} дн. ({wb_fact_log:.0f} ₽/заказ)"
            try:
                calc = find_optimal_wb_price(
                    current_price=cur_p,
                    cogs=cogs,
                    comm_rate=float(comm_val),
                    fbo_stock=fbo_st,
                    fbs_stock=fbs_st,
                    fbo_logistics=float(fbo_log or 0.0),
                    fbs_logistics=float(fbs_log or 0.0),
                    overhead_per_order_rub=wb_overhead_per_order,
                    ad_rate=wb_ad_rate,
                    daily_sales=float(item.get("daily_sales") or 0.0),
                    spp_rate=wb_spp,
                    competitor_buyer_price=float(wb_comp_buyer) if wb_comp_buyer else None,
                    roi_base=roi_base_of(p_db),
                )
            except ValueError as exc:
                await send_tg_safe(bot, admin_id, f"⚠️ {html.escape(sku)}: {html.escape(str(exc))}")
                continue
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
                f"ℹ️ Логистика — {log_src}; проверьте объём и склад.\n"
                f"💰 Текущая цена: <b>{cur_p:.0f} ₽</b> ➡️ Рекомендуемая: <b>{rec_p:.0f} ₽</b>\n"
                f"📊 Комиссия WB ({calc['commission_pct']}%): <b>-{calc['commission_rub']:.0f} ₽</b>\n"
                f"🚚 Логистика: <b>-{calc['logistics_rub']:.0f} ₽</b> | Эквайринг ({settings.ACQUIRING_RATE * 100:.1f}%): <b>-{calc['acquiring_rub']:.2f} ₽</b>\n"
                f"📣 Реклама ({calc['ad_pct']}%): <b>-{calc['ad_rub']:.0f} ₽</b> | Прочие удержания: <b>-{calc['overhead_rub']:.0f} ₽</b>\n"
                f"🧾 Налог УСН ({settings.TAX_RATE * 100:.0f}%): <b>-{calc['tax_rub']:.0f} ₽</b> | Себестоимость: <b>-{cogs:.0f} ₽</b>\n"
                f"👉 <b>Чистая прибыль: {calc['net_profit']:+.0f} ₽/шт.</b> (сейчас: {calc['current_net_profit']:+.0f} ₽/шт.) "
                f"· ROI {calc['roi_pct']:.0f}% · маржа {calc['margin_pct']:.0f}%\n"
                f"👤 Покупатель увидит ≈ {calc['buyer_price']:.0f} ₽ (СПП {calc['spp_pct']:.0f}%); налог 6% считан с этой цены.\n"
                f"🎯 {html.escape(calc['verdict'])}"
                + (f"\nℹ️ Неполные данные заменены оценкой: {html.escape('; '.join(wb_notes))}" if wb_notes else "")
            )
            await send_tg_safe(
                bot,
                admin_id,
                msg_text,
                reply_markup=build_approval_keyboard(act_id.id, f"✅ Установить {rec_p:.0f} ₽ на WB"),
            )
    except Exception as exc:
        await send_tg_safe(bot, admin_id, f"⚠️ Ошибка аудита цен WB: <code>{html.escape(str(exc))}</code>")