"""Проверка и массовая правка закупки (себестоимость = закупка 58–66 ₽ + упаковка 9 ₽ + зарплата 5 ₽).

    python fix_costs.py                       — показать все SKU и разбивку себестоимости
    python fix_costs.py --default 62          — SKU с закупкой вне 58–66 (старое «70») получат 62 ₽
    python fix_costs.py --set 1001=58 1002=66 — задать закупку по артикулам (оба маркетплейса)
    python fix_costs.py --zero-packaging      — обнулить упаковку в БД (9 ₽ и 5 ₽ берутся из .env централизованно)
"""
import argparse
import asyncio

from config import prepare_runtime_dirs, settings
from database.crud import get_all_products, upsert_product_costs
from database.models import close_db, init_db
from services.common.cogs import cogs_parts, purchase_out_of_range, unit_cogs_of


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--default", type=float, help="закупка для SKU вне диапазона")
    ap.add_argument("--set", nargs="*", default=[], metavar="SKU=ЦЕНА")
    ap.add_argument("--zero-packaging", action="store_true")
    args = ap.parse_args()
    prepare_runtime_dirs()
    await init_db()
    try:
        manual = {k.strip(): float(v.replace(",", ".")) for k, v in (x.split("=", 1) for x in args.set)}
        for prod in await get_all_products():
            new_cost, new_pack = float(prod.cost_price or 0), float(prod.packaging_cost or 0)
            if prod.sku in manual:
                new_cost = manual[prod.sku]
            elif args.default and (purchase_out_of_range(prod) or new_cost <= 0):
                new_cost = args.default
            if args.zero_packaging and settings.PACKAGING_COST_PER_UNIT > 0:
                new_pack = 0.0
            if (new_cost, new_pack) != (float(prod.cost_price or 0), float(prod.packaging_cost or 0)):
                await upsert_product_costs(prod.sku, prod.marketplace, new_cost, new_pack)
        print(f"Упаковка {settings.PACKAGING_COST_PER_UNIT:g} ₽ + зарплата {settings.LABOR_COST_PER_UNIT:g} ₽ (из .env). "
              f"Допустимая закупка {settings.PURCHASE_COST_MIN:g}–{settings.PURCHASE_COST_MAX:g} ₽.")
        for prod in sorted(await get_all_products(), key=lambda p: (p.marketplace, p.sku)):
            parts = cogs_parts(prod)
            flag = "  ⚠ закупка вне 58–66" if purchase_out_of_range(prod) else ("  ⚠ закупка не задана" if parts["purchase"] <= 0 else "")
            print(f"{prod.marketplace:5} {prod.sku:8} закупка {parts['purchase']:6.2f} + упак. {parts['packaging']:.0f} + ЗП {parts['labor']:.0f}"
                  f" + 1-я миля {parts['first_mile']:.2f} = {unit_cogs_of(prod):7.2f} ₽{flag}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
