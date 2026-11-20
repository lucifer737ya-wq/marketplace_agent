"""Keep two independent Ozon ledgers. Never add the same fee from both APIs."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


def _parse_decimal(value) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, Decimal):
        return value if value.is_finite() else Decimal(0)
    try:
        parsed = Decimal(
            str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        )
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return parsed if parsed.is_finite() else Decimal(0)


def money(value) -> Decimal:
    if isinstance(value, dict):
        return _parse_decimal(value.get("amount"))
    return _parse_decimal(value)


def amount(node) -> float:
    """Ozon отдаёт деньги объектом {'amount': '123.45', 'currency': 'RUB'}."""
    return float(money(node))


# type_id -> ключ книги (тот же словарь, что у cash-flow/транзакций).
# Справочник Ozon /v1/finance/accrual/types содержит 100+ типов, поэтому
# незнакомые значения разбираются по имени типа, а не теряются.
ACCRUAL_TYPES = {
    32: "deliv_direct",      # Logistic
    29: "deliv_direct",      # LastMileCourier
    98: "deliv_ozon_handover",  # DeliveryToHandoverPlaceByOzon
    59: "deliv_return",      # ReturnFlowLogistic
    45: "deliv_return",      # PickUpPointReturnAcceptance
    6: "deliv_return",       # Cancellation
    41: "promo_cpc",         # PayPerClick
    54: "promo_cpo",         # Promotion
    77: "partner_pack",      # SupplyInbound
    84: "partner_pack",      # ItemPacking
    39: "partner_pack",      # PackingFee
    38: "partner_pack",      # PackageCost
    15: "fbo_storage",       # Disposal
    46: "fbo_storage",       # Placements
    78: "fbo_storage",       # TemporaryPlacement
    96: "other_fines",       # AcceleratedReviewCollection
    48: "other_fines",       # PremiumCashbackIndividualPoints
    12: "partner_dropoff",   # CrossDock
    1: "acquiring",          # Acquiring
    10: "decompensations",   # Compensation
    25: "decompensations",   # ItemCompensation
}

# Более специфичные маркеры (возвраты, передача, drop-off) обязаны идти раньше
# общих «logistic» / «delivery», чтобы ReturnLogistic не попадал в прямую доставку.
_TYPE_HINTS = (
    ("acquiring", "acquiring"),
    ("payperclick", "promo_cpc"),
    ("perclick", "promo_cpc"),
    ("promotion", "promo_cpo"),
    ("return", "deliv_return"),
    ("cancel", "deliv_return"),
    ("handover", "deliv_ozon_handover"),
    ("crossdock", "partner_dropoff"),
    ("dropoff", "partner_dropoff"),
    ("logistic", "deliv_direct"),
    ("lastmile", "deliv_direct"),
    ("delivery", "deliv_direct"),
    ("storage", "fbo_storage"),
    ("placement", "fbo_storage"),
    ("pack", "partner_pack"),
    ("supply", "partner_pack"),
    ("compensation", "decompensations"),
)


def accrual_category(types: dict, type_id, unknown: set) -> str:
    """type_id -> категория книги. Незнакомый тип -> other_fines + сбор в unknown."""
    try:
        key_id = int(type_id)
    except (TypeError, ValueError):
        key_id = None
    if key_id in ACCRUAL_TYPES:
        return ACCRUAL_TYPES[key_id]
    name = str((types or {}).get(key_id, "") or "").lower()
    for needle, bucket in _TYPE_HINTS:
        if needle in name:
            return bucket
    if key_id is not None:
        unknown.add(key_id)
    return "other_fines"


def walk_fees(node, unknown: set):
    """Собрать пары (type_id, Decimal) на любой глубине дерева начислений.

    Новый API раскладывает услуги по разным ветвям (posting.products[].delivery.
    services, item_fees.fees[].fees[], non_item_fee). Рекурсия переживает смену
    формы ответа, чего не скажешь о жёстко прописанных путях.
    """
    found: list[tuple] = []

    containers = ("posting", "item_fees", "non_item_fee", "accrual_id")

    def visit(current):
        if isinstance(current, dict):
            # У контейнера начисления тоже бывает type_id (старая схема): это
            # не услуга, а сама продажа — иначе сумма задвоится.
            if any(key in current for key in containers):
                for value in current.values():
                    visit(value)
                return
            if "type_id" in current and isinstance(current.get("accrued"), dict):
                found.append((current.get("type_id"), _parse_decimal(current["accrued"].get("amount"))))
                return
            if "type_id" in current and current.get("accrued") is not None and not isinstance(
                current.get("accrued"), (dict, list)
            ):
                found.append((current.get("type_id"), _parse_decimal(current.get("accrued"))))
                return
            for value in current.values():
                visit(value)
        elif isinstance(current, list):
            for value in current:
                visit(value)

    visit(node)
    return found


def category(name: str, default: str) -> str:
    clean_name = str(name or "")
    categories = {
        "CostPerClick": "promo_cpc",
        "ElectronicServiceStencil": "promo_cpc",
        "ElectronicServicesPromotionInSearch": "promo_cpo",
        "OperationMarketplaceServiceStorage": "fbo_storage",
        "ItemDropoff": "partner_dropoff",
        "ReturnAfterDeliv": "deliv_return",
        "ReturnNotDeliv": "deliv_return",
        "ReturnPartGoods": "deliv_return",
        "ItemDelivToCustomer": "deliv_direct",
        "DCFlowTrans": "deliv_direct",
        "ItemFulfillment": "deliv_direct",
        "PromotionWithCostPerOrder": "promo_cpo",
        "Acquiring": "acquiring",
        "RedistributionDropoff": "partner_dropoff",
        "DeliveryToHandoverPlace": "deliv_ozon_handover",
        "RedistributionLastMile": "partner_last_mile",
        "RedistributionReturns": "partner_returns",
        "SellerReturnsCargo": "partner_returns",
        "AdditionalPackaging": "partner_pack",
        "ProductMovementFromWarehouse": "partner_pack",
        "StarsMembership": "star_products",
        "StorageItem": "fbo_storage",
        "ReturnFlow": "deliv_return",
        "DirectFlow": "deliv_direct",
        "Decompensation": "decompensations",
        "SetOffBalance": "mutual_settlements",
    }
    return next((v for k, v in categories.items() if k in clean_name), default)


def book_transaction(book, op) -> None:
    if not isinstance(op, dict):
        return
    sales = money(op.get("accruals_for_sale"))
    commission = -money(op.get("sale_commission"))
    book["sales_total"] += sales
    book["commission"] += commission
    expenses = sales - commission - money(op.get("amount"))
    # Legacy delivery_charge and modern itemized delivery represent the same service.
    services = [s for s in (op.get("services") or []) if isinstance(s, dict)]
    for s in services:
        cost = -money(s.get("price"))
        book[category(str(s.get("name") or ""), "other_fines")] += cost
        expenses -= cost
    if not services:
        for field, key in (
            ("delivery_charge", "deliv_direct"),
            ("return_delivery_charge", "deliv_return"),
        ):
            cost = -money(op.get(field))
            book[key] += cost
            expenses -= cost
    book[category(str(op.get("operation_type") or ""), "other_fines")] += expenses


def complete_statement(rows, start: date, end: date) -> bool:
    """Reporting periods must cover the requested window exactly, without gaps/overlap."""
    intervals = []
    for r in rows or []:
        if not isinstance(r, dict):
            return False
        period = r.get("period") or {}
        try:
            intervals.append(
                (
                    date.fromisoformat(str(period["begin"])[:10]),
                    date.fromisoformat(str(period["end"])[:10]),
                )
            )
        except (ValueError, KeyError, TypeError):
            return False
    cursor = start.toordinal()
    for first, last in sorted(intervals):
        if first.toordinal() != cursor or last < first:
            return False
        cursor = last.toordinal() + 1
    return bool(intervals) and cursor == end.toordinal() + 1


def book_statement(flows, details):
    book = defaultdict(Decimal)
    for cf in flows or []:
        if not isinstance(cf, dict):
            continue
        book["sales_total"] += money(cf.get("orders_amount")) + money(cf.get("returns_amount"))
        book["commission"] -= money(cf.get("commission_amount"))
    for det in details or []:
        if not isinstance(det, dict):
            continue
        groups = [
            (
                (det.get("delivery") or {}).get("delivery_services") or {},
                "deliv_direct",
            ),
            ((det.get("return") or {}).get("return_services") or {}, "deliv_return"),
            (det.get("services") or {}, "other_fines"),
            (det.get("others") or {}, "decompensations"),
        ]
        for group, default in groups:
            if not isinstance(group, dict):
                continue
            items = [i for i in (group.get("items") or []) if isinstance(i, dict)]
            if not items and group.get("total"):
                book[default] -= money(group.get("total"))
            for item in items:
                name = str(item.get("name") or "")
                if any(
                    code in name
                    for code in (
                        "AgencyFeeForSale",
                        "PointsAwarded",
                        "CostOperationAggregate",
                    )
                ):
                    continue  # settlement / commission already present in the summary
                book[category(name, default)] -= money(item.get("price"))
    return book


def totals(book) -> dict:
    def subtotal(*keys):
        return sum((book.get(k, Decimal(0)) for k in keys), Decimal(0))

    acquiring_reported = "acquiring" in book
    sales_total = book.get("sales_total", Decimal(0))
    delivery = subtotal("deliv_direct", "deliv_ozon_handover", "deliv_return")
    partner = subtotal(
        "partner_dropoff",
        "partner_pack",
        "partner_returns",
        "acquiring",
        "star_products",
        "partner_last_mile",
    )
    ads = subtotal("promo_cpc", "promo_cpo")
    deductions = (
        delivery
        + partner
        + ads
        + subtotal(
            "commission",
            "fbo_storage",
            "other_fines",
            "decompensations",
            "mutual_settlements",
        )
    )
    merged = {
        **dict(book),
        "sales_total": sales_total,
        "deliv_total": delivery,
        "partners_total": partner,
        "ads_total": ads,
        "total_deductions": deductions,
        "total_payout": sales_total - deductions,
    }
    return {
        k: float(round(v, 2)) for k, v in merged.items()
    } | {"acquiring_reported": acquiring_reported}