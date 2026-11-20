"""Регрессии по аудиту 20.11.2026: Gemini, 429, фильтр советов, маршруты."""

import time
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from services.common import buyer_questions, gemini, market_api, routes
from services.common.buyer_questions import has_unsafe_advice


class UnsafeAdviceFilter(unittest.TestCase):
    def test_negated_and_lamp_sentences_are_allowed(self):
        self.assertFalse(has_unsafe_advice("Не разбавляйте гель спиртом, он уже готов к работе."))
        self.assertFalse(has_unsafe_advice("В лампе гель может нагреваться — это нормальная реакция."))
        self.assertFalse(has_unsafe_advice("Гель средне-вязкий и самовыравнивается за 3–5 секунд."))

    def test_real_advice_is_blocked(self):
        self.assertTrue(has_unsafe_advice("Добавьте спирт."))
        self.assertTrue(has_unsafe_advice("Подогрейте банку перед работой."))
        self.assertTrue(has_unsafe_advice("Разбавьте ацетоном."))


class QuestionRetry(unittest.IsolatedAsyncioTestCase):
    async def test_negated_mention_is_published_without_retry(self):
        reply = "Здравствуйте! Гель средне-вязкий; не разбавляйте его спиртом."
        with patch.object(buyer_questions, "call_fast_gemini", AsyncMock(return_value=reply)) as mock:
            self.assertEqual(await buyer_questions.generate_buyer_question_answer("Густой?", "1010"), reply)
        self.assertEqual(mock.await_count, 1)

    async def test_unsafe_then_safe_reply_is_retried_with_strict_prompt(self):
        mock = AsyncMock(side_effect=["Добавьте спирт", "Здравствуйте! Гель средне-вязкий."])
        with patch.object(buyer_questions, "call_fast_gemini", mock):
            text = await buyer_questions.generate_buyer_question_answer("Густой?", "1010")
        self.assertIn("средне-вязкий", text)
        self.assertIn("ВАЖНО", mock.await_args_list[1].args[0])

    async def test_review_reply_gets_greeting_and_closing(self):
        with patch.object(buyer_questions, "call_fast_gemini", AsyncMock(return_value="Спасибо за отзыв.")):
            text = await buyer_questions.generate_review_reply("Густой", 3, "1010")
        self.assertTrue(text.startswith("Здравствуйте!"))
        self.assertIn("Желаем красивого маникюра", text)

    async def test_gemini_outage_is_not_retried(self):
        mock = AsyncMock(side_effect=gemini.GeminiUnavailable("down"))
        with patch.object(buyer_questions, "call_fast_gemini", mock):
            with self.assertRaises(gemini.GeminiUnavailable):
                await buyer_questions.generate_review_reply("x", 5, "1001")
        self.assertEqual(mock.await_count, 1)


class RouteDefaultsAndCooldown(unittest.TestCase):
    def setUp(self):
        routes.reset_routes()
        # Не записываем «последний рабочий маршрут» в общий кэш тестов.
        patcher = patch.object(routes, "remember_route")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gemini_and_telegram_proxy_only_marketplaces_direct(self):
        proxy = "http://127.0.0.1:3066"
        with patch.object(routes.settings, "GEMINI_PROXY_ONLY", True), \
             patch.object(routes.settings, "TELEGRAM_PROXY_ONLY", True), \
             patch.object(routes.settings, "MARKETPLACES_DIRECT_ONLY", True), \
             patch.object(routes.settings, "LOCAL_PROXY_URL", proxy), \
             patch.object(routes.settings, "LOCAL_PROXY_FALLBACK_URL", ""), \
             patch.object(routes.settings, "LOCAL_PROXY_LIST", ""):
            self.assertEqual(routes.request_routes("gemini"), [proxy])
            self.assertEqual(routes.request_routes("telegram"), [proxy])
            self.assertEqual(routes.request_routes("ozon"), [routes.DIRECT])
            self.assertEqual(routes.request_routes("wb"), [routes.DIRECT])

    def test_cooldown_is_tracked_per_route(self):
        proxy = "http://127.0.0.1:3066"
        routes.mark_route_failed("gemini", routes.DIRECT, "geo")
        routes.mark_route_failed("gemini", proxy, "503")
        self.assertFalse(routes.route_available("gemini", routes.DIRECT))
        self.assertFalse(routes.route_available("gemini", proxy))
        routes.mark_route_ok("gemini", proxy)
        self.assertTrue(routes.route_available("gemini", proxy))
        self.assertFalse(routes.route_available("gemini", routes.DIRECT))


class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, **kw):
        return self._payload


def _session_factory(responses, seen):
    class Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            raise aiohttp.ServerDisconnectedError()

        def post(self, url, **kw):
            seen.append((url.rsplit("/", 1)[-1], kw.get("proxy"), kw["json"]["generationConfig"]["maxOutputTokens"]))
            return responses.pop(0)

    return Session


OK = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ответ"}]}}]}
CUT = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}


class GeminiModelFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()
        patcher = patch.object(routes, "remember_route")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _run(self, responses, seen, fast=True):
        proxy = "http://127.0.0.1:3066"
        with patch.object(gemini.aiohttp, "ClientSession", _session_factory(responses, seen)), \
             patch.object(gemini, "request_routes", return_value=[proxy]), \
             patch.object(gemini, "_get_available_models", AsyncMock(return_value=[])):
            return await gemini.generate("p", max_tokens=100, fast=fast)

    async def test_503_on_one_model_moves_to_next_model_on_same_route(self):
        seen = []
        text, model = await self._run(
            [_Resp(503, {"error": {"status": "UNAVAILABLE"}}), _Resp(200, OK)], seen
        )
        self.assertEqual(text, "ответ")
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0][0], seen[1][0])
        # Рабочий прокси не должен попасть в карантин из-за перегрузки модели.
        self.assertTrue(routes.route_available("gemini", "http://127.0.0.1:3066"))

    async def test_max_tokens_is_retried_with_larger_limit(self):
        seen = []
        text, _ = await self._run([_Resp(200, CUT), _Resp(200, OK)], seen)
        self.assertEqual(text, "ответ")
        self.assertEqual([s[2] for s in seen], [100, 200])
        self.assertEqual(seen[0][0], seen[1][0])

    async def test_deprecated_gemini_25_models_are_not_in_chains(self):
        for name in gemini.FALLBACK_MODELS + gemini.FAST_MODELS:
            self.assertNotIn("2.5", name)


class RateLimitWait(unittest.IsolatedAsyncioTestCase):
    async def test_429_waits_for_retry_header_and_succeeds(self):
        client = market_api.MarketplaceClient("WB", "acct-test")
        calls = []

        async def once(*args):
            calls.append(time.monotonic())
            if len(calls) == 1:
                raise market_api.APIStatusError(429, "limit", retry_after=3)
            return {"ok": True}

        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)

        with patch.object(client, "_request_once", once), \
             patch.object(market_api.settings, "RATE_LIMIT_RETRIES", 2), \
             patch.object(market_api.asyncio, "sleep", fake_sleep):
            result = await client.request_json("POST", "https://finance-api.wildberries.ru/x", {})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(sleeps, [4])

    async def test_429_with_huge_wait_is_not_hammered(self):
        client = market_api.MarketplaceClient("WB", "acct-test2")

        async def once(*args):
            raise market_api.APIStatusError(429, "limit", retry_after=3600)

        with patch.object(client, "_request_once", once), \
             patch.object(market_api.settings, "RATE_LIMIT_RETRIES", 2):
            with self.assertRaises(market_api.APIStatusError):
                await client.request_json("POST", "https://finance-api.wildberries.ru/x", {})


if __name__ == "__main__":
    unittest.main()
