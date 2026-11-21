"""Тесты правок 20.11.2026 (часть 3): логистика Ozon по формуле, цены ≤300 ₽, конкуренты, ответы без артикулов."""

import unittest

from tests import offline  # noqa: F401  (окружение до импорта приложения)

from config import settings
from services.common.ozon_tariffs import ozon_logistics_cost, set_volume_l
from services.common.price_calculator import (
    Rates, build_bundle_lines, ozon_rates_from_finance, profit_at, solve_price,
)
from services.common.buyer_questions import scrub_series_numbers
from services.competitors import economics as ce


class Formula(unittest.TestCase):
    def test_user_formula_table(self):
        # F ≤ 300
        self.assertEqual(ozon_logistics_cost(0.18, 299), 25)
        self.assertEqual(ozon_logistics_cost(0.18, 300), 25)
        self.assertEqual(ozon_logistics_cost(0.36, 250), 27)
        self.assertEqual(ozon_logistics_cost(0.9, 100), 32)
        self.assertEqual(ozon_logistics_cost(15, 100), 172)
        # F > 300
        self.assertEqual(ozon_logistics_cost(0.18, 301), 97)
        self.assertEqual(ozon_logistics_cost(0.36, 360), 106)
        self.assertEqual(ozon_logistics_cost(15, 999), 200)
        self.assertEqual(ozon_logistics_cost(16, 999), 0)  # как в формуле
        self.assertEqual(set_volume_l(2, 0.18), 0.36)


class Pricing(unittest.TestCase):
    RATES = Rates(0.22, 0.52, 0.20, 10.0, "t", 0.18)

    def test_price_over_300_is_loss_making(self):
        """360 ₽ при комиссии 52% и логистике 97 ₽ — убыток, 299 ₽ — прибыль."""
        self.assertGreater(profit_at(299, 70.0, self.RATES, limit=300), 0)
        self.assertLess(profit_at(360, 70.0, self.RATES, limit=300), 0)

    def test_break_even_uses_tier_logistics(self):
        adv = solve_price(70.0, self.RATES, 0.0, tiered=True)
        self.assertEqual(adv.tier, "low")
        self.assertLessEqual(adv.price, 300)
        # цена для высокой маржи недостижима на льготном тарифе → тариф «high» и >300
        tgt = solve_price(200.0, self.RATES, 0.15, tiered=True)
        self.assertTrue(tgt is None or tgt.price > 300)

    def test_bundles_two_jars_over_300_not_profitable(self):
        lines = build_bundle_lines(70.0, self.RATES)
        self.assertTrue(any("2 шт" in x for x in lines))
        self.assertTrue(any("тариф 52%" in x for x in lines))

    def test_sold_units_ignore_zero_price_and_returns(self):
        fin = {
            "sales_total": 600.0, "orders_count": 3, "commission": 132.0,
            "deliv_direct": 50.0, "deliv_total": 50.0,
            "orders": [
                {"price": 300, "quantity": 1, "status": "sold"},
                {"price": 300, "quantity": 1, "status": "sold"},
                {"price": 0, "quantity": 1, "status": "sold"},
                {"price": -300, "quantity": 1, "status": "returned"},
            ],
        }
        rates = ozon_rates_from_finance(fin)
        self.assertIn("2 проданных", rates.source)


class Competitors(unittest.TestCase):
    def test_lk_price_and_profit(self):
        ctx = ce.OurEconomy(cogs_unit=70.0, wb_spp=0.25, wb_commission=0.2, wb_logistics=40.0,
                            ozon_commission_low=0.23, ozon_discount=0.0)
        self.assertAlmostEqual(ce.lk_price("WB", 300, ctx), 400.0)
        self.assertAlmostEqual(ce.lk_price("OZON", 300, ctx), 300.0)
        # WB: 400*(1-0.2-0.015) - 70 - 40 - 300*0.06 = 186
        self.assertAlmostEqual(ce.net_profit("WB", 400.0, 300.0, ctx), 186.0, places=1)
        # Ozon ≤300 дешевле, чем 360 (52% + логистика 97)
        low = ce.net_profit("OZON", 299.0, 299.0, ctx)
        high = ce.net_profit("OZON", 360.0, 360.0, ctx)
        self.assertGreater(low, 0)
        self.assertLess(high, low)
        no_cost = ce.OurEconomy(None, 0.25, 0.2, 40.0, 0.23, 0.0)
        self.assertIsNone(ce.net_profit("WB", 400.0, 300.0, no_cost))

    def test_spp_from_items(self):
        spp = ce.spp_from_items([{"current_price": 400, "buyer_price": 300}], 0.27)
        self.assertAlmostEqual(spp, 0.25)
        self.assertEqual(ce.spp_from_items([], 0.27), 0.27)


class Replies(unittest.TestCase):
    def test_series_range_and_article_removed(self):
        text = ("Гель под артикулом 1010 относится к классической моделирующей серии (1001–1036) "
                "и средне-вязкий. Все гели 1001-1408 удобны.")
        out = scrub_series_numbers(text)
        for bad in ("1001", "1036", "1010", "1408"):
            self.assertNotIn(bad, out)
        self.assertIn("классической моделирующей серии", out)


if __name__ == "__main__":
    unittest.main()


class AiGuard(unittest.TestCase):
    class P:
        marketplace = "ozon"
        cost_price = 60.0
        packaging_cost = 5.0
        first_mile_unit_cost = 5.0

    def test_guard_block_mentions_cap_and_52(self):
        from services.common.price_calculator import build_ozon_guard
        text = build_ozon_guard([self.P()], None)
        self.assertIn("299 ₽", text)
        self.assertIn("52%", text)
        self.assertIn("97", text)

    def test_ai_price_over_threshold_is_flagged(self):
        from services.common.price_calculator import check_ai_ozon_prices
        ai = "1. Поднять цену арт. 1031 на Ozon с 299 ₽ до 349 ₽. Бюджет 500 ₽/день.\nВыручка 42,470 ₽"
        out = check_ai_ozon_prices(ai, [self.P()], None)
        self.assertIn("ПРОВЕРКА КОДОМ", out)
        self.assertIn("349 ₽", out)
        self.assertNotIn("• 500", out)
        clean = "Оставить цену Ozon 299 ₽."
        self.assertEqual(check_ai_ozon_prices(clean, [self.P()], None), clean)


class Sanitizer(unittest.TestCase):
    def test_foreign_scripts_removed(self):
        from services.common.ai_advisory_board import strip_foreign_scripts
        self.assertEqual(strip_foreign_scripts("всего 4 ফ на FBO"), "всего 4  на FBO")
