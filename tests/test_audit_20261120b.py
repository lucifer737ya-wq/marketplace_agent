"""Цены ЛК с фактическими ставками, устойчивость Gemini к обрывам прокси, WB v3 adverts."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import offline

offline.require_isolation()  # before importing application settings

import aiohttp

from services.common import gemini
from services.common import routes as routes_module
from services.common.price_calculator import (
    ozon_rates_from_finance,
    profit_at,
    solve_price,
    build_price_lines,
)

FIN = {
    "sales_total": 469290.0, "orders_count": 2789, "commission": 105787.5,
    "acquiring": 5894.86, "ads_total": 60492.72, "fbo_storage": 2432.58,
    "star_products": 7487.59, "other_fines": 2137.72, "decompensations": 769.14,
    "mutual_settlements": 10956.04, "deliv_total": 64469.98,
    "partner_dropoff": 7900.0, "partner_pack": 1140.0,
    "partner_returns": 0.0, "partner_last_mile": 0.0,
}


class PriceCalculator(unittest.TestCase):
    def test_rates_follow_real_report(self):
        rates = ozon_rates_from_finance(FIN)
        self.assertAlmostEqual(rates.commission_low, 0.2254, places=3)
        self.assertAlmostEqual(rates.fixed_per_order, (64469.98 + 7900 + 1140) / 2789, places=2)

    def test_break_even_is_zero_profit_and_tier_jump(self):
        rates = ozon_rates_from_finance(FIN)
        advice = solve_price(70.0, rates, 0.0, tiered=True)
        self.assertIsNotNone(advice)
        # цена на 1 ₽ ниже не должна быть прибыльной (в той же зоне комиссии)
        self.assertGreaterEqual(profit_at(advice.price, 70.0, rates, limit=300), -0.5)
        self.assertLess(profit_at(advice.price - 1, 70.0, rates, limit=300), 0.5)

    def test_expensive_cogs_forces_high_tier(self):
        rates = ozon_rates_from_finance(FIN)
        advice = solve_price(200.0, rates, 0.15, tiered=True)
        self.assertEqual(advice.tier, "high")
        self.assertGreater(advice.price, 300)

    def test_unreachable_price_is_none(self):
        rates = ozon_rates_from_finance(FIN)
        self.assertIsNone(solve_price(50.0, rates, 0.60, tiered=False))

    def test_report_lines_skip_missing_cost_and_survive_empty_data(self):
        products = [SimpleNamespace(marketplace="ozon", sku="1001", cost_price=70, packaging_cost=5, first_mile_unit_cost=0)]
        lines = build_price_lines(
            [{"sku": "1001", "current_price": 148}, {"sku": "1002", "current_price": 299}],
            [{"sku": "1001", "current_price": 400, "buyer_price": 320, "commission_rate": 0.24}],
            products, FIN, None,
        )
        text = "\n".join(lines)
        self.assertIn("1001", text)
        self.assertIn("Нет себестоимости", text)
        self.assertIn("ozon:1002", text)


class GeminiTransient(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_is_retried_on_new_connection(self):
        routes_module.reset_routes()
        calls = {"n": 0}

        class Resp:
            status = 200

            async def json(self):
                return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}]}}]}

            async def __aenter__(self):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise aiohttp.ServerDisconnectedError()
                return self

            async def __aexit__(self, *a):
                return False

        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def post(self, *a, **k): return Resp()

        with patch.object(gemini.aiohttp, "ClientSession", Session), \
             patch.object(gemini, "_get_available_models", AsyncMock(return_value=[])), \
             patch.object(routes_module, "_prefer_last_working", lambda service, r: list(r)), \
             patch.object(routes_module, "remember_route", lambda *a, **k: None), \
             patch.object(gemini, "mark_route_ok", lambda *a, **k: None), \
             patch.object(gemini.asyncio, "sleep", AsyncMock()):
            text, model = await gemini.generate("hi", max_tokens=64)
        self.assertEqual(text, "ok")
        self.assertEqual(calls["n"], 3)  # две обрыва + успех на той же модели и маршруте


if __name__ == "__main__":
    unittest.main()
