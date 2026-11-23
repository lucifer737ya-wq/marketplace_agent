"""Ответ на отзыв по координатам — ОБЛАСТИ вокруг точек, координаты как основной способ."""
import asyncio
import json
import unittest
from unittest import mock

from tests.offline import STATE  # noqa: F401  (включает офлайн-изоляцию и тестовый .env)
from services.ozon import browser_reviews as br

NO_FILE = STATE / "no_such_points_areas.json"


def _hit(text="", **kw):
    base = {"text": text, "button": False, "link": False, "editable": False, "disabled": False, "row": None}
    base.update(kw)
    return base


class _Mouse:
    def __init__(self, log):
        self.log = log

    async def move(self, x, y, steps=1):
        pass

    async def click(self, x, y):
        self.log.append((x, y))


class _Page:
    """Страница-заглушка: elementFromPoint отдаёт то, что вернёт hit_fn(x, y)."""

    def __init__(self, hit_fn=None, size=(1800, 966)):
        self.viewport_size = {"width": size[0], "height": size[1]}
        self.clicks = []
        self.mouse = _Mouse(self.clicks)
        self.hit_fn = hit_fn
        self.url = "https://seller.ozon.ru/app/reviews"

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, script, arg=None):
        if self.hit_fn is None:
            raise RuntimeError("no evaluate")
        return [self.hit_fn(x, y) for x, y in arg]


class Zones(unittest.TestCase):
    def test_default_zone_is_area_around_calibrated_point(self):
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            cx, cy, x1, y1, x2, y2 = br._zone("reply")
        self.assertEqual((cx, cy), (547, 517))
        self.assertEqual((x1, y1, x2, y2), (547 - 90, 517 - 60, 547 + 90, 517 + 60))

    def test_four_numbers_set_an_area_and_center_is_its_middle(self):
        f = STATE / "points_area4.json"
        f.write_text(json.dumps({"reply": [400, 400, 600, 500], "_viewport": [1800, 966]}), encoding="utf-8")
        with mock.patch.object(br, "REVIEW_POINTS_FILE", f):
            self.assertEqual(br._zone("reply"), (500, 450, 400, 400, 600, 500))

    def test_areas_block_keeps_clicked_point_as_center(self):
        f = STATE / "points_areas_block.json"
        f.write_text(json.dumps({
            "send": [1317, 805], "_areas": {"send": [1290, 780, 1350, 830]}, "_viewport": [1800, 966],
        }), encoding="utf-8")
        with mock.patch.object(br, "REVIEW_POINTS_FILE", f):
            self.assertEqual(br._zone("send"), (1317, 805, 1290, 780, 1350, 830))

    def test_grid_starts_with_center(self):
        pts = br._grid((0, 0, 60, 40), 30, 20)
        self.assertEqual(pts[0], (30, 20))
        self.assertEqual(len(pts), br._GRID_COLS * br._GRID_ROWS)

    def test_area_is_scaled_to_real_window(self):
        page = _Page(size=(900, 483))
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            cx, cy, box = asyncio.run(br._scaled_zone(page, "close"))
        self.assertEqual((cx, cy), (round(1361 / 2), round(25 / 2)))
        self.assertTrue(box[0] <= cx <= box[2] and box[1] <= cy <= box[3])


class Clicking(unittest.TestCase):
    def test_button_shifted_down_is_found_inside_area(self):
        # Кнопка «Ответить» сдвинулась вниз на ~40 px (длинный отзыв): центр попадает в пустоту.
        def hit(x, y):
            if 555 <= y <= 575 and 480 <= x <= 620:
                return _hit("Ответить на отзыв", button=True)
            return _hit("текст отзыва")
        page = _Page(hit)
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertTrue(asyncio.run(br._coord_click(page, "reply")))
        (x, y), = page.clicks
        self.assertTrue(555 <= y <= 575 and 480 <= x <= 620, (x, y))

    def test_blind_center_click_when_nothing_matches(self):
        page = _Page(lambda x, y: _hit("пусто"))
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertTrue(asyncio.run(br._coord_click(page, "close")))
        self.assertEqual(page.clicks, [(1361, 25)])

    def test_strict_never_clicks_blindly(self):
        page = _Page(lambda x, y: _hit("пусто"))
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertFalse(asyncio.run(br._coord_click(page, "cell", hint=br._row_hint("R"), strict=True)))
        self.assertEqual(page.clicks, [])

    def test_row_hint_requires_exact_row_and_no_link(self):
        h = br._row_hint("строка")
        self.assertTrue(h(_hit(row="строка")))
        self.assertFalse(h(_hit(row="чужая")))
        self.assertFalse(h(_hit(row="строка", link=True)))

    def test_send_is_exactly_one_click(self):
        page = _Page(lambda x, y: _hit("", button=True))
        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertTrue(asyncio.run(br._coord_click(page, "send")))
        self.assertEqual(len(page.clicks), 1)

    def test_verify_retries_neighbour_points_until_confirmed(self):
        page = _Page(None)  # без elementFromPoint — «вслепую»
        state = {"n": 0}

        async def verify():
            state["n"] += 1
            return len(page.clicks) >= 2

        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertTrue(asyncio.run(br._coord_click(page, "input", verify=verify)))
        self.assertEqual(len(page.clicks), 2)
        self.assertEqual(page.clicks[0], (473, 808))  # первым всегда калиброванный центр
        self.assertNotEqual(page.clicks[0], page.clicks[1])

    def test_verify_gives_up_after_limited_tries(self):
        page = _Page(None)

        async def never():
            return False

        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertFalse(asyncio.run(br._coord_click(page, "input", verify=never)))
        self.assertLessEqual(len(page.clicks), 5)

    def test_stops_if_click_left_reviews_page(self):
        page = _Page(None)

        async def never():
            page.url = "https://www.ozon.ru/product/1"
            return False

        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE):
            self.assertFalse(asyncio.run(br._coord_click(page, "reply", verify=never)))
        self.assertEqual(len(page.clicks), 1)


class Modes(unittest.TestCase):
    def _mode(self, value):
        return mock.patch.object(br.settings, "OZON_REVIEWS_CLICK_MODE", value, create=True)

    def test_modes(self):
        for value, first, enabled in (("auto", True, True), ("coords", True, True),
                                      ("dom_first", False, True), ("dom", False, False)):
            with self._mode(value):
                self.assertEqual((br._coords_first(), br._coords_enabled()), (first, enabled), value)


class _Drawer:
    def locator(self, sel):
        class _L:
            async def count(self_inner):
                return 0
        return _L()


class ReplyNotFound(unittest.TestCase):
    def test_nothing_typed_or_sent_if_editor_never_opens(self):
        page = _Page(None)
        page.keyboard = mock.Mock()

        async def no_editor(_p):
            return None

        with mock.patch.object(br, "REVIEW_POINTS_FILE", NO_FILE), \
                mock.patch.object(br, "_active_editor_text", no_editor):
            with self.assertRaises(br._CoordNotReady):
                asyncio.run(br._coord_reply(page, _Drawer(), "Спасибо за отзыв!", "R1"))
        self.assertNotIn((1317, 805), page.clicks)  # «самолётик» не нажат
        self.assertFalse(page.keyboard.insert_text.called)



class PointsMerge(unittest.TestCase):
    def test_partial_file_keeps_defaults_for_missing_keys(self):
        f = STATE / "points_partial.json"
        f.write_text(json.dumps({"reply": [10, 20], "_viewport": [1800, 966]}), encoding="utf-8")
        with mock.patch.object(br, "REVIEW_POINTS_FILE", f):
            pts = br._load_points()
        self.assertEqual(pts["reply"], [10, 20])
        self.assertEqual(pts["close"], [1361, 25])  # не потеряна

    def test_file_from_other_window_size_is_not_mixed_with_defaults(self):
        f = STATE / "points_old_window.json"
        f.write_text(json.dumps({"reply": [10, 20], "_viewport": [1440, 900]}), encoding="utf-8")
        with mock.patch.object(br, "REVIEW_POINTS_FILE", f):
            pts = br._load_points()
        self.assertEqual(set(pts), {"reply", "_viewport"})


if __name__ == "__main__":
    unittest.main()
