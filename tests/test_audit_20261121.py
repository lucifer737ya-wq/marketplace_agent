"""Регрессии аудита 21.11.2026: себестоимость, налог с цены покупателя, ROI 30–100%, конкуренты, неполные данные."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from tests import offline  # noqa: F401  (изолированное окружение до импорта приложения)

from config import settings
from database.crud import upsert_product_costs
from database.models import close_db, init_db
from services.common import cogs
from services.common.price_auditor import find_optimal_wb_price, run_daily_price_audit
from services.common.price_calculator import Rates, build_bundle_lines
from services.common.pricing_policy import recommend_price
from services.competitors.economics import net_profit, spp_from_items, OurEconomy


class CogsRules(unittest.TestCase):
    def test_purchase_plus_packaging_plus_labor(self):
        with patch.object(settings, "PACKAGING_COST_PER_UNIT", 9.0), patch.object(settings, "LABOR_COST_PER_UNIT", 5.0):
            p = NS(cost_price=60.0, packaging_cost=0.0, first_mile_unit_cost=0.0)
            self.assertEqual(cogs.unit_cogs_of(p), 74.0)
            self.assertFalse(cogs.purchase_out_of_range(p))
            self.assertTrue(cogs.purchase_out_of_range(NS(cost_price=70.0, packaging_cost=0, first_mile_unit_cost=0)))
            self.assertEqual(cogs.unit_cogs_of(NS(cost_price=0, packaging_cost=9, first_mile_unit_cost=0)), 0.0)

    def test_ozon_tax_base_is_buyer_price(self):
        with patch.object(settings, "COMPETITOR_OZON_DISCOUNT_RATE", 0.26), patch.object(settings, "OZON_TAX_ON_BUYER_PRICE", True):
            self.assertAlmostEqual(cogs.ozon_tax_base_ratio(), 0.74)


class PolicyRules(unittest.TestCase):
    pf = staticmethod(lambda p: p * 0.5 - 50.0)  # прибыль растёт с ценой; cogs=75

    def test_never_above_competitors_and_roi_corridor(self):
        r = recommend_price(self.pf, 75.0, lo=100, hi=3000, competitor_lk=650)
        self.assertLessEqual(r.price, 650)
        self.assertGreaterEqual(r.roi, 0.30)
        self.assertLessEqual(r.roi, 1.00 + 0.02)
        self.assertEqual(r.status, "ok")

    def test_cap_at_100_percent_roi(self):
        r = recommend_price(self.pf, 75.0, lo=100, hi=3000, competitor_lk=5000)
        self.assertLessEqual(r.roi, 1.02)

    def test_competitors_below_break_even_never_loss(self):
        r = recommend_price(self.pf, 75.0, lo=100, hi=3000, competitor_lk=90)
        self.assertEqual(r.status, "loss_risk")
        self.assertGreater(r.profit, 0)

    def test_no_competitors_uses_min_roi(self):
        r = recommend_price(self.pf, 75.0, lo=100, hi=3000)
        self.assertEqual(r.status, "no_competitors")
        self.assertGreaterEqual(r.roi, 0.30)


class SppAndBundles(unittest.TestCase):
    def test_items_without_orders_do_not_zero_spp(self):
        items = [{"current_price": 400, "buyer_price": 400}] * 10 + [{"current_price": 400, "buyer_price": 290}]
        self.assertAlmostEqual(spp_from_items(items, 0.27), 1 - 290 / 400)
        self.assertEqual(spp_from_items([{"current_price": 400, "buyer_price": 400}], 0.27), 0.27)

    def test_ozon_tax_from_showcase_price(self):
        ctx = OurEconomy(cogs_unit=74.0, wb_spp=0.27, wb_commission=0.42, wb_logistics=50, ozon_commission_low=0.23, ozon_discount=0.26)
        lk = 296.0
        showcase = round(lk * 0.74, 2)
        with patch.object(settings, "OZON_TAX_ON_BUYER_PRICE", True):
            on_buyer = net_profit("OZON", lk, showcase, ctx)
        with patch.object(settings, "OZON_TAX_ON_BUYER_PRICE", False):
            on_lk = net_profit("OZON", lk, showcase, ctx)
        self.assertGreater(on_buyer, on_lk)

    def test_bundle_ads_are_cost_per_order_not_percent(self):
        rates = Rates(0.22, 0.52, 0.30, 20.0, "t", 0.18, ads_pct=0.13, ads_per_order=36.0)
        text = "\n".join(build_bundle_lines(148.0, rates))
        self.assertIn("ROI 30%", text)
        self.assertIn("ROI 100%", text)


class AuditFallbacks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.addAsyncCleanup(close_db)

    async def test_wb_policy_respects_competitors_and_buyer_tax(self):
        rich = find_optimal_wb_price(380, 74, 0.42, 10, 0, 78, 0, 5, 0.0, spp_rate=0.27)
        capped = find_optimal_wb_price(380, 74, 0.42, 10, 0, 78, 0, 5, 0.0, spp_rate=0.27, competitor_buyer_price=420)
        self.assertLessEqual(capped["recommended_price"] * 0.73, 420)
        self.assertGreaterEqual(rich["roi_pct"], 30)
        self.assertLessEqual(capped["roi_pct"], 101)
        self.assertGreater(capped["net_profit"], 0)

    async def test_incomplete_data_still_gives_cards_with_buttons(self):
        await upsert_product_costs("1031", "ozon", 60, 0)
        await upsert_product_costs("1031", "wb", 60, 0)
        bot, oz, wb = AsyncMock(), AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {"orders_count": 10, "deliv_total": 360, "sales_total": 3000, "ads_total": 300}
        # кампания «оплата за заказ»: состав известен, ДРР в промо нет -> раньше SKU пропускался
        oz.fetch_active_ad_campaigns.return_value = []
        item = {"sku": "1031", "current_price": 299, "stock_fbs": 5, "commission_rate": 0.23, "daily_sales": 1, "product_id": 33}
        oz.fetch_stocks_and_prices.return_value = [item]  # fbs_pvz_dropoff_rub отсутствует
        wb.fetch_real_finance_breakdown.return_value = {"orders_count": 1}
        wb.fetch_stocks_and_prices.return_value = [
            {"sku": "1031", "nm_id": 99, "current_price": 353, "stock_fbs": 3, "daily_sales": None}  # нет комиссии и логистики
        ]
        await run_daily_price_audit(bot, settings.TG_ADMIN_ID, oz, wb)
        texts = [c.args[1] if len(c.args) > 1 else "" for c in bot.send_message.await_args_list]
        self.assertFalse(any("рекомендация пропущена" in t or "нет кнопок" in t for t in texts), texts)
        kb = [c.kwargs.get("reply_markup") for c in bot.send_message.await_args_list if c.kwargs.get("reply_markup")]
        self.assertEqual(len(kb), 2)
        self.assertEqual(len(kb[0].inline_keyboard), 3)  # до 300 / выше 300 / отмена


if __name__ == "__main__":
    unittest.main()
