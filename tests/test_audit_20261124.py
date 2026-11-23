"""Координаты ответа на отзыв Ozon (калибровка 20.11.2026) и порядок кликов."""
import asyncio
import json
import unittest
from unittest import mock

from tests.offline import STATE  # noqa: F401  (включает офлайн-изоляцию и тестовый .env)
from services.ozon import browser_reviews as br


class _Mouse:
    def __init__(self, log):
        self.log = log

    async def move(self, x, y, steps=1):
        pass

    async def click(self, x, y):
        self.log.append((x, y))


class _Page:
    viewport_size = {"width": 1800, "height": 966}

    def __init__(self):
        self.clicks = []
        self.mouse = _Mouse(self.clicks)

    async def wait_for_timeout(self, ms):
        return None


class Defaults(unittest.TestCase):
    def test_default_points_are_the_calibrated_ones(self):
        with mock.patch.object(br, "REVIEW_POINTS_FILE", STATE / "no_such_points.json"):
            pts = br._load_points()
        self.assertEqual(pts["cell"], [745, 752])
        self.assertEqual(pts["reply"], [547, 517])
        self.assertEqual(pts["input"], [473, 808])
        self.assertEqual(pts["send"], [1317, 805])
        self.assertEqual(pts["close"], [1361, 25])
        self.assertEqual(pts["_viewport"], [1800, 966])

    def test_file_overrides_defaults(self):
        f = STATE / "points_override.json"
        f.write_text(json.dumps({"send": [1, 2], "_viewport": [1800, 966]}), encoding="utf-8")
        with mock.patch.object(br, "REVIEW_POINTS_FILE", f):
            self.assertEqual(br._load_points()["send"], [1, 2])

    def test_headless_viewport_matches_calibration(self):
        with mock.patch.object(br, "REVIEW_POINTS_FILE", STATE / "no_such_points.json"):
            self.assertEqual(br.browser_launch_kwargs(True)["viewport"], {"width": 1800, "height": 966})

    def test_click_in_same_window_is_not_scaled(self):
        page = _Page()
        with mock.patch.object(br, "REVIEW_POINTS_FILE", STATE / "no_such_points.json"):
            self.assertTrue(asyncio.run(br._coord_click(page, "close")))
        self.assertEqual(page.clicks, [(1361, 25)])

    def test_dom_mode_never_clicks_coordinates(self):
        page = _Page()
        with mock.patch.object(br.settings, "OZON_REVIEWS_CLICK_MODE", "dom", create=True):
            self.assertFalse(asyncio.run(br._coord_click(page, "send")))
        self.assertEqual(page.clicks, [])


class _Handle:
    async def get_attribute(self, name):
        return "R1"

    async def dispose(self):
        return None


class _Drawer:
    def locator(self, sel):
        class _L:
            async def count(self_inner):
                return 0
        return _L()

    async def element_handle(self, timeout=None):
        return _Handle()


class _SeqPage(_Page):
    def __init__(self, typed_ok=True):
        super().__init__()
        self.events, self.typed_ok, self.text = [], typed_ok, ""
        self.keyboard = self
        self.mouse.log = self.events  # клики и ввод — в одном журнале

    async def insert_text(self, text):
        self.events.append("type")
        self.text = text if self.typed_ok else text[:5]

    async def press(self, key):
        self.events.append("key:" + key)
        self.text = ""

    async def evaluate_handle(self, script):
        return _Handle()

    async def wait_for_function(self, script, arg=None, timeout=None):
        self.events.append("receipt")


class CoordReply(unittest.TestCase):
    def _run(self, page):
        async def fake_active(_p):
            return page.text

        with mock.patch.object(br, "REVIEW_POINTS_FILE", STATE / "no_such_points.json"), \
                mock.patch.object(br, "_active_editor_text", fake_active):
            return asyncio.run(br._coord_reply(page, _Drawer(), "Спасибо за отзыв!", "R1"))

    def test_steps_go_strictly_one_after_another(self):
        page = _SeqPage()
        self.assertTrue(self._run(page))
        self.assertEqual(page.events, [(547, 517), (473, 808), "type", (1317, 805), "receipt"])

    def test_nothing_is_sent_if_text_is_not_fully_in_field(self):
        page = _SeqPage(typed_ok=False)
        with self.assertRaises(br._SkipReview):
            self._run(page)
        self.assertNotIn((1317, 805), page.events)  # «самолётик» не нажат
        self.assertIn("key:Delete", page.events)  # обрезок стёрт


if __name__ == "__main__":
    unittest.main()
