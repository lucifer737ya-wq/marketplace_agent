import asyncio
import html
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set

from services.common.gemini import generate
from sqlalchemy import select

from config import settings
from database.models import Product, get_db_session
from products_catalog import OZON_SKU_TO_ART, PRODUCTS_CATALOG, WB_SKU_TO_ART
from services.common.orders_report import (
    _div_rub,
    _fmt_money,
    _mul_rub,
    _round_pct,
    _round_rub,
    _safe_float,
    _safe_int,
    _safe_nonneg,
    _to_dec,
)

INACTIVE_CAMPAIGN_STATES: Set[str] = {
    "INACTIVE",
    "STOPPED",
    "ARCHIVED",
    "FINISHED",
    "CANCELLED",
    "CANCELED",
    "PAUSED",
    "CAMPAIGN_STATE_INACTIVE",
    "CAMPAIGN_STATE_STOPPED",
    "CAMPAIGN_STATE_ARCHIVED",
    "CAMPAIGN_STATE_FINISHED",
    "CAMPAIGN_STATE_PLANNED",
}

_TAG_TOKEN_RE = re.compile(r"<(/?)(b|i|code)>", re.IGNORECASE)


def sanitize_telegram_html(raw_text: str) -> str:
    """
    Очищает текст для безопасной отправки в Telegram (parse_mode='HTML'):
    убирает Markdown-разметку, экранирует произвольные '<', '>', '&' и гарантирует
    строгий баланс разрешенных тегов <b>, <i>, <code>.
    """
    if not raw_text:
        return ""

    cleaned = re.sub(r"^#{1,6}\s*", "", raw_text, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", cleaned)

    out: List[str] = []
    open_stack: List[str] = []
    cursor = 0

    for match in _TAG_TOKEN_RE.finditer(cleaned):
        start, end = match.span()
        if start > cursor:
            out.append(html.escape(cleaned[cursor:start]))
        is_closing = bool(match.group(1))
        tag = match.group(2).lower()

        if not is_closing:
            if tag not in open_stack:
                open_stack.append(tag)
                out.append(f"<{tag}>")
        else:
            if tag in open_stack:
                to_reopen: List[str] = []
                while open_stack:
                    top = open_stack.pop()
                    out.append(f"</{top}>")
                    if top == tag:
                        break
                    to_reopen.append(top)
                for reopen_tag in reversed(to_reopen):
                    open_stack.append(reopen_tag)
                    out.append(f"<{reopen_tag}>")
        cursor = end

    if cursor < len(cleaned):
        out.append(html.escape(cleaned[cursor:]))

    while open_stack:
        out.append(f"</{open_stack.pop()}>")

    return "".join(out).strip()


async def _call_gemini_unlimited(prompt: str) -> str:
    try:
        text, _ = await generate(prompt, max_tokens=8192, temperature=0.15)
        return sanitize_telegram_html(text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — сбой ИИ не должен ронять фактический отчёт
        return f"⚠️ <i>Рекомендации ИИ временно недоступны ({html.escape(str(exc))}).</i>"


def calculate_ozon_ad_unit_economics(
    price: float,
    cogs_rub: Optional[float],
    actual_delivery_rub: Optional[float],
    commission_rate_under_300: float,
    drr_pct: Optional[float],
    fbs_dropoff_rub: float = 0.0,
    overhead_per_order_rub: float = 0.0,
) -> Dict[str, Optional[float]]:
    """
    Точный поштучный расчет юнит-экономики SKU Ozon с учетом:
    - комиссии API до 300 ₽ (22–23%) и 52% свыше 300 ₽ (settings.OZON_HIGH_COMMISSION_RATE);
    - эквайринга 1.5% (settings.ACQUIRING_RATE);
    - налога УСН 6% (settings.TAX_RATE);
    - фактической логистики Ozon (~39 ₽/заказ из отчёта, без выдуманных констант);
    - индивидуального ДРР конкретной кампании (например, 6.3%, 15.4% или 0.0% для органики).
    """
    clean_price = round(_safe_nonneg(price), 2)
    tax_rate = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
    acq_rate = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
    low_limit = _safe_nonneg(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0)
    high_comm = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)

    if clean_price <= 0.0:
        return {
            "price": 0.0,
            "comm_pct": 0.0,
            "comm_rub": 0.0,
            "acq_rub": 0.0,
            "tax_rub": 0.0,
            "ad_pct": round(drr_pct, 1) if drr_pct is not None else None,
            "ad_rub": None if drr_pct is None else 0.0,
            "delivery_rub": round(actual_delivery_rub, 2) if actual_delivery_rub is not None else None,
            "pvz_rub": round(_safe_nonneg(fbs_dropoff_rub), 2),
            "overhead_rub": round(_safe_nonneg(overhead_per_order_rub), 2),
            "cogs_rub": round(cogs_rub, 2) if cogs_rub is not None else None,
            "net_profit": None,
            "ros_pct": 0.0,
            "breakeven_drr_pct": None,
        }

    base_comm = _safe_nonneg(commission_rate_under_300)
    if not 0.22 <= base_comm <= 0.23:
        base_comm = 0.23
    eff_comm = base_comm if clean_price <= low_limit else high_comm

    comm_rub = _mul_rub(clean_price, eff_comm)
    acq_rub = _mul_rub(clean_price, acq_rate)
    tax_rub = _mul_rub(clean_price, tax_rate)
    ad_rub: Optional[float] = (
        _round_rub((_to_dec(clean_price) * _to_dec(_safe_nonneg(drr_pct))) / _to_dec(100))
        if drr_pct is not None
        else None
    )
    pvz_rub = _round_rub(_safe_nonneg(fbs_dropoff_rub))
    ovh_rub = _round_rub(_safe_nonneg(overhead_per_order_rub))

    if cogs_rub is None or actual_delivery_rub is None:
        return {
            "price": clean_price,
            "comm_pct": round(eff_comm * 100.0, 1),
            "comm_rub": comm_rub,
            "acq_rub": acq_rub,
            "tax_rub": tax_rub,
            "ad_pct": round(_safe_nonneg(drr_pct), 1) if drr_pct is not None else None,
            "ad_rub": ad_rub,
            "delivery_rub": _round_rub(actual_delivery_rub) if actual_delivery_rub is not None else None,
            "pvz_rub": pvz_rub,
            "overhead_rub": ovh_rub,
            "cogs_rub": _round_rub(cogs_rub) if cogs_rub is not None else None,
            "net_profit": None,
            "ros_pct": None,
            "breakeven_drr_pct": None,
        }

    deliv_rub = _round_rub(_safe_nonneg(actual_delivery_rub))
    clean_cogs = _round_rub(_safe_nonneg(cogs_rub))
    profit_before_ads = _round_rub(
        _to_dec(clean_price)
        - _to_dec(comm_rub)
        - _to_dec(acq_rub)
        - _to_dec(tax_rub)
        - _to_dec(deliv_rub)
        - _to_dec(pvz_rub)
        - _to_dec(ovh_rub)
        - _to_dec(clean_cogs)
    )
    breakeven_drr_pct = max(_round_pct(profit_before_ads, clean_price), 0.0)

    if ad_rub is None:
        net_profit: Optional[float] = None
        ros_pct: Optional[float] = None
    else:
        net_profit = _round_rub(_to_dec(profit_before_ads) - _to_dec(ad_rub))
        ros_pct = _round_pct(net_profit, clean_price)

    return {
        "price": clean_price,
        "comm_pct": round(eff_comm * 100.0, 1),
        "comm_rub": comm_rub,
        "acq_rub": acq_rub,
        "tax_rub": tax_rub,
        "ad_pct": round(_safe_nonneg(drr_pct), 1) if drr_pct is not None else None,
        "ad_rub": ad_rub,
        "delivery_rub": deliv_rub,
        "pvz_rub": pvz_rub,
        "overhead_rub": ovh_rub,
        "cogs_rub": clean_cogs,
        "net_profit": net_profit,
        "ros_pct": ros_pct,
        "breakeven_drr_pct": breakeven_drr_pct,
    }


def _extract_raw_campaign_skus(camp: Dict[str, Any]) -> List[str]:
    raw_seq = camp.get("skus_inside")
    if raw_seq is None:
        raw_seq = camp.get("products")
    if raw_seq is None:
        raw_seq = camp.get("skus")
    if not isinstance(raw_seq, list):
        return []

    extracted: List[str] = []
    seen: Set[str] = set()
    for entry in raw_seq:
        if isinstance(entry, dict):
            candidate = (
                entry.get("offer_id")
                or entry.get("sku")
                or entry.get("product_id")
                or entry.get("id")
                or ""
            )
        else:
            candidate = entry
        token = str(candidate or "").strip()
        if not token or token.lower() == "none" or token in seen:
            continue
        seen.add(token)
        extracted.append(token)
    return extracted


def _is_campaign_active(camp: Dict[str, Any]) -> bool:
    state = str(camp.get("state") or "").strip().upper()
    if not state:
        return True
    if state in INACTIVE_CAMPAIGN_STATES:
        return False
    if any(marker in state for marker in ("STOPPED", "INACTIVE", "ARCHIVED", "FINISHED", "PAUSED")):
        return False
    return True


def analyze_ozon_campaigns(
    raw_campaigns: Optional[Sequence[Dict[str, Any]]],
    oz_stocks: Optional[Sequence[Dict[str, Any]]],
    oz_fin: Optional[Dict[str, Any]],
    db_products: Optional[Sequence[Product]] = None,
    campaigns_error: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Разбирает рекламные кампании Ozon и строго отделяет продвигаемые SKU от органических:
    - без подмены пустых или недоступных данных фейковыми заглушками;
    - без смешивания выручки/SKU между Кампанией №1, Кампанией №2 и органическими товарами;
    - без деления на ноль при пустом периоде или нулевой атрибутированной выручке.
    """
    # 1. Финансовые итоги Ozon за период (строго фактические, без 172 и 6686 по умолчанию)
    fin_dict = oz_fin if isinstance(oz_fin, dict) else {}
    oz_orders_cnt = max(_safe_int(fin_dict.get("orders_count"), 0), 0)
    oz_sales_total = round(_safe_float(fin_dict.get("sales_total")), 2)
    oz_deliv_total = round(
        _safe_nonneg(fin_dict.get("deliv_total"))
        if fin_dict.get("deliv_total") is not None
        else (
            _safe_nonneg(fin_dict.get("deliv_direct"))
            + _safe_nonneg(fin_dict.get("deliv_return"))
            + _safe_nonneg(fin_dict.get("deliv_ozon_handover"))
        ),
        2,
    )
    actual_delivery_per_order: Optional[float] = (
        _div_rub(oz_deliv_total, oz_orders_cnt)
        if oz_orders_cnt > 0 and oz_deliv_total > 0.0
        else None
    )

    dropoff_total = _safe_nonneg(fin_dict.get("partner_dropoff"))
    actual_dropoff_per_order = (
        _div_rub(dropoff_total, oz_orders_cnt) if oz_orders_cnt > 0 and dropoff_total > 0.0 else 0.0
    )

    other_hold_total = (
        _safe_nonneg(fin_dict.get("partner_pack"))
        + _safe_nonneg(fin_dict.get("partner_returns"))
        + _safe_nonneg(fin_dict.get("star_products"))
        + _safe_nonneg(fin_dict.get("partner_last_mile"))
        + _safe_nonneg(fin_dict.get("fbo_storage"))
        + _safe_nonneg(fin_dict.get("other_fines"))
        + _safe_nonneg(fin_dict.get("decompensations"))
        + _safe_nonneg(fin_dict.get("mutual_settlements"))
    )
    overhead_per_order = (
        _div_rub(other_hold_total, oz_orders_cnt)
        if oz_orders_cnt > 0 and other_hold_total > 0.0
        else 0.0
    )

    promo_cpo = _round_rub(_safe_nonneg(fin_dict.get("promo_cpo")))
    promo_cpc = _round_rub(_safe_nonneg(fin_dict.get("promo_cpc")))
    ads_total = (
        _round_rub(_safe_nonneg(fin_dict.get("ads_total")))
        if fin_dict.get("ads_total") is not None
        else _round_rub(_to_dec(promo_cpo) + _to_dec(promo_cpc))
    )
    store_tacos_pct = _round_pct(ads_total, oz_sales_total) if oz_sales_total > 0.0 else 0.0

    # 2. Индексация себестоимости и остатков Ozon (строго marketplace == 'ozon')
    oz_cogs_by_sku: Dict[str, float] = {}
    prod_id_to_art: Dict[str, str] = {}
    for prod in db_products or []:
        if str(prod.marketplace or "").strip().lower() != "ozon":
            continue
        sku_key = str(prod.sku or "").strip()
        ext_key = str(prod.external_id or "").strip()
        if sku_key and ext_key:
            prod_id_to_art[ext_key] = sku_key
        if sku_key and _safe_nonneg(prod.cost_price) > 0.0:
            oz_cogs_by_sku[sku_key] = round(
                _safe_nonneg(prod.cost_price)
                + _safe_nonneg(prod.packaging_cost)
                + _safe_nonneg(prod.first_mile_unit_cost),
                2,
            )

    stock_by_art: Dict[str, Dict[str, Any]] = {}
    market_sku_to_art = dict(OZON_SKU_TO_ART)
    known_offer_ids: Set[str] = set(PRODUCTS_CATALOG.keys())
    for item in oz_stocks or []:
        if not isinstance(item, dict):
            continue
        raw_offer = str(item.get("sku") or "").strip()
        if not raw_offer:
            continue
        art = OZON_SKU_TO_ART.get(raw_offer, raw_offer)
        known_offer_ids.add(art)
        stock_by_art[art] = item
        for market_sku in item.get("ozon_skus", []):
            market_sku_to_art[str(market_sku)] = art
        pid = str(item.get("product_id") or item.get("external_id") or "").strip()
        if pid:
            prod_id_to_art[pid] = art

    def resolve_ozon_identifier(raw_id: str) -> str:
        clean = str(raw_id or "").strip()
        if not clean:
            return ""
        if clean in known_offer_ids:
            return clean
        if clean in market_sku_to_art:
            return market_sku_to_art[clean]
        if clean in prod_id_to_art:
            return prod_id_to_art[clean]
        return clean

    def build_sku_card(art: str, drr_pct: Optional[float]) -> Dict[str, Any]:
        st_item = stock_by_art.get(art, {})
        fbo_st = max(_safe_int(st_item.get("stock_fbo"), 0), 0)
        fbs_st = max(_safe_int(st_item.get("stock_fbs"), 0), 0)
        total_st = fbo_st + fbs_st
        daily_sales = round(_safe_nonneg(st_item.get("daily_sales")), 2)
        days_of_stock: Optional[float] = (
            round(total_st / daily_sales, 1) if daily_sales > 0.0 else None
        )

        price = round(_safe_nonneg(st_item.get("current_price")), 2)
        c_fbo = _safe_nonneg(st_item.get("commission_rate_fbo") or st_item.get("commission_rate"))
        c_fbs = _safe_nonneg(st_item.get("commission_rate_fbs") or c_fbo)
        if fbo_st > 0 and fbs_st > 0:
            weighted_comm = (c_fbo * fbo_st + c_fbs * fbs_st) / total_st
            fbs_share = fbs_st / total_st
        elif fbs_st > 0:
            weighted_comm = c_fbs if c_fbs > 0.0 else c_fbo
            fbs_share = 1.0
        else:
            weighted_comm = c_fbo
            fbs_share = 0.0

        pvz = st_item.get("fbs_pvz_dropoff_rub")
        weighted_pvz = (round(float(pvz) * fbs_share, 2) if pvz is not None else actual_dropoff_per_order) if fbs_share else 0.0
        cogs_val = oz_cogs_by_sku.get(art)
        econ_current = calculate_ozon_ad_unit_economics(
            price=price,
            cogs_rub=cogs_val,
            actual_delivery_rub=actual_delivery_per_order,
            commission_rate_under_300=weighted_comm if weighted_comm > 0.0 else 0.23,
            drr_pct=drr_pct,
            fbs_dropoff_rub=weighted_pvz,
            overhead_per_order_rub=overhead_per_order,
        )
        low_comm_for_tiers = (
            weighted_comm
            if 0.0 < weighted_comm < _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)
            else 0.23
        )
        econ_299 = calculate_ozon_ad_unit_economics(
            price=299.0,
            cogs_rub=cogs_val,
            actual_delivery_rub=actual_delivery_per_order,
            commission_rate_under_300=low_comm_for_tiers,
            drr_pct=drr_pct,
            fbs_dropoff_rub=weighted_pvz,
            overhead_per_order_rub=overhead_per_order,
        )

        econ_over300: Optional[Dict[str, Optional[float]]] = None
        if cogs_val is not None and actual_delivery_per_order is not None and drr_pct is not None:
            high_comm = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52)
            acq_r = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015)
            tax_r = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06)
            low_lim = _safe_nonneg(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0)
            remaining_share = 1.0 - high_comm - acq_r - tax_r - (_safe_nonneg(drr_pct) / 100.0)
            if remaining_share > 0.01:
                base_299_prof = econ_299["net_profit"] if econ_299["net_profit"] is not None else 0.0
                target_profit = max(base_299_prof + 25.0, 55.0)
                fixed_costs = cogs_val + actual_delivery_per_order + weighted_pvz + overhead_per_order
                over300_price = float(
                    max(int(low_lim) + 1, math.ceil((fixed_costs + target_profit + 0.02) / remaining_share))
                )
                econ_over300 = calculate_ozon_ad_unit_economics(
                    price=over300_price,
                    cogs_rub=cogs_val,
                    actual_delivery_rub=actual_delivery_per_order,
                    commission_rate_under_300=low_comm_for_tiers,
                    drr_pct=drr_pct,
                    fbs_dropoff_rub=weighted_pvz,
                    overhead_per_order_rub=overhead_per_order,
                )

        cat_meta = PRODUCTS_CATALOG.get(art, {})
        return {
            "sku": art,
            "ozon_sku": str(cat_meta.get("ozon_sku") or ""),
            "product_id": str(st_item.get("product_id") or st_item.get("external_id") or ""),
            "name": str(cat_meta.get("name") or f"Арт. {art}"),
            "current_price": price,
            "in_promo": bool(st_item.get("in_promo", False)),
            "stock_fbo": fbo_st,
            "stock_fbs": fbs_st,
            "total_stock": total_st,
            "daily_sales": daily_sales,
            "days_of_stock": days_of_stock,
            "unit_economics": econ_current,
            "tier_299_economics": econ_299,
            "tier_over300_economics": econ_over300,
        }

    # 3. Если Performance API вернул ошибку — не подменяем кампании и не смешиваем с органикой
    if campaigns_error is not None or raw_campaigns is None:
        unclassified = [
            build_sku_card(art, drr_pct=None)
            for art, st in stock_by_art.items()
            if max(_safe_int(st.get("stock_fbo"), 0), 0) + max(_safe_int(st.get("stock_fbs"), 0), 0) > 0
        ]
        return {
            "campaigns_available": False,
            "campaigns_error": str(campaigns_error or "Данные кампаний Ozon не получены"),
            "campaigns": [],
            "promoted_skus": [],
            "organic_items": [],
            "unclassified_items": unclassified,
            "overlapping_skus": [],
            "zero_stock_promoted_skus": [],
            "low_stock_promoted_skus": [],
            "finance_available": isinstance(oz_fin, dict),
            "finance": {
                "orders_count": oz_orders_cnt,
                "sales_total": oz_sales_total,
                "deliv_total": oz_deliv_total,
                "actual_delivery_per_order": actual_delivery_per_order,
                "dropoff_per_order": actual_dropoff_per_order,
                "overhead_per_order": overhead_per_order,
                "ads_total": ads_total,
                "promo_cpo": promo_cpo,
                "promo_cpc": promo_cpc,
                "store_tacos_pct": store_tacos_pct,
            },
        }

    # 4. Точный разбор каждой активной РК Ozon
    parsed_campaigns: List[Dict[str, Any]] = []
    sku_to_campaigns: Dict[str, List[str]] = {}
    promoted_set: Set[str] = set()
    zero_stock_promoted: Set[str] = set()
    low_stock_promoted: Set[str] = set()

    for camp in raw_campaigns:
        if not isinstance(camp, dict) or not _is_campaign_active(camp):
            continue

        cid = str(camp.get("id") or "").strip()
        title = str(camp.get("title") or (f"Кампания #{cid}" if cid else "Кампания Ozon")).strip()
        state = str(camp.get("state") or "ACTIVE").strip()

        raw_skus = _extract_raw_campaign_skus(camp)
        resolved_skus: List[str] = []
        seen_resolved: Set[str] = set()
        for raw_token in raw_skus:
            art = resolve_ozon_identifier(raw_token)
            if art and art not in seen_resolved:
                seen_resolved.add(art)
                resolved_skus.append(art)

        spend_raw = camp.get("spend_7d_rub")
        if spend_raw is None:
            spend_raw = camp.get("spend_rub")
        rev_raw = camp.get("promo_revenue_7d_rub")
        if rev_raw is None:
            rev_raw = camp.get("revenue_rub")
        drr_raw = camp.get("drr_in_promo_pct")
        if drr_raw is None:
            drr_raw = camp.get("drr_pct")

        spend_rub = _round_rub(spend_raw) if spend_raw is not None else None
        revenue_rub = _round_rub(rev_raw) if rev_raw is not None else None
        if spend_rub is not None and revenue_rub is not None and revenue_rub > 0 and spend_rub >= 0:
            drr_pct, drr_status = _round_pct(spend_rub, revenue_rub), 'calculated'
        elif spend_rub and spend_rub > 0 and revenue_rub == 0:
            drr_pct, drr_status = None, 'spend_without_revenue'
        elif drr_raw is not None and camp.get('stats_available') is not False:
            drr_pct, drr_status = _round_rub(drr_raw), 'reported'
        else:
            drr_pct, drr_status = None, 'unavailable'

        camp_items: List[Dict[str, Any]] = []
        for art in resolved_skus:
            promoted_set.add(art)
            sku_to_campaigns.setdefault(art, []).append(cid or title)
            card = build_sku_card(art, drr_pct=drr_pct)
            camp_items.append(card)
            if art in stock_by_art and card["total_stock"] <= 0:
                zero_stock_promoted.add(art)
            elif card["days_of_stock"] is not None and card["days_of_stock"] < 2.0:
                low_stock_promoted.add(art)

        parsed_campaigns.append(
            {
                "id": cid,
                "title": title,
                "state": state,
                "products_count": len(resolved_skus),
                "skus": resolved_skus,
                "raw_skus": raw_skus,
                "spend_7d_rub": spend_rub,
                "promo_revenue_7d_rub": revenue_rub,
                "drr_in_promo_pct": drr_pct,
                "drr_status": drr_status,
                "items": camp_items,
                "products_missing": not resolved_skus or any(art not in known_offer_ids for art in resolved_skus),
                "source": str(camp.get("source") or "Ozon Performance API"),
            }
        )

    # 5. Строгое выделение органических SKU (в наличии и НЕ входят ни в одну активную РК)
    billed = {resolve_ozon_identifier(s) for s in fin_dict.get("billed_skus", [])}
    has_unlisted_campaign_products = any(c["products_missing"] for c in parsed_campaigns)
    # A debit with neither a matching campaign expense nor SKU attribution cannot be organic.
    unmatched_spend = not billed and ads_total - sum(c['spend_7d_rub'] or 0 for c in parsed_campaigns) > .01
    organic_items: List[Dict[str, Any]] = []
    unclassified_items: List[Dict[str, Any]] = []

    for art, st in stock_by_art.items():
        total_st = max(_safe_int(st.get("stock_fbo"), 0), 0) + max(_safe_int(st.get("stock_fbs"), 0), 0)
        if total_st <= 0 or art in promoted_set:
            continue
        if not isinstance(oz_fin, dict) or unmatched_spend or has_unlisted_campaign_products or art in billed:
            # Если хотя бы одна активная РК вернулась без списка товаров, не объявляем остальные SKU органикой
            unclassified_items.append(build_sku_card(art, drr_pct=None))
        else:
            organic_items.append(build_sku_card(art, drr_pct=0.0))

    overlapping_skus = sorted(sku for sku, camps in sku_to_campaigns.items() if len(camps) > 1)

    return {
        "campaigns_available": True,
        "campaigns_error": None,
        "campaigns": parsed_campaigns,
        "promoted_skus": sorted(promoted_set),
        "organic_items": organic_items,
        "unclassified_items": unclassified_items,
        "overlapping_skus": overlapping_skus,
        "zero_stock_promoted_skus": sorted(zero_stock_promoted),
        "low_stock_promoted_skus": sorted(low_stock_promoted),
        "finance_available": isinstance(oz_fin, dict),
        "finance": {
            "orders_count": oz_orders_cnt,
            "sales_total": oz_sales_total,
            "deliv_total": oz_deliv_total,
            "actual_delivery_per_order": actual_delivery_per_order,
            "dropoff_per_order": actual_dropoff_per_order,
            "overhead_per_order": overhead_per_order,
            "ads_total": ads_total,
            "promo_cpo": promo_cpo,
            "promo_cpc": promo_cpc,
            "store_tacos_pct": store_tacos_pct,
        },
    }


def _format_drr_label(camp: Dict[str, Any]) -> str:
    status = camp.get("drr_status")
    drr = camp.get("drr_in_promo_pct")
    if status == "spend_without_revenue":
        return "∞ (расход без заказов!)"
    if drr is None:
        return "н/д"
    return f"{float(drr):.1f}%"


def _format_unit_profit_brief(card: Dict[str, Any]) -> str:
    econ = card["unit_economics"]
    price = card["current_price"]
    if price <= 0.0:
        return "цена н/д"
    ad_rub = econ.get("ad_rub")
    net = econ.get("net_profit")
    be_drr = econ.get("breakeven_drr_pct")
    parts = [f"цена {price:.0f} ₽"]
    if ad_rub is not None and ad_rub > 0.0:
        parts.append(f"реклама -{ad_rub:.1f} ₽")
    if net is not None:
        parts.append(f"чистая прибыль {net:+.1f} ₽/шт.")
        if be_drr is not None:
            parts.append(f"безубыт. ДРР {be_drr:.1f}%")
    else:
        parts.append("прибыль н/д (нужны себестоимость и факт. доставка)")
    return ", ".join(parts)


def _render_factual_ads_header(
    oz_analysis: Dict[str, Any],
    wb_campaigns: Sequence[Dict[str, Any]],
    wb_fin: Optional[Dict[str, Any]],
    api_errors: Sequence[str],
) -> str:
    fin = oz_analysis["finance"]
    deliv_unit = fin["actual_delivery_per_order"]
    deliv_str = (
        f"<b>{deliv_unit:.2f} ₽/заказ</b> ({_fmt_money(fin['deliv_total'])} ₽ / {fin['orders_count']} шт.)"
        if deliv_unit is not None
        else "<i>н/д (нет заказов или данных доставки за период)</i>"
    )

    lines: List[str] = [
        "🔵 <b>ФАКТИЧЕСКИЙ СРЕЗ РЕКЛАМЫ OZON (7 дн.):</b>",
        f"• Выручка магазина: <b>{_fmt_money(fin['sales_total'])} ₽</b> | Заказов: <b>{fin['orders_count']} шт.</b>",
        f"• Фактическая логистика Ozon: {deliv_str}",
        (
            f"• Общие списания на рекламу (фин. отчёт): <b>{_fmt_money(fin['ads_total'])} ₽</b> "
            f"(TACoS {fin['store_tacos_pct']:.1f}% | CPO: {_fmt_money(fin['promo_cpo'])} ₽, CPC: {_fmt_money(fin['promo_cpc'])} ₽)"
        ),
    ]

    if not oz_analysis['finance_available']:
        lines[1:] = ['• Выручка, доставка и списания: нет финансового отчёта за период.']

    if not oz_analysis["campaigns_available"]:
        err_msg = html.escape(str(oz_analysis.get("campaigns_error") or "API недоступен"))
        lines.append(
            f"⚠️ <b>Состав кампаний Ozon не получен:</b> <code>{err_msg}</code>\n"
            f"<i>Фейковые кампании не подставляются; разделение на рекламные и органические SKU отложено до ответа Performance API.</i>"
        )
    elif not oz_analysis["campaigns"]:
        lines.append("• Активных рекламных кампаний в Ozon Performance API не обнаружено.")
    else:
        for idx, camp in enumerate(oz_analysis["campaigns"], 1):
            safe_title = html.escape(camp["title"])
            safe_id = html.escape(camp["id"]) if camp["id"] else "—"
            drr_lbl = _format_drr_label(camp)
            lines.append(
                f"\n🎯 <b>Кампания №{idx}: {safe_title}</b> (ID: <code>{safe_id}</code>)\n"
                f"• Товаров в РК: <b>{camp['products_count']} шт.</b> | ДРР в продвижении: <b>{drr_lbl}</b>\n"
                + (f"• Расход 7 дн.: <b>{_fmt_money(camp['spend_7d_rub'])} ₽</b> | "
                 f"Выручка в РК: <b>{_fmt_money(camp['promo_revenue_7d_rub'])} ₽</b>"
                 if None not in (camp["spend_7d_rub"], camp["promo_revenue_7d_rub"]) else "• Расход и выручка в РК: н/д")
            )
            if camp["products_missing"]:
                lines.append("  ⚠️ <i>API не вернул список SKU внутри кампании.</i>")
            for card in camp["items"]:
                safe_sku = html.escape(card["sku"])
                lines.append(
                    f"  ├ Арт. <code>{safe_sku}</code>: FBO {card['stock_fbo']} / FBS {card['stock_fbs']} шт. | "
                    f"{_format_unit_profit_brief(card)}"
                )

    if oz_analysis["organic_items"]:
        lines.append(
            f"\n🌿 <b>Органические SKU Ozon (вне рекламы — {len(oz_analysis['organic_items'])} шт., ДРР 0.0%):</b>"
        )
        for card in oz_analysis["organic_items"]:
            safe_sku = html.escape(card["sku"])
            lines.append(
                f"  ├ Арт. <code>{safe_sku}</code>: FBO {card['stock_fbo']} / FBS {card['stock_fbs']} шт. | "
                f"{_format_unit_profit_brief(card)}"
            )
    if oz_analysis["unclassified_items"]:
        uncl_skus = ", ".join(html.escape(c["sku"]) for c in oz_analysis["unclassified_items"])
        lines.append(
            f"\n⚠️ <i>SKU с неподтверждённым статусом рекламы: <code>{uncl_skus}</code>.</i>"
        )

    if oz_analysis["overlapping_skus"]:
        ov_str = ", ".join(html.escape(s) for s in oz_analysis["overlapping_skus"])
        lines.append(f"\n🚨 <b>Пересечение SKU между кампаниями Ozon:</b> <code>{ov_str}</code>")
    if oz_analysis["zero_stock_promoted_skus"]:
        zs_str = ", ".join(html.escape(s) for s in oz_analysis["zero_stock_promoted_skus"])
        lines.append(f"🛑 <b>Нулевой остаток у товаров в рекламе Ozon:</b> <code>{zs_str}</code>")
    if oz_analysis["low_stock_promoted_skus"]:
        ls_str = ", ".join(html.escape(s) for s in oz_analysis["low_stock_promoted_skus"])
        lines.append(f"⚠️ <b>Остаток менее 2 дней в рекламе Ozon:</b> <code>{ls_str}</code>")

    # Блок Wildberries
    wb_fin_dict = wb_fin if isinstance(wb_fin, dict) else {}
    wb_rev = _safe_float(wb_fin_dict.get("revenue_seller"))
    wb_summary_only = bool(wb_fin_dict.get("summary_only"))
    wb_camp_spend = _round_rub(
        sum(
            _safe_nonneg(c.get("spend_7d_rub") or c.get("spend") or c.get("updSum"))
            for c in wb_campaigns
            if isinstance(c, dict)
        )
    )
    lines.append("\n🟣 <b>ФАКТИЧЕСКИЙ СРЕЗ РЕКЛАМЫ WB (7 дн.):</b>")
    if isinstance(wb_fin, dict) and wb_summary_only:
        spend_note = (
            f"Списания по РК WB: <b>{_fmt_money(wb_camp_spend)} ₽</b>"
            if wb_camp_spend > 0.0
            else "Списания ВБ.Продвижение: <i>н/д (только сводный финотчёт WB)</i>"
        )
        lines.append(
            f"• Выручка (сводный отчёт): <b>{_fmt_money(wb_rev)} ₽</b> | {spend_note}"
        )
    elif isinstance(wb_fin, dict):
        wb_ads = _safe_nonneg(wb_fin_dict.get("ads"))
        drr_label = f"{_round_pct(wb_ads, wb_rev):.1f}%" if wb_rev > 0 else "н/д"
        lines.append(
            f"• Выручка ЛК: <b>{_fmt_money(wb_rev)} ₽</b> | Списания ВБ.Продвижение: "
            f"<b>{_fmt_money(wb_ads)} ₽</b> (ДРР {drr_label})"
        )
    else:
        lines.append("• Финансовый отчёт WB недоступен.")
    lines.append(f"• Получено активных/учтённых РК: <b>{len(wb_campaigns)}</b>")

    if api_errors:
        lines.append("\n" + "\n".join(api_errors))

    return "\n".join(lines)


async def generate_weekly_ads_report_and_recommendations(ozon_api, wb_api) -> str:
    async def _fetch_campaigns_safe(api_obj) -> List[Dict[str, Any]]:
        if hasattr(api_obj, "fetch_active_ad_campaigns"):
            return await api_obj.fetch_active_ad_campaigns(days=7)
        return []

    async def _load_db_products() -> List[Product]:
        async with get_db_session() as session:
            res = await session.execute(select(Product))
            return list(res.scalars().all())

    (
        oz_stocks_res,
        wb_stocks_res,
        oz_fin_res,
        wb_fin_res,
        oz_camp_res,
        wb_camp_res,
        db_prods_res,
    ) = await asyncio.gather(
        ozon_api.fetch_stocks_and_prices(),
        wb_api.fetch_stocks_and_prices(),
        ozon_api.fetch_real_finance_breakdown(days=7),
        wb_api.fetch_real_finance_breakdown(days=7),
        _fetch_campaigns_safe(ozon_api),
        _fetch_campaigns_safe(wb_api),
        _load_db_products(),
        return_exceptions=True,
    )

    api_errors: List[str] = []

    oz_stocks: List[Dict[str, Any]] = []
    if isinstance(oz_stocks_res, Exception):
        api_errors.append(f"⚠️ Остатки/цены Ozon: <code>{html.escape(str(oz_stocks_res))}</code>")
    elif isinstance(oz_stocks_res, list):
        oz_stocks = oz_stocks_res

    wb_stocks: List[Dict[str, Any]] = []
    if isinstance(wb_stocks_res, Exception):
        api_errors.append(f"⚠️ Остатки/цены WB: <code>{html.escape(str(wb_stocks_res))}</code>")
    elif isinstance(wb_stocks_res, list):
        wb_stocks = wb_stocks_res

    oz_fin: Optional[Dict[str, Any]] = None
    if isinstance(oz_fin_res, Exception):
        api_errors.append(f"⚠️ Финансы Ozon: <code>{html.escape(str(oz_fin_res))}</code>")
    elif isinstance(oz_fin_res, dict):
        oz_fin = oz_fin_res

    wb_fin: Optional[Dict[str, Any]] = None
    if isinstance(wb_fin_res, Exception):
        api_errors.append(f"⚠️ Финансы WB: <code>{html.escape(str(wb_fin_res))}</code>")
    elif isinstance(wb_fin_res, dict):
        wb_fin = wb_fin_res

    oz_campaigns: Optional[List[Dict[str, Any]]] = None
    oz_campaigns_error: Optional[str] = None
    if isinstance(oz_camp_res, Exception):
        oz_campaigns_error = str(oz_camp_res)
    elif isinstance(oz_camp_res, list):
        oz_campaigns = oz_camp_res
    else:
        oz_campaigns_error = "Некорректный формат ответа Ozon Performance API"

    wb_campaigns: List[Dict[str, Any]] = []
    if isinstance(wb_camp_res, Exception):
        api_errors.append(f"⚠️ Реклама WB: <code>{html.escape(str(wb_camp_res))}</code>")
    elif isinstance(wb_camp_res, list):
        wb_campaigns = [c for c in wb_camp_res if isinstance(c, dict)]

    if isinstance(db_prods_res, Exception):
        api_errors.append(f"⚠️ База себестоимости: <code>{html.escape(str(db_prods_res))}</code>")
        db_products: List[Product] = []
    else:
        db_products = db_prods_res if isinstance(db_prods_res, list) else []

    oz_analysis = analyze_ozon_campaigns(
        raw_campaigns=oz_campaigns,
        oz_stocks=oz_stocks,
        oz_fin=oz_fin,
        db_products=db_products,
        campaigns_error=oz_campaigns_error,
    )

    factual_header = _render_factual_ads_header(
        oz_analysis=oz_analysis,
        wb_campaigns=wb_campaigns,
        wb_fin=wb_fin,
        api_errors=api_errors,
    )

    wb_summary: List[str] = []
    for item in wb_stocks:
        if not isinstance(item, dict):
            continue
        st_fbo = max(_safe_int(item.get("stock_fbo"), 0), 0)
        st_fbs = max(_safe_int(item.get("stock_fbs"), 0), 0)
        if st_fbo + st_fbs <= 0:
            continue
        raw_sku = str(item.get("sku") or "").strip()
        art = WB_SKU_TO_ART.get(raw_sku, raw_sku)
        nm_id = str(item.get("nm_id") or item.get("external_id") or "")
        cur_price = _safe_nonneg(item.get("current_price"))
        comm_pct = f"{100 * item['commission_rate']:.1f}%" if item.get('commission_rate') is not None else 'н/д'
        daily_sales = f"{item['daily_sales']:.2f}" if item.get('daily_sales') is not None else 'н/д'
        wb_summary.append(
            f"SKU {art} (nmID {nm_id}): цена {cur_price:.0f} ₽, комиссия WB={comm_pct}, "
            f"FBO={st_fbo} шт., FBS={st_fbs} шт., продаж/день={daily_sales}"
        )

    prompt_payload_ozon = {
        "campaigns_available": oz_analysis["campaigns_available"],
        "campaigns_error": oz_analysis["campaigns_error"],
        "finance_7d": oz_analysis["finance"] if oz_fin is not None else None,
        "campaigns": [
            {
                "index": idx,
                "id": c["id"],
                "title": c["title"],
                "products_count": c["products_count"],
                "skus": c["skus"],
                "spend_7d_rub": c["spend_7d_rub"],
                "promo_revenue_7d_rub": c["promo_revenue_7d_rub"],
                "drr_in_promo_pct": c["drr_in_promo_pct"],
                "drr_status": c["drr_status"],
                "items": c["items"],
            }
            for idx, c in enumerate(oz_analysis["campaigns"], 1)
        ],
        "organic_skus_not_in_ads": oz_analysis["organic_items"],
        "unclassified_skus": oz_analysis["unclassified_items"],
        "overlapping_skus": oz_analysis["overlapping_skus"],
        "zero_stock_promoted_skus": oz_analysis["zero_stock_promoted_skus"],
        "low_stock_promoted_skus": oz_analysis["low_stock_promoted_skus"],
    }

    oz_json = json.dumps(prompt_payload_ozon, ensure_ascii=False, indent=2)
    wb_json = (
        json.dumps(wb_campaigns, ensure_ascii=False, indent=2)
        if wb_campaigns
        else "Активных РК WB со списаниями за 7 дней не найдено."
    )

    tax_pct = _safe_nonneg(getattr(settings, "TAX_RATE", 0.06), 0.06) * 100.0
    acq_pct = _safe_nonneg(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015) * 100.0
    high_comm_pct = _safe_nonneg(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52) * 100.0

    prompt = (
        "Ты — финансовый аналитик и performance-директор бренда YaSoKo "
        "(моделирующие гели, базы и топы для ногтей 15 г, ИП на УСН).\n\n"
        "СТРОГИЕ ФИНАНСОВЫЕ ПРАВИЛА (ЗАПРЕЩЕНО НАРУШАТЬ ИЛИ ВЫДУМЫВАТЬ ЦИФРЫ):\n"
        f"- Налог УСН = {tax_pct:.0f}%, эквайринг = {acq_pct:.1f}%, комиссия Ozon до 300 ₽ — из API (22–23%), "
        f"свыше 300 ₽ — {high_comm_pct:.0f}% (не советовать ловушку 301–450 ₽).\n"
        "- Используй ТОЛЬКО проверенные числа и списки SKU из JSON ниже. "
        "Если campaigns_available=false или поле равно null, прямо укажи, что данные API недоступны, "
        "и НЕ выдумывай состав кампаний, ДРР или прибыль.\n"
        "- Строго разделяй каждую активную кампанию Ozon между собой и НЕ смешивай продвигаемые SKU "
        "с органическими SKU из organic_skus_not_in_ads.\n\n"
        f"1. ПРОВЕРЕННЫЙ РАЗБОР КАМПАНИЙ, ОРГАНИКИ И ФИНАНСОВ OZON:\n{oz_json}\n\n"
        f"2. РЕКЛАМНЫЕ КАМПАНИИ WILDBERRIES:\n{wb_json}\n\n"
        "3. АКТИВНЫЕ ТОВАРЫ И ОСТАТКИ WILDBERRIES (FBO и FBS):\n"
        + ("\n".join(wb_summary) or "Нет подтвержденных остатков WB.")
        + "\n\n"
        "СОСТАВЬ КРАТКИЙ ПЛАН ДЕЙСТВИЙ ПО 4 РАЗДЕЛАМ БЕЗ ВОДЫ:\n"
        "1. Раздельный аудит каждой активной кампании Ozon (состав SKU, фактический ДРР в продвижении, "
        "поштучная прибыль против безубыточного ДРР, корректировка целевой ставки и защита остатков < 2 дней).\n"
        "2. Органические SKU Ozon вне рекламы (какие артикулы оставить в органике, а какие тестировать в РК при достаточном остатке).\n"
        "3. План по ВБ.Продвижению с учетом текущих остатков FBO/FBS и фактического ДРР.\n"
        "4. Контрольные точки проверки через 24 ч, 72 ч и 7 дней.\n"
        "Форматирование: только HTML-теги <b>, <i>, <code>. Не используй Markdown (**, ###) и таблицы."
    )

    ai_block = await _call_gemini_unlimited(prompt)
    return f"{factual_header}\n\n➖➖➖➖➖➖➖➖➖➖\n🧠 <b>РЕКОМЕНДАЦИИ ПО УПРАВЛЕНИЮ РЕКЛАМОЙ:</b>\n\n{ai_block}"
