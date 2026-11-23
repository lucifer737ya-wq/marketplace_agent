"""Патч 2026-11-26b: поиск окна отзыва без role=dialog и фильтр «Контент» только внутри панели фильтров.

Чистые проверки (строки JS) идут всегда; DOM-проверки — только если установлен Chromium Playwright
(так же, как tests/test_review_dom.py).
"""
import os
import unittest
from pathlib import Path

from tests import offline  # noqa: F401 — изоляция тестов от боевых путей
from services.ozon import browser_reviews as br


class JsContracts(unittest.TestCase):
    def test_drawer_scripts_share_one_finder(self):
        for name in ("_MARK_LOOSE_JS", "_CLOSE_X_JS", "_REVIEW_CARD_OPEN_JS"):
            self.assertIn("findDrawer", getattr(br, name) + br._DRAWER_FN, name)
        self.assertIn("Отзыв на товар", br._DRAWER_FN)
        self.assertIn("ответить на отзыв", br._DRAWER_FN)

    def test_filter_scripts_ignore_table(self):
        for name in ("_CHECKBOX_STATE_JS", "_CHECKBOX_MARK_JS"):
            js = getattr(br, name)
            self.assertIn("inTable", js, name)
            self.assertIn("применить", js, name)
        # прежний опасный приём — клик по родителю первого найденного элемента
        self.assertFalse(hasattr(br, "_CHECKBOX_CLICK_JS"))

    def test_content_options_unchanged(self):
        self.assertEqual(
            br._CONTENT_OPTS, {"С текстом": True, "С медиа": True, "Только с оценкой": False}
        )


PAGE = """
<div id="app"><table><thead><tr><th>Дата</th><th>Отзыв</th></tr></thead><tbody>
<tr><td><input type="checkbox" id="rowcb"></td><td><span>С медиа</span> Хороший гель</td></tr></tbody></table></div>
<div id="filters"><label><input type="checkbox" id="t" checked><span>С текстом</span></label>
<label><input type="checkbox" id="m"><span>С медиа</span></label>
<label><input type="checkbox" id="r"><span>Только с оценкой</span></label>
<button>Применить</button></div>
<div id="ui-kit-side-page-portal-container" style="height:0;position:static">
  <div style="position:fixed;right:0;top:0;width:400px;height:300px"><h2>Отзыв на товар</h2>
  <button>Ответить на отзыв</button></div></div>
"""


class ReviewDomHelpers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        self.addAsyncCleanup(pw.stop)
        path = os.environ.get("CHROMIUM_EXECUTABLE_PATH") or pw.chromium.executable_path
        if not Path(path).is_file():
            self.skipTest("Install Playwright Chromium or set CHROMIUM_EXECUTABLE_PATH")
        browser = await pw.chromium.launch(executable_path=path, args=["--no-sandbox", "--disable-gpu"])
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.set_content(PAGE)

    async def test_filter_reads_panel_not_table_badge(self):
        names = list(br._CONTENT_OPTS)
        states = await self.page.evaluate(br._CHECKBOX_STATE_JS, names)
        self.assertEqual(states, {"С текстом": True, "С медиа": False, "Только с оценкой": False})

    async def test_filter_option_is_toggled_by_real_click_in_panel(self):
        await br._set_filter_option(self.page, "С медиа", True)
        self.assertTrue(await self.page.evaluate("document.getElementById('m').checked"))
        self.assertFalse(await self.page.evaluate("document.getElementById('rowcb').checked"))

    async def test_zero_height_portal_is_marked_as_card(self):
        self.assertTrue(await br._review_card_open(self.page))
        self.assertTrue(await self.page.evaluate(br._MARK_LOOSE_JS, "k"))
        marked = await self.page.evaluate("document.querySelector('[data-yasoko-card=k]').id")
        self.assertEqual(marked, "ui-kit-side-page-portal-container")


if __name__ == "__main__":
    unittest.main()
