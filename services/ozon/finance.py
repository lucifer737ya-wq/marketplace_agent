"""Keep two independent Ozon ledgers. Never add the same fee from both APIs."""

from collections import defaultdict
from datetime import date
from decimal import Decimal


def money(value) -> Decimal:
    return Decimal(str(value or 0))


def category(name: str, default: str) -> str:
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
    return next((v for k, v in categories.items() if k in name), default)


def book_transaction(book, op) -> None:
    sales = money(op.get("accruals_for_sale"))
    commission = -money(op.get("sale_commission"))
    book["sales_total"] += sales
    book["commission"] += commission
    expenses = sales - commission - money(op["amount"])
    # Legacy delivery_charge and modern itemized delivery represent the same service.
    services = op.get("services") or []
    for s in services:
        cost = -money(s["price"])
        book[category(s["name"], "other_fines")] += cost
        expenses -= cost
    if not services:
        for field, key in (
            ("delivery_charge", "deliv_direct"),
            ("return_delivery_charge", "deliv_return"),
        ):
            cost = -money(op.get(field))
            book[key] += cost
            expenses -= cost
    book[category(op["operation_type"], "other_fines")] += expenses


def complete_statement(rows, start: date, end: date) -> bool:
    """Reporting periods must cover the requested window exactly, without gaps/overlap."""
    intervals = []
    for r in rows:
        period = r.get("period") or {}
        try:
            intervals.append(
                (
                    date.fromisoformat(period["begin"][:10]),
                    date.fromisoformat(period["end"][:10]),
                )
            )
        except (ValueError, KeyError, TypeError):
            return False
    cursor = start.toordinal()
    for first, last in sorted(intervals):
        if first.toordinal() != cursor or last < first:
            return False
        cursor = last.toordinal() + 1
    return bool(rows) and cursor == end.toordinal() + 1


def book_statement(flows, details):
    book = defaultdict(Decimal)
    for cf in flows:
        book["sales_total"] += money(cf["orders_amount"]) + money(cf["returns_amount"])
        book["commission"] -= money(cf["commission_amount"])
    for det in details:
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
            items = group.get("items") or []
            if not items and group.get("total"):
                book[default] -= money(group["total"])
            for item in items:
                name = item["name"]
                if any(
                    code in name
                    for code in (
                        "AgencyFeeForSale",
                        "PointsAwarded",
                        "CostOperationAggregate",
                    )
                ):
                    continue  # settlement / commission already present in the summary
                book[category(name, default)] -= money(item["price"])
    return book


def totals(book) -> dict:
    def subtotal(*keys):
        return sum((book[k] for k in keys), Decimal(0))

    acquiring_reported = "acquiring" in book
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
    return {
        k: float(round(v, 2))
        for k, v in dict(
            book,
            deliv_total=delivery,
            partners_total=partner,
            ads_total=ads,
            total_deductions=deductions,
            total_payout=book["sales_total"] - deductions,
        ).items()
    } | {"acquiring_reported": acquiring_reported}
