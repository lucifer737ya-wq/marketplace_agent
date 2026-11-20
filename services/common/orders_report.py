import asyncio
import html
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import settings
from database.crud import _db_write_lock
from database.models import OrderRecord, Product, get_db_session
from products_catalog import OZON_SKU_TO_ART, WB_SKU_TO_ART
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

CANCELLED_ORDER_STATUSES: Set[str] = {
    "cancelled",
    "canceled",
    "returned",
    "return",
    "отменен",
    "отменён",
    "возврат",
}

_SQLITE_IN_CHUNK_SIZE = 500
_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")


def _to_dec(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return default
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not cleaned:
            return default
        try:
            parsed = Decimal(cleaned)
            return parsed if parsed.is_finite() else default
        except InvalidOperation:
            return default
    return default


def _round_rub(value: Any) -> float:
    return float(_to_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def _mul_rub(amount: Any, rate: Any) -> float:
    return float((_to_dec(amount) * _to_dec(rate)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _div_rub(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    den = _to_dec(denominator)
    if den <= Decimal("0"):
        return default
    return float((_to_dec(numerator) / den).quantize(_CENT, rounding=ROUND_HALF_UP))


def _round_pct(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    den = _to_dec(denominator)
    if den <= Decimal("0"):
        return default
    return float(((_to_dec(numerator) / den) * Decimal("100")).quantize(_TENTH, rounding=ROUND_HALF_UP))


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not cleaned:
            return default
        value = cleaned
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_nonneg(value: Any, default: float = 0.0) -> float:
    return max(_safe_float(value, default), 0.0)


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return int(parsed)


def _parse_order_datetime(value: Any) -> Optional[datetime]:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None  # do not backdate historical sales to the report run
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _resolve_order_sku(raw_sku: Any, marketplace: str) -> str:
    clean = str(raw_sku or "").strip()
    if not clean:
        return ""
    mp = str(marketplace or "").strip().lower()
    if mp == "ozon":
        return OZON_SKU_TO_ART.get(clean, clean)
    if mp == "wb":
        return WB_SKU_TO_ART.get(clean, clean)
    return clean


def _pct(value: Any, base: Any) -> str:
    val = _safe_float(value)
    denom = _safe_float(base)
    if denom <= 0.0:
        return "0.0%"
    return f"{_round_pct(val, denom):.1f}%"


def _fmt_money(value: Any) -> str:
    val = _safe_float(value)
    if abs(val) < 0.005:
        return "0"
    if abs(val - round(val)) >= 0.005:
        return f"{val:,.2f}"
    return f"{val:,.0f}"


def _fmt_expense(value: float) -> str:
    amt = _safe_float(value)
    if amt < -0.005:
        return f"+{_fmt_money(-amt)} ₽"
    if abs(amt) < 0.005:
        return "0 ₽"
    return f"-{_fmt_money(amt)} ₽"


def _index_products_by_marketplace(
    all_prods: List[Product],
) -> Tuple[Dict[Tuple[str, str], Product], Dict[Tuple[str, str], Product]]:
    by_mp_sku: Dict[Tuple[str, str], Product] = {}
    by_mp_ext: Dict[Tuple[str, str], Product] = {}
    for prod in all_prods:
        mp = str(prod.marketplace or "").strip().lower()
        sku = str(prod.sku or "").strip()
        ext = str(prod.external_id or "").strip()
        if mp and sku:
            by_mp_sku[(mp, sku)] = prod
        if mp and ext and (mp, ext) not in by_mp_ext:
            by_mp_ext[(mp, ext)] = prod
    return by_mp_sku, by_mp_ext


def _lookup_product_for_order(
    marketplace: str,
    raw_sku: str,
    by_mp_sku: Dict[Tuple[str, str], Product],
    by_mp_ext: Dict[Tuple[str, str], Product],
) -> Optional[Product]:
    mp = str(marketplace or "").strip().lower()
    clean_raw = str(raw_sku or "").strip()
    resolved = _resolve_order_sku(clean_raw, mp)
    return (
        by_mp_sku.get((mp, resolved))
        or by_mp_sku.get((mp, clean_raw))
        or by_mp_ext.get((mp, clean_raw))
        or by_mp_ext.get((mp, resolved))
    )


def _calculate_marketplace_cogs(
    orders_list: Any,
    marketplace: str,
    by_mp_sku: Dict[Tuple[str, str], Product],
    by_mp_ext: Dict[Tuple[str, str], Product],
) -> Tuple[float, int, List[str]]:
    """
    Рассчитывает подтвержденную себестоимость (сырье + упаковка + первая миля) строго в разрезе
    конкретного маркетплейса, без подстановки вымышленных значений и без учета отмененных заказов.
    Возвращает: (total_cogs_rub, missing_cogs_units, sorted_missing_skus).
    """
    if not isinstance(orders_list, list):
        return 0.0, 0, []

    mp = str(marketplace or "").strip().lower()
    total_cogs = Decimal("0")
    missing_units = 0
    missing_skus: Set[str] = set()
    seen_order_ids: Set[str] = set()

    for order in orders_list:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("order_id") or "").strip()
        if order_id:
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)

        status = str(order.get("status") or "ordered").strip().lower()
        if status in CANCELLED_ORDER_STATUSES:
            continue

        qty = max(_safe_int(order.get("quantity"), 1), 1)
        raw_sku = str(order.get("sku") or "").strip()
        art = _resolve_order_sku(raw_sku, mp)
        prod = _lookup_product_for_order(mp, raw_sku, by_mp_sku, by_mp_ext)

        if prod is not None and _safe_nonneg(prod.cost_price) > 0.0:
            unit_cogs = (
                _to_dec(_safe_nonneg(prod.cost_price))
                + _to_dec(_safe_nonneg(prod.packaging_cost))
                + _to_dec(_safe_nonneg(prod.first_mile_unit_cost))
            )
            total_cogs += unit_cogs * Decimal(qty)
        else:
            missing_units += qty
            if art or raw_sku:
                missing_skus.add(art or raw_sku)

    return _round_rub(total_cogs), missing_units, sorted(missing_skus)


def _count_active_orders(orders_list: Any) -> int:
    if not isinstance(orders_list, list):
        return 0
    count = 0
    seen_order_ids: Set[str] = set()
    for order in orders_list:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("order_id") or "").strip()
        if order_id:
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
        status = str(order.get("status") or "ordered").strip().lower()
        if status in CANCELLED_ORDER_STATUSES:
            continue
        count += max(_safe_int(order.get("quantity"), 1), 1)
    return count


def _prepare_clean_orders(ozon_fin: Any, wb_fin: Any) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    sources: List[Tuple[str, Any]] = [("ozon", ozon_fin), ("wb", wb_fin)]

    for default_mp, payload in sources:
        if not isinstance(payload, dict):
            continue
        orders = payload.get("orders")
        if not isinstance(orders, list):
            continue
        for ord_data in orders:
            if not isinstance(ord_data, dict):
                continue
            order_id = str(ord_data.get("order_id") or "").strip()
            if not order_id or order_id in deduped or ord_data.get("price") is None:
                continue
            mp = str(ord_data.get("marketplace") or default_mp).strip().lower()
            if mp not in {"ozon", "wb"}:
                mp = default_mp
            raw_sku = str(ord_data.get("sku") or "").strip()
            created = _parse_order_datetime(ord_data.get("created_at"))
            if created is None:
                continue
            deduped[order_id] = {
                "order_id": order_id[:128],
                "marketplace": mp,
                "sku": _resolve_order_sku(raw_sku, mp)[:64],
                "created_at": created,
                "price": round(_safe_nonneg(ord_data.get("price")), 2),
                "commission": round(_safe_float(ord_data.get("commission")), 2),
                "logistics": round(_safe_float(ord_data.get("logistics")), 2),
                "status": str(ord_data.get("status") or "ordered").strip()[:64],
            }
    return list(deduped.values())


async def _load_products_and_persist_orders(
    clean_orders: List[Dict[str, Any]],
) -> List[Product]:
    async with _db_write_lock:
        async with get_db_session() as session:
            prods_res = await session.execute(select(Product))
            all_prods = list(prods_res.scalars().all())

            if clean_orders:
                all_ids = [item["order_id"] for item in clean_orders]
                existing_ids: Set[str] = set()
                for offset in range(0, len(all_ids), _SQLITE_IN_CHUNK_SIZE):
                    chunk = all_ids[offset : offset + _SQLITE_IN_CHUNK_SIZE]
                    res = await session.execute(
                        select(OrderRecord.order_id).where(OrderRecord.order_id.in_(chunk))
                    )
                    existing_ids.update(row[0] for row in res.fetchall())

                pending_rows = [item for item in clean_orders if item["order_id"] not in existing_ids]
                if pending_rows:
                    for item in pending_rows:
                        session.add(OrderRecord(**item))
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                        for item in pending_rows:
                            try:
                                async with session.begin_nested():
                                    session.add(OrderRecord(**item))
                            except IntegrityError:
                                continue
                        await session.commit()

            return all_prods


def _format_missing_cogs_note(missing_units: int, missing_skus: List[str]) -> str:
    if missing_units <= 0:
        return ""
    sample = ", ".join(html.escape(s) for s in missing_skus[:8])
    if len(missing_skus) > 8:
        sample += f" и ещё {len(missing_skus) - 8}"
    sku_part = f" (арт.: <code>{sample}</code>)" if sample else ""
    return (
        f"⚠️ <i>Не задана себестоимость для <b>{missing_units} шт.</b>{sku_part} — "
        f"чистая прибыль не рассчитана.</i>\n"
    )


async def sync_and_generate_orders_report(
    ozon_service: OzonSellerAPI,
    wb_service: WBStatisticsAPI,
    days: int = 7,
    custom_date_from: Optional[str] = None,
    custom_date_to: Optional[str] = None,
    report_title: str = "📊 Финансовый отчет (по доступным данным Ozon и WB)",
) -> str:
    safe_days = max(_safe_int(days, 7), 1)
    tax_rate = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
    acq_rate = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
    tax_pct_label = f"{tax_rate * 100.0:.0f}%" if abs(tax_rate * 100.0 - round(tax_rate * 100.0)) < 0.05 else f"{tax_rate * 100.0:.1f}%"
    acq_pct_label = f"{acq_rate * 100.0:.1f}%"

    ozon_fin, wb_fin = await asyncio.gather(
        ozon_service.fetch_real_finance_breakdown(
            days=safe_days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
        wb_service.fetch_real_finance_breakdown(
            days=safe_days, custom_date_from=custom_date_from, custom_date_to=custom_date_to
        ),
        return_exceptions=True,
    )

    clean_orders = _prepare_clean_orders(ozon_fin, wb_fin)
    all_prods = await _load_products_and_persist_orders(clean_orders)
    by_mp_sku, by_mp_ext = _index_products_by_marketplace(all_prods)

    api_errors_count = 0
    oz_profit_known = False
    wb_profit_known = False

    # --- БЛОК OZON ---
    if not isinstance(ozon_fin, dict):
        api_errors_count += 1
        err_text = html.escape(str(ozon_fin))
        ozon_block = f"🔵 <b>OZON:</b>\n❌ Ошибка API Ozon:\n<code>{err_text}</code>\n\n"
        oz_sales = oz_net_profit = oz_cogs = oz_ded_tot = oz_tax = oz_acq = 0.0
        oz_count = oz_missing_cogs = 0
    else:
        oz_orders = ozon_fin.get("orders") if isinstance(ozon_fin.get("orders"), list) else []
        raw_oz_count = ozon_fin.get("orders_count")
        oz_count = (
            max(_safe_int(raw_oz_count, 0), 0)
            if raw_oz_count is not None
            else _count_active_orders(oz_orders)
        )

        oz_sales = round(_safe_float(ozon_fin.get("sales_total")), 2)
        oz_comm = round(_safe_float(ozon_fin.get("commission")), 2)

        oz_d_dir = round(_safe_float(ozon_fin.get("deliv_direct")), 2)
        oz_d_ret = round(_safe_float(ozon_fin.get("deliv_return")), 2)
        oz_d_ho = round(_safe_float(ozon_fin.get("deliv_ozon_handover")), 2)
        oz_deliv = (
            round(_safe_float(ozon_fin.get("deliv_total")), 2)
            if ozon_fin.get("deliv_total") is not None
            else round(oz_d_dir + oz_d_ret + oz_d_ho, 2)
        )
        oz_deliv_per_order: Optional[float] = (
            _div_rub(oz_deliv, oz_count) if oz_count > 0 else None
        )

        oz_dropoff = _round_rub(_safe_float(ozon_fin.get("partner_dropoff")))
        oz_pack = _round_rub(_safe_float(ozon_fin.get("partner_pack")))
        oz_pret = _round_rub(_safe_float(ozon_fin.get("partner_returns")))
        raw_oz_acq = _round_rub(_safe_float(ozon_fin.get("acquiring")))
        oz_star = _round_rub(_safe_float(ozon_fin.get("star_products")))
        oz_plm = _round_rub(_safe_float(ozon_fin.get("partner_last_mile")))

        # Если эквайринг еще не отражен в cash-flow Ozon при положительных продажах,
        # учитываем эталонный эквайринг 1.5% (settings.ACQUIRING_RATE) без двойного списания.
        oz_fbo = _round_rub(_safe_float(ozon_fin.get("fbo_storage")))
        oz_cpo = _round_rub(_safe_float(ozon_fin.get("promo_cpo")))
        oz_cpc = _round_rub(_safe_float(ozon_fin.get("promo_cpc")))
        oz_ads = (
            _round_rub(_safe_float(ozon_fin.get("ads_total")))
            if ozon_fin.get("ads_total") is not None
            else _round_rub(_to_dec(oz_cpo) + _to_dec(oz_cpc))
        )
        oz_fines = _round_rub(_safe_float(ozon_fin.get("other_fines")))
        oz_decomp = _round_rub(_safe_float(ozon_fin.get("decompensations")))
        oz_mutual = _round_rub(_safe_float(ozon_fin.get("mutual_settlements")))

        oz_part_no_acq = (
            _round_rub(_safe_float(ozon_fin.get("partners_total")))
            if ozon_fin.get("partners_total") is not None
            else _round_rub(
                _to_dec(oz_dropoff)
                + _to_dec(oz_pack)
                + _to_dec(oz_pret)
                + _to_dec(oz_star)
                + _to_dec(oz_plm)
            )
        )
        known_oz_ded_no_acq = _round_rub(
            _to_dec(oz_comm)
            + _to_dec(oz_deliv)
            + _to_dec(oz_part_no_acq)
            + _to_dec(oz_fbo)
            + _to_dec(oz_ads)
            + _to_dec(oz_fines)
            + _to_dec(oz_decomp)
            + _to_dec(oz_mutual)
        )

        oz_acq_estimated = False
        if raw_oz_acq != 0.0 or ozon_fin.get("acquiring_reported"):
            oz_acq = raw_oz_acq
            oz_acq_delta = 0.0
        elif oz_sales > 0.0 and acq_rate > 0.0:
            est_oz_acq = _mul_rub(oz_sales, acq_rate)
            raw_tot_ded = ozon_fin.get("total_deductions")
            lump_sum_already_covers_acq = (
                "acquiring" not in ozon_fin
                and raw_tot_ded is not None
                and _safe_float(raw_tot_ded) >= known_oz_ded_no_acq + est_oz_acq - 0.05
            )
            oz_acq = est_oz_acq
            oz_acq_delta = 0.0 if lump_sum_already_covers_acq else est_oz_acq
            oz_acq_estimated = True
        else:
            oz_acq = 0.0
            oz_acq_delta = 0.0

        if ozon_fin.get("partners_total") is not None:
            oz_part = _round_rub(_to_dec(oz_part_no_acq) + _to_dec(oz_acq_delta))
        else:
            oz_part = _round_rub(
                _to_dec(oz_dropoff)
                + _to_dec(oz_pack)
                + _to_dec(oz_pret)
                + _to_dec(oz_acq)
                + _to_dec(oz_star)
                + _to_dec(oz_plm)
            )

        computed_oz_ded = _round_rub(
            _to_dec(oz_comm)
            + _to_dec(oz_deliv)
            + _to_dec(oz_part)
            + _to_dec(oz_fbo)
            + _to_dec(oz_ads)
            + _to_dec(oz_fines)
            + _to_dec(oz_decomp)
            + _to_dec(oz_mutual)
        )
        if ozon_fin.get("total_deductions") is not None:
            oz_ded_tot = _round_rub(_to_dec(_safe_float(ozon_fin.get("total_deductions"))) + _to_dec(oz_acq_delta))
        else:
            oz_ded_tot = computed_oz_ded

        if ozon_fin.get("total_payout") is not None:
            oz_payout = _round_rub(_to_dec(_safe_float(ozon_fin.get("total_payout"))) - _to_dec(oz_acq_delta))
        else:
            oz_payout = _round_rub(_to_dec(oz_sales) - _to_dec(oz_ded_tot))

        oz_cogs, oz_missing_cogs, oz_missing_skus = _calculate_marketplace_cogs(
            oz_orders, "ozon", by_mp_sku, by_mp_ext
        )
        oz_tax_base = max(oz_sales, 0.0)
        oz_tax = _mul_rub(oz_tax_base, tax_rate)
        oz_net_profit = _round_rub(_to_dec(oz_payout) - _to_dec(oz_cogs) - _to_dec(oz_tax))

        oz_source_low = str(ozon_fin.get("source") or "").lower()
        oz_profit_known = (
            oz_missing_cogs == 0
            and (oz_sales == 0 or bool(oz_orders))
            and not any(
                str(o.get("status") or "").lower() in {"return", "returned", "возврат"}
                for o in oz_orders
                if isinstance(o, dict)
            )
            and not any(
                mark in oz_source_low
                for mark in (
                    "незнакомые типы",
                    "accrual http",
                    "accrual runtimeerror",
                    "accrual timeouterror",
                    "accrual oserror",
                    "accrual client",
                )
            )
            and (oz_sales == 0 or all(k in ozon_fin for k in ("total_deductions", "total_payout")))
            and all(o.get("price") is not None for o in oz_orders if isinstance(o, dict))
        )
        oz_profit_line = (f"<code>{_fmt_money(oz_net_profit)} ₽</code> (<b>{_pct(oz_net_profit, oz_sales)}</b>)"
                          if oz_profit_known else "<b>н/д — требуется себестоимость и сверка начислений/возвратов</b>")
        oz_source = html.escape(str(ozon_fin.get("source") or "ЛК Ozon Финансы"))
        oz_sales_share = "100.0%" if oz_sales > 0.0 else "0.0%"
        oz_deliv_unit_str = (
            f" | факт. <b>{oz_deliv_per_order:.2f} ₽/заказ</b>"
            if oz_deliv_per_order is not None
            else ""
        )
        oz_acq_title = f"Эквайринг (расч. {acq_pct_label})" if oz_acq_estimated else "Эквайринг"
        oz_missing_note = _format_missing_cogs_note(oz_missing_cogs, oz_missing_skus)

        ozon_block = (
            f"🔵 <b>OZON ({oz_source}):</b>\n"
            f"• Реализовано заказов (отправлений): <b>{oz_count}</b>\n"
            f"• <b>Продажи и возвраты: {_fmt_money(oz_sales)} ₽</b> ({oz_sales_share})\n"
            f"• <b>Начисления МП (всего): {_fmt_expense(oz_ded_tot)}</b> ({_pct(oz_ded_tot, oz_sales)})\n\n"
            f"<b>Детализация расходов Ozon:</b>\n"
            f"• <b>Вознаграждение Ozon: {_fmt_expense(oz_comm)}</b> ({_pct(oz_comm, oz_sales)})\n"
            f"• <b>Услуги доставки: {_fmt_expense(oz_deliv)}</b> ({_pct(oz_deliv, oz_sales)}{oz_deliv_unit_str})\n"
            f"  ├ Логистика (прямая): {_fmt_expense(oz_d_dir)}\n"
            f"  ├ Обратная логистика: {_fmt_expense(oz_d_ret)}\n"
            f"  └ Доставка до места выдачи Ozon: {_fmt_expense(oz_d_ho)}\n"
            f"• <b>Услуги партнёров: {_fmt_expense(oz_part)}</b> ({_pct(oz_part, oz_sales)})\n"
            f"  ├ Обработка Drop-off (сдача в ПВЗ): {_fmt_expense(oz_dropoff)}\n"
            f"  ├ Упаковка товара партнёрами: {_fmt_expense(oz_pack)}\n"
            f"  ├ Обработка возвратов и невыкупов: {_fmt_expense(oz_pret)}\n"
            f"  ├ {oz_acq_title}: {_fmt_expense(oz_acq)} ({_pct(oz_acq, oz_sales)})\n"
            f"  ├ Звёздные товары: {_fmt_expense(oz_star)}\n"
            f"  └ Доставка до места выдачи партнёрами: {_fmt_expense(oz_plm)}\n"
            f"• <b>Услуги FBO (хранение на складе): {_fmt_expense(oz_fbo)}</b> ({_pct(oz_fbo, oz_sales)})\n"
            f"• <b>Продвижение и реклама: {_fmt_expense(oz_ads)}</b> ({_pct(oz_ads, oz_sales)})\n"
            f"  ├ Продвижение с оплатой за заказ: {_fmt_expense(oz_cpo)}\n"
            f"  └ Оплата за клик / Трафареты: {_fmt_expense(oz_cpc)}\n"
            f"• <b>Другие услуги и штрафы: {_fmt_expense(oz_fines)}</b> ({_pct(oz_fines, oz_sales)})\n"
            f"• <b>Компенсации и декомпенсации: {_fmt_expense(oz_decomp)}</b>\n"
            f"• <b>Взаимозачет между договорами: {_fmt_expense(oz_mutual)}</b>\n\n"
            f"💳 <b>Всего к выплате Ozon: {_fmt_money(oz_payout)} ₽</b>\n"
            f"📦 Учтённая себестоимость + упаковка + первая миля: <b>{_fmt_expense(oz_cogs)}</b> ({_pct(oz_cogs, oz_sales)})\n"
            f"{oz_missing_note}"
            f"🏛 Налог УСН ({tax_pct_label}): <b>{_fmt_expense(oz_tax)}</b> ({_pct(oz_tax, oz_sales)})\n"
            f"👉 <b>Чистая прибыль Ozon:</b> {oz_profit_line}\n\n"
        )

    # --- БЛОК WILDBERRIES ---
    if not isinstance(wb_fin, dict):
        api_errors_count += 1
        err_text = html.escape(str(wb_fin))
        wb_block = f"🟣 <b>WILDBERRIES:</b>\n❌ Ошибка API WB: <code>{err_text}</code>\n\n"
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = wb_acq = 0.0
        wb_count = wb_missing_cogs = 0
    elif wb_fin.get("summary_only"):
        api_errors_count += 1
        wb_rev_s = wb_net_profit = wb_cogs = wb_ded_tot = wb_tax = wb_acq = 0.0
        wb_count = wb_missing_cogs = 0
        rows = wb_fin.get("summary_reports") or []
        report_rows = "\n".join(
            f"• №{html.escape(str(r.get('id')))} ({html.escape(str(r.get('date_from')))}..{html.escape(str(r.get('date_to')))}): "
            f"retailAmountSum {_fmt_money(r.get('retail_amount_sum'))} ₽; forPaySum {_fmt_money(r.get('for_pay_sum'))} ₽"
            for r in rows
            if isinstance(r, dict)
        ) or "• Сводных отчётов WB за выбранный период не найдено."
        wb_block = (
            "🟣 <b>WILDBERRIES: только сводные отчёты</b>\n"
            f"{report_rows}\n"
            "⚠️ Нет построчной реализации и расшифровки базы налога/комиссии. "
            "Прибыль, количество, налог и общий итог WB не рассчитаны; "
            "значения отчёта не включены в сводные итоги.\n\n"
        )
    else:
        wb_orders = wb_fin.get("orders") if isinstance(wb_fin.get("orders"), list) else []
        raw_wb_count = wb_fin.get("orders_count")
        wb_count = (
            max(_safe_int(raw_wb_count, 0), 0)
            if raw_wb_count is not None
            else _count_active_orders(wb_orders)
        )

        wb_rev_s = round(_safe_float(wb_fin.get("revenue_seller")), 2)
        wb_rev_b = (
            round(_safe_float(wb_fin.get("revenue_buyer")), 2)
            if wb_fin.get("revenue_buyer") is not None
            else wb_rev_s
        )
        wb_ret = round(_safe_nonneg(wb_fin.get("returns_amount")), 2)
        wb_comm = round(_safe_float(wb_fin.get("commission")), 2)

        raw_wb_acq = _round_rub(_safe_float(wb_fin.get("acquiring")))
        wb_acq_base = max(wb_rev_b, wb_rev_s, 0.0)

        wb_log_ret = _round_rub(_safe_float(wb_fin.get("logistics_return")))
        if wb_fin.get("logistics") is not None:
            wb_log = _round_rub(_safe_float(wb_fin.get("logistics")))
            wb_log_dir = (
                _round_rub(_safe_float(wb_fin.get("logistics_direct")))
                if wb_fin.get("logistics_direct") is not None
                else _round_rub(max(_to_dec(wb_log) - _to_dec(wb_log_ret), Decimal("0")))
            )
        else:
            wb_log_dir = _round_rub(_safe_float(wb_fin.get("logistics_direct")))
            wb_log = _round_rub(_to_dec(wb_log_dir) + _to_dec(wb_log_ret))

        wb_stor = _round_rub(_safe_float(wb_fin.get("storage")))
        wb_acc = _round_rub(_safe_float(wb_fin.get("acceptance")))
        wb_ads = _round_rub(_safe_float(wb_fin.get("ads")))
        wb_other = _round_rub(_safe_float(wb_fin.get("other_services")))

        known_wb_ded_no_acq = _round_rub(
            _to_dec(wb_comm)
            + _to_dec(wb_log)
            + _to_dec(wb_stor)
            + _to_dec(wb_acc)
            + _to_dec(wb_ads)
            + _to_dec(wb_other)
        )

        wb_acq_estimated = bool(wb_fin.get("acquiring_estimated"))
        if wb_fin.get("acquiring") is not None:
            wb_acq = raw_wb_acq
            wb_acq_delta = 0.0
        elif wb_acq_base > 0.0 and acq_rate > 0.0:
            est_wb_acq = _mul_rub(wb_acq_base, acq_rate)
            raw_wb_tot_ded = wb_fin.get("total_deductions")
            wb_lump_covers_acq = (
                "acquiring" not in wb_fin
                and raw_wb_tot_ded is not None
                and _safe_float(raw_wb_tot_ded) >= known_wb_ded_no_acq + est_wb_acq - 0.05
            )
            wb_acq = est_wb_acq
            wb_acq_delta = 0.0 if wb_lump_covers_acq else est_wb_acq
            wb_acq_estimated = True
        else:
            wb_acq = 0.0
            wb_acq_delta = 0.0

        computed_wb_ded = _round_rub(_to_dec(known_wb_ded_no_acq) + _to_dec(wb_acq))
        if wb_fin.get("total_deductions") is not None:
            wb_ded_tot = _round_rub(_to_dec(wb_fin.get("total_deductions")) + _to_dec(wb_acq_delta))
        else:
            wb_ded_tot = computed_wb_ded

        if wb_fin.get("total_payout") is not None:
            wb_payout = _round_rub(_to_dec(_safe_float(wb_fin.get("total_payout"))) - _to_dec(wb_acq_delta))
        else:
            wb_payout = _round_rub(_to_dec(wb_rev_s) - _to_dec(wb_ded_tot))

        wb_cogs, wb_missing_cogs, wb_missing_skus = _calculate_marketplace_cogs(
            wb_orders, "wb", by_mp_sku, by_mp_ext
        )
        wb_tax_base = max(wb_rev_b, 0.0)
        wb_tax = _mul_rub(wb_tax_base, tax_rate)
        wb_net_profit = _round_rub(_to_dec(wb_payout) - _to_dec(wb_cogs) - _to_dec(wb_tax))

        wb_profit_known = (wb_missing_cogs == 0 and (wb_rev_s == 0 or bool(wb_orders))
                           and not _safe_nonneg(wb_fin.get("returns_amount"))
                           and (wb_rev_s == 0 or all(k in wb_fin for k in ("total_deductions", "total_payout", "revenue_buyer")))
                           and all(o.get("price") is not None for o in wb_orders if isinstance(o, dict)))
        wb_profit_line = (f"<code>{_fmt_money(wb_net_profit)} ₽</code> (<b>{_pct(wb_net_profit, wb_rev_s)}</b>)"
                          if wb_profit_known else "<b>н/д — требуется детализация продаж, себестоимости и возвратов</b>")
        wb_source = html.escape(str(wb_fin.get("source") or "ЛК Wildberries"))
        wb_sales_share = "100.0%" if wb_rev_s > 0.0 else "0.0%"
        wb_acq_title = (
            f"Эквайринг WB (расч. {acq_pct_label})" if wb_acq_estimated else "Эквайринг WB"
        )
        wb_log_unit_str = (
            f" | факт. <b>{_div_rub(wb_log, wb_count):.2f} ₽/заказ</b>" if wb_count > 0 else ""
        )
        wb_missing_note = _format_missing_cogs_note(wb_missing_cogs, wb_missing_skus)

        wb_block = (
            f"🟣 <b>WILDBERRIES ({wb_source}):</b>\n"
            f"• Строк реализации: <b>{wb_count}</b> (Отмены/возвраты: {_fmt_money(wb_ret)} ₽)\n"
            f"• <b>Продажи в ценах ЛК (priceWithDisc): {_fmt_money(wb_rev_s)} ₽</b> ({wb_sales_share})\n"
            f"• <b>Цена для покупателя с СПП (база УСН): {_fmt_money(wb_rev_b)} ₽</b>\n"
            f"• <b>Удержания WB (всего): {_fmt_expense(wb_ded_tot)}</b> ({_pct(wb_ded_tot, wb_rev_s)})\n\n"
            f"<b>Детализация расходов Wildberries:</b>\n"
            f"• <b>Вознаграждение WB (с НДС): {_fmt_expense(wb_comm)}</b> ({_pct(wb_comm, wb_rev_s)})\n"
            f"• <b>{wb_acq_title}: {_fmt_expense(wb_acq)}</b> ({_pct(wb_acq, wb_rev_s)})\n"
            f"• <b>Услуги логистики WB: {_fmt_expense(wb_log)}</b> ({_pct(wb_log, wb_rev_s)}{wb_log_unit_str})\n"
            f"  ├ Прямая доставка до покупателя: {_fmt_expense(wb_log_dir)}\n"
            f"  └ Обратная логистика (возвраты/отмены): {_fmt_expense(wb_log_ret)}\n"
            f"• <b>Платное хранение на складах WB: {_fmt_expense(wb_stor)}</b> ({_pct(wb_stor, wb_rev_s)})\n"
            f"• <b>Платная приёмка товара: {_fmt_expense(wb_acc)}</b> ({_pct(wb_acc, wb_rev_s)})\n"
            f"• <b>ВБ.Продвижение (Реклама): {_fmt_expense(wb_ads)}</b> ({_pct(wb_ads, wb_rev_s)})\n"
            f"• <b>Штрафы и прочие удержания: {_fmt_expense(wb_other)}</b> ({_pct(wb_other, wb_rev_s)})\n\n"
            f"💳 <b>Всего к перечислению WB: {_fmt_money(wb_payout)} ₽</b>\n"
            f"📦 Учтённая себестоимость + упаковка + первая миля: <b>{_fmt_expense(wb_cogs)}</b> ({_pct(wb_cogs, wb_rev_s)})\n"
            f"{wb_missing_note}"
            f"🏛 Налог УСН {tax_pct_label} (с {_fmt_money(wb_tax_base)} ₽): <b>{_fmt_expense(wb_tax)}</b>\n"
            f"👉 <b>Чистая прибыль WB:</b> {wb_profit_line}\n\n"
        )

    tot_count = oz_count + wb_count
    tot_sales = _round_rub(_to_dec(oz_sales) + _to_dec(wb_rev_s))
    tot_ded = _round_rub(_to_dec(oz_ded_tot) + _to_dec(wb_ded_tot))
    tot_acq = _round_rub(_to_dec(oz_acq) + _to_dec(wb_acq))
    tot_cogs = _round_rub(_to_dec(oz_cogs) + _to_dec(wb_cogs))
    tot_tax = _round_rub(_to_dec(oz_tax) + _to_dec(wb_tax))
    tot_profit = _round_rub(_to_dec(oz_net_profit) + _to_dec(wb_net_profit))
    tot_missing_cogs = oz_missing_cogs + wb_missing_cogs

    summary_warnings: List[str] = []
    if api_errors_count > 0:
        summary_warnings.append(
            "⚠️ <i>Внимание: данные одного или обоих маркетплейсов недоступны — сводный итог неполный.</i>"
        )
    elif tot_count == 0 and tot_sales == 0.0 and tot_ded == 0.0:
        summary_warnings.append("ℹ️ <i>За выбранный период продаж и удержаний не зафиксировано.</i>")
    if tot_missing_cogs > 0:
        summary_warnings.append(
            f"⚠️ <i>По {tot_missing_cogs} ед. товара отсутствует себестоимость в БД — внесите её через /costs.</i>"
        )
    profit_summary = (f"<code>{_fmt_money(tot_profit)} ₽</code> (<b>{_pct(tot_profit, tot_sales)} ROS</b>)"
                      if api_errors_count == 0 and oz_profit_known and wb_profit_known
                      else "<b>н/д — данные для расчёта неполны</b>")
    if oz_profit_known or wb_profit_known:
        summary_warnings.append("ℹ️ <i>Прибыль и УСН — расчётные: налоговую базу, возвраты и удержания необходимо сверить с ЛК и учётом.</i>")
    warnings_suffix = ("\n" + "\n".join(summary_warnings)) if summary_warnings else ""
    safe_title = html.escape(str(report_title))
    return (
        f"<b>{safe_title}</b>\n\n"
        f"{ozon_block}"
        f"{wb_block}"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏁 <b>СВОДНЫЙ ИТОГ (Ozon + WB):</b>\n"
        f"• Заказы Ozon + реализации WB: <b>{tot_count}</b>\n"
        f"• Общий оборот (в ценах ЛК): <b>{_fmt_money(tot_sales)} ₽</b>\n"
        f"• Все удержания маркетплейсов: <b>{_fmt_expense(tot_ded)}</b> ({_pct(tot_ded, tot_sales)}), в т.ч. эквайринг <b>{_fmt_expense(tot_acq)}</b>\n"
        f"• Учтённая себестоимость, упаковка и первая миля: <b>{_fmt_expense(tot_cogs)}</b> ({_pct(tot_cogs, tot_sales)})\n"
        f"• Налог УСН {tax_pct_label}: <b>{_fmt_expense(tot_tax)}</b>\n"
        f"💰 <b>ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> {profit_summary}"
        f"{warnings_suffix}"
    )
