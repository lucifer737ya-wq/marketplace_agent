import asyncio
import unittest

from tests.offline import STATE  # noqa: F401  (включает офлайн-изоляцию и тестовый .env)
from services.ozon import browser_reviews as br


class _Timeout(br.PWTimeout):
    pass


class _FakeLocator:
    """Клик проходит только после N-й попытки (имитация подложки, перехватывающей клик)."""

    def __init__(self, ok_on: int):
        self.ok_on, self.calls, self.forced = ok_on, 0, False

    async def click(self, timeout=None, force=False):
        self.calls += 1
        if force:
            self.forced = True
            return
        if self.calls < self.ok_on:
            raise br.PWTimeout("Locator.click: Timeout\n - div intercepts pointer events")


class _FakePage:
    viewport_size = None

    def __init__(self, size=(1440, 900)):
        self.size, self.escapes, self.neutralized = size, 0, 0
        self.keyboard = self
        self.neutralize_calls = 0

    async def press(self, key):
        self.escapes += 1

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, sel):
        page = self

        class _Box:
            async def count(self_inner):
                return 0

        return _Box()

    async def evaluate(self, script, arg=None):
        if "innerWidth, window.innerHeight" in script:
            return list(self.size)
        self.neutralize_calls += 1
        return 1


class LaunchOptions(unittest.TestCase):
    def test_automation_infobar_is_disabled(self):
        for headless in (True, False):
            kw = br.browser_launch_kwargs(headless)
            self.assertIn("--enable-automation", kw["ignore_default_args"])

    def test_visible_window_fits_screen(self):
        kw = br.browser_launch_kwargs(False)
        self.assertIsNone(kw["viewport"])
        self.assertIn("--start-maximized", kw["args"])
        self.assertNotIn("--start-maximized", br.DIRECT_BROWSER_ARGS)

    def test_headless_keeps_calibrated_viewport(self):
        kw = br.browser_launch_kwargs(True)
        self.assertEqual(kw["viewport"], br.VIEWPORT)


class ShortError(unittest.TestCase):
    def test_call_log_is_not_sent_whole(self):
        err = br.PWTimeout("Locator.click: Timeout 30000ms exceeded.\nCall log:\n" + "- retry\n" * 500)
        text = br.short_error(err)
        self.assertNotIn("\n", text)
        self.assertLess(len(text), 400)

    def test_overlay_hint(self):
        err = br.PWTimeout("Timeout\n- <div> intercepts pointer events")
        self.assertIn("перекрыт", br.short_error(err))


class ClickClear(unittest.TestCase):
    def test_plain_click(self):
        loc, page = _FakeLocator(1), _FakePage()
        asyncio.run(br._click_clear(page, loc))
        self.assertEqual(loc.calls, 1)
        self.assertEqual(page.escapes, 0)

    def test_recovers_after_dismiss(self):
        loc, page = _FakeLocator(2), _FakePage()
        asyncio.run(br._click_clear(page, loc))
        self.assertEqual(loc.calls, 2)
        self.assertGreaterEqual(page.escapes, 1)

    def test_recovers_after_backdrop_neutralized(self):
        loc, page = _FakeLocator(3), _FakePage()
        asyncio.run(br._click_clear(page, loc))
        self.assertEqual(loc.calls, 3)
        self.assertEqual(page.neutralize_calls, 1)

    def test_falls_back_to_force(self):
        loc, page = _FakeLocator(99), _FakePage()
        asyncio.run(br._click_clear(page, loc))
        self.assertTrue(loc.forced)


class PointScaling(unittest.TestCase):
    def test_same_size_is_unchanged(self):
        self.assertEqual(asyncio.run(br._scale_pt(_FakePage((1440, 900)), 377, 737)), (377, 737))

    def test_smaller_window_scales(self):
        x, y = asyncio.run(br._scale_pt(_FakePage((720, 450)), 400, 600))
        self.assertEqual((x, y), (200, 300))


if __name__ == "__main__":
    unittest.main()
