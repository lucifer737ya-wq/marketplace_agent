"""Регрессии отчёта 22.11.2026: ROI к закупке, столбцы отзывов по шапке, проверка выходной страны."""

import unittest
from types import SimpleNamespace

from services.common import cogs, pricing_policy
from services.common.direct_guard import looks_like_vpn_wall
from services.ozon import browser_reviews as br


class RoiOnPurchase(unittest.TestCase):
    def test_example_38_over_61_is_62_3_percent(self):
        self.assertAlmostEqual(38 / 61 * 100, 62.3, places=1)
        prod = SimpleNamespace(cost_price=61.0, first_mile_unit_cost=0.0, packaging_cost=0.0)
        self.assertEqual(cogs.roi_base_of(prod), 61.0)
        self.assertGreaterEqual(cogs.unit_cogs_of(prod), 61.0)  # полная себестоимость не меньше закупки

    def test_recommend_price_uses_purchase_as_base(self):
        full_cogs, purchase = 76.0, 61.0
        pf = lambda p: p * 0.5 - full_cogs  # прибыль после вычета полной себестоимости
        rec = pricing_policy.recommend_price(pf, full_cogs, lo=1, hi=3000, min_roi=0.30, max_roi=1.0, roi_base=purchase)
        # цена должна давать прибыль >= 30% закупки (18.3 ₽), а не 30% себестоимости (22.8 ₽)
        self.assertGreaterEqual(pf(rec.p_min_roi), 0.30 * purchase)
        self.assertLess(pf(rec.p_min_roi - 1), 0.30 * purchase)
        self.assertAlmostEqual(rec.roi, rec.profit / purchase, places=3)
        self.assertGreaterEqual(pf(rec.p_max_roi), 1.0 * purchase)


class ReviewColumns(unittest.TestCase):
    HEAD = ["", "Товар", "Статус", "Отзыв", "Видео", "Фото", "Ответы", "Дата"]

    def test_review_column_is_found_by_header_not_by_index_3(self):
        cols = br.resolve_review_columns(self.HEAD, 8)
        self.assertEqual(cols["review"], 3)
        self.assertEqual((cols["video"], cols["photo"], cols["answers"]), (4, 5, 6))

    def test_product_block_split_into_three_cells_shifts_from_right(self):
        # чекбокс | фото | название | статус | отзыв | видео | фото | ответы | дата
        cols = br.resolve_review_columns(["Товар", "Статус", "Отзыв", "Видео", "Фото", "Ответы", "Дата"], 9)
        self.assertEqual(cols["review"], 4)
        self.assertEqual(cols["status"], 3)

    def test_review_falls_back_to_cell_after_status(self):
        cols = br.resolve_review_columns(["Товар", "Статус", "Текст"], 3)
        self.assertEqual(cols["review"], 2)

    def test_reply_rule_text_or_media(self):
        self.assertEqual(br.review_row_decision("-", "0", "0")[0], False)
        self.assertEqual(br.review_row_decision("—", "0", "0")[0], False)
        self.assertEqual(br.review_row_decision("Отличный гель", "0", "0")[0], True)
        self.assertEqual(br.review_row_decision("-", "2", "0")[0], True)
        self.assertEqual(br.review_row_decision("–", "0", "1")[0], True)
        self.assertEqual(br.review_row_decision("Отличный гель", "0", "0", rule="media")[0], False)


class VpnWall(unittest.TestCase):
    def test_marker_detection(self):
        self.assertTrue(looks_like_vpn_wall("Пожалуйста, отключите VPN и обновите страницу"))
        self.assertFalse(looks_like_vpn_wall("Гель для ногтей — 296 ₽"))


if __name__ == "__main__":
    unittest.main()
