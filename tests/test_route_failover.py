"""Failover contract: every module must walk all available routes.

Regression cover for the 18.11.2026 incident: the only route was a local proxy,
so a single ``ServerDisconnectedError`` took down Gemini, Ozon and WB at once.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from tests import offline

offline.require_isolation()  # before importing application settings

import services.common.gemini as gemini
from config import Settings
from services.common import routes
from services.common.market_api import (
    APIStatusError,
    CircuitBreakerOpenError,
    MarketplaceClient,
)
from services.ozon.finance import accrual_category, walk_fees


class RouteList(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def test_direct_route_is_available_by_default(self):
        with patch.object(Settings, "model_config", {"env_file": None, "extra": "ignore"}):
            fresh = Settings(
                TG_BOT_TOKEN="1:a",
                TG_ADMIN_ID=1,
                OZON_CLIENT_ID="c",
                OZON_API_KEY="k",
                WB_API_TOKEN="w",
                GEMINI_API_KEY="g",
            )
        self.assertTrue(fresh.ALLOW_DIRECT_CONNECTIONS)
        self.assertEqual(fresh.LOCAL_PROXY_URL, "http://127.0.0.1:3066")

    def test_all_routes_are_listed_direct_first(self):
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            listed = routes.request_routes("service-without-history")
        self.assertEqual(listed[0], routes.DIRECT)
        self.assertIn("http://127.0.0.1:3066", listed)

    def test_non_loopback_proxy_is_rejected(self):
        with patch.object(
            routes.settings, "LOCAL_PROXY_LIST", "http://203.0.113.7:8080,"
        ):
            listed = routes.request_routes("gemini")
        self.assertNotIn("http://203.0.113.7:8080", listed)

    def test_failed_route_goes_to_quarantine_and_is_skipped(self):
        routes.mark_route_failed("gemini", routes.DIRECT, "test")
        self.assertFalse(routes.route_available("gemini", routes.DIRECT))
        routes.reset_routes()
        self.assertTrue(routes.route_available("gemini", routes.DIRECT))


class GeminiFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def tearDown(self):
        routes.reset_routes()

    async def test_generate_moves_to_next_route_after_proxy_drop(self):
        """ServerDisconnectedError на первом маршруте не убивает модуль."""
        calls = []

        class FakeSession:
            closed = False

            def __init__(self, *args, **kwargs):
                self._route = "?"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                self.closed = True
                return False

            def get(self, url, **kwargs):
                calls.append(("get", kwargs.get("proxy")))
                raise aiohttp.ServerDisconnectedError()

            def post(self, url, **kwargs):
                calls.append(("post", kwargs.get("proxy")))
                proxy = kwargs.get("proxy")

                class Response:
                    status = 200

                    async def __aenter__(self_inner):
                        if proxy is None:  # direct route is "down" in this test
                            raise aiohttp.ServerDisconnectedError()
                        return self_inner

                    async def __aexit__(self_inner, *exc):
                        return False

                    async def json(self_inner):
                        return {
                            "candidates": [
                                {
                                    "finishReason": "STOP",
                                    "content": {"parts": [{"text": "ответ"}]},
                                }
                            ]
                        }

                return Response()

        with (
            patch.object(gemini.aiohttp, "ClientSession", FakeSession),
            patch.object(gemini.settings, "LOCAL_PROXY_LIST", ""),
            patch.object(gemini.settings, "LOCAL_PROXY_FALLBACK_URL", ""),
            patch.object(gemini, "FALLBACK_MODELS", ["gemini-3.8-flash"]),
        ):
            text, model = await gemini.generate("привет", max_tokens=64)

        self.assertEqual(text, "ответ")
        self.assertEqual(model, "gemini-3.8-flash")
        # Первый маршрут (direct) упал, второй (локальный прокси) ответил.
        states = {r["route"]: r["state"] for r in routes.route_diagnostics("gemini")}
        self.assertEqual(states[routes.DIRECT], "карантин")
        self.assertTrue(any(proxy for kind, proxy in calls if kind == "post"))

    async def test_all_routes_down_raises_compact_error(self):
        class DeadSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def get(self, *a, **k):
                raise aiohttp.ServerDisconnectedError()

            def post(self, *a, **k):
                raise aiohttp.ServerDisconnectedError()

        with (
            patch.object(gemini.aiohttp, "ClientSession", lambda *a, **k: DeadSession()),
            patch.object(gemini.settings, "LOCAL_PROXY_LIST", ""),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await gemini.generate("x", max_tokens=32)
        self.assertIn("ни по одному маршруту", str(ctx.exception))
        self.assertNotIn(gemini.settings.GEMINI_API_KEY, str(ctx.exception))


class MarketplaceFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def tearDown(self):
        routes.reset_routes()

    async def test_request_json_walks_routes_and_reports_route_status(self):
        client = MarketplaceClient("Ozon", "acc")
        order = []

        async def once(method, url, headers, body, params, route):
            order.append(route)
            if route == routes.DIRECT:
                raise aiohttp.ClientResponseError(
                    MagicMock(), (), status=403, message="antibot"
                )
            return {"ok": True}

        client._request_once = once
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            result = await client.request_json("GET", "https://api-seller.ozon.ru/x", {})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(order[0], routes.DIRECT)
        self.assertEqual(len(order), 2)

    async def test_api_verdict_is_not_a_route_failure(self):
        client = MarketplaceClient("WB", "acc")
        client._request_once = AsyncMock(
            side_effect=APIStatusError(404, "/api/v5/supplier/reportDetailByPeriod")
        )
        with self.assertRaises(APIStatusError):
            await client.request_json("GET", "https://x/y", {})

    async def test_all_routes_failed_raises_circuit_breaker(self):
        client = MarketplaceClient("WB", "acc")

        async def dead(*args):
            raise aiohttp.ServerDisconnectedError()

        client._request_once = dead
        with patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            with self.assertRaises(CircuitBreakerOpenError) as ctx:
                await client.request_json("GET", "https://x/y", {})
        self.assertFalse(client.is_degraded)
        self.assertIn("все маршруты", str(ctx.exception))


class AccrualParsing(unittest.TestCase):
    def test_fees_are_found_at_any_depth(self):
        unknown = set()
        node = {
            "services": [{"type_id": 32, "accrued": {"amount": "-39"}}],
            "fees": [
                {
                    "sku": 1,
                    "fees": [{"type_id": 41, "accrued": {"amount": "-6"}}],
                }
            ],
            "non_item_fee": {"type_id": 1, "accrued": {"amount": "-2.1"}},
        }
        found = walk_fees(node, unknown)
        self.assertEqual({tid for tid, _ in found}, {1, 32, 41})
        self.assertEqual(unknown, set())

    def test_unknown_type_keeps_money_and_is_reported(self):
        unknown = set()
        self.assertEqual(accrual_category({}, 999, unknown), "other_fines")
        self.assertEqual(unknown, {999})
        # Незнакомый тип с понятным названием разбирается по имени.
        self.assertEqual(accrual_category({5: "Acquiring"}, 5, unknown), "acquiring")


if __name__ == "__main__":
    unittest.main()
