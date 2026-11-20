"""Регрессии аудита 20.11.2026 (часть C): WB sellerOperName, цены ЛК, анонс, самопроверка, витрины."""

import asyncio
import json
import unittest
from unittest import mock

from maintenance import self_healer
from maintenance.ai_inspector import Issue
from maintenance.supervisor import _apply_edits, _instruction_lines, _json_edits
from services.common.buyer_questions import (
    has_unsafe_advice,
    is_negative_review,
    soft_announcement,
    _ensure_review_shape,
)
from services.common.price_calculator import _line, _pct_ru, Rates
from services.competitors.browser_listings import WB_SEARCH_RE, parse_ozon_tile_grid
from services.ozon.seller_api import _campaign_in_window
from services.wb.statistics_api import WBStatisticsAPI


def _sale_row(rid, oper="Продажа", **extra):
    row = {
        "rrdId": rid, "sellerOperName": oper, "docTypeName": "Продажа", "quantity": 1,
        "retailPriceWithDisc": "370.00", "retailAmount": "400.00", "forPay": "200.00",
        "acquiringFee": "6.00", "vendorCode": "1503", "saleDt": "2026-11-12T10:00:00",
        "deliveryService": "0",
    }
    row.update(extra)
    return row


class WbFinanceRows(unittest.TestCase):
    def test_new_finance_api_field_names_are_read(self):
        rows = [_sale_row(1), _sale_row(2, "Логистика", deliveryService="55.5", retailPriceWithDisc="0")]
        rep = WBStatisticsAPI._finance_rows(rows, 0.0, "2026-11-01", "2026-11-20")
        self.assertEqual(rep["orders_count"], 1)
        self.assertGreater(rep["commission"], 0)
        self.assertEqual(rep["acquiring"], 6.0)
        self.assertEqual(rep["logistics_direct"], 55.5)
        self.assertNotIn("—", rep["operation_counts"])

    def test_empty_operation_falls_back_to_doc_type(self):
        row = _sale_row(3)
        row["sellerOperName"] = ""
        rep = WBStatisticsAPI._finance_rows([row], 0.0, "a", "b")
        self.assertEqual(rep["orders_count"], 1)

    def test_return_is_negative(self):
        rep = WBStatisticsAPI._finance_rows(
            [_sale_row(1), _sale_row(2, "Возврат", docTypeName="Возврат")], 0.0, "a", "b"
        )
        self.assertEqual(rep["orders_count"], 1)
        self.assertEqual(rep["revenue_seller"], 0.0)

    def test_sales_api_estimate_fills_commission_and_acquiring(self):
        report = {"logistics_direct": 10.0, "logistics_return": 0.0, "storage": 0.0,
                  "acceptance": 0.0, "other_services": 0.0, "ads": 0.0}
        rows = [{"saleID": "S1", "priceWithDisc": 370, "finishedPrice": 330, "forPay": 190,
                 "supplierArticle": "1503", "date": "2026-11-12"}]
        self.assertTrue(WBStatisticsAPI._apply_sales_api_estimate(report, rows, 0.015))
        self.assertGreater(report["commission"], 0)
        self.assertGreater(report["acquiring"], 0)
        self.assertTrue(report["acquiring_estimated"] and report["commission_estimated"])
        self.assertAlmostEqual(report["total_payout"], 370 - report["total_deductions"], places=2)


class PriceLine(unittest.TestCase):
    def test_actual_margin_percent_instead_of_target(self):
        rates = Rates(commission_low=0.22, commission_high=0.52, pct_other=0.2, fixed_per_order=26.0,
                      source="тест")
        line = _line("OZON", "1001", 296.0, 70.0, rates, tiered=True, ratio=1.0)
        self.assertNotIn("цель", line)
        self.assertIn("чистая прибыль", line)
        self.assertIn("%", line)

    def test_decimal_comma(self):
        self.assertEqual(_pct_ru(18.2), "18,2%")
        self.assertEqual(_pct_ru(-5.3), "-5,3%")


class Replies(unittest.TestCase):
    def test_degreasing_nail_plate_is_not_unsafe(self):
        self.assertFalse(has_unsafe_advice("Перед нанесением обезжирьте ногтевую пластину."))
        self.assertTrue(has_unsafe_advice("Разбавьте гель спиртом для текучести."))

    def test_negative_detection(self):
        self.assertTrue(is_negative_review("гель отслоился через два дня", None))
        self.assertTrue(is_negative_review("нормально", 2))
        self.assertFalse(is_negative_review("отличный цвет", 5))

    def test_announcement_is_stable_and_mentions_development(self):
        text = soft_announcement("abc")
        self.assertEqual(text, soft_announcement("abc"))
        self.assertIn("баз", text)
        shaped = _ensure_review_shape("Спасибо за отзыв.", text)
        self.assertTrue(shaped.startswith("Здравствуйте"))
        self.assertIn(text, shaped)
        self.assertNotIn(text, _ensure_review_shape("Сожалеем.", ""))


class SelfHealerSplit(unittest.TestCase):
    def test_api_limit_is_warning_not_code_error(self):
        issues = [Issue("API", None, 0, "Повторяющийся сбой: Ozon HTTP 429", False)]
        with mock.patch.object(self_healer, "inspect_project", return_value=issues):
            res = asyncio.run(self_healer.run_full_self_check_and_heal())
        self.assertEqual(res["total_issues"], 0)
        self.assertEqual(res["env_warnings"], 1)
        self.assertIn("ошибок кода нет", res["summary"])


class LargeFileEditMode(unittest.TestCase):
    def test_edits_apply_once_only(self):
        code = "a = 1\nb = 2\nb = 2\n"
        self.assertEqual(_apply_edits(code, [{"find": "a = 1", "replace": "a = 5"}]), "a = 5\nb = 2\nb = 2\n")
        with self.assertRaises(ValueError):
            _apply_edits(code, [{"find": "b = 2", "replace": "b = 3"}])  # дважды в файле
        with self.assertRaises(ValueError):
            _apply_edits(code, [{"find": "zzz", "replace": "q"}])

    def test_lines_and_json(self):
        self.assertEqual(_instruction_lines("worker.py:120 NameError: x\nbot/a.py:7 ImportError"), [7, 120])
        self.assertEqual(len(_json_edits('текст {"edits":[{"find":"a","replace":"b"}]}')), 1)
        with self.assertRaises(ValueError):
            _json_edits("нет json")


class Storefronts(unittest.TestCase):
    def test_new_wb_internal_search_url_is_intercepted(self):
        self.assertTrue(WB_SEARCH_RE.search(
            "https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search?query=x"))
        self.assertTrue(WB_SEARCH_RE.search("https://search.wb.ru/exactmatch/ru/common/v18/search?query=x"))

    def test_ozon_tile_grid_price_rating_reviews(self):
        tile = {
            "sku": "511884916",
            "action": {"link": "/product/runail-gel-511884916/?x=1"},
            "mainState": [
                {"id": "p", "atom": {"type": "priceV2", "priceV2": {"price": [
                    {"text": "873 ₽", "textStyle": "PRICE"}, {"text": "1 200 ₽", "textStyle": "ORIGINAL_PRICE"}]}}},
                {"id": "n", "atom": {"type": "textAtom", "textAtom": {"text": "RuNail Professional Гель для моделирования"}}},
                {"id": "l", "atom": {"type": "labelList", "labelList": {"items": [
                    {"title": "4.9", "testInfo": {"automatizationId": "tile-list-rating"}},
                    {"title": "1 234 отзыва", "testInfo": {"automatizationId": "tile-list-comments"}}]}}},
            ],
        }
        payload = {"widgetStates": {"tileGridDesktop-1": json.dumps({"items": [tile]})}}
        items = parse_ozon_tile_grid(payload, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["buyer_price"], 873.0)
        self.assertEqual(items[0]["rating"], 4.9)
        self.assertEqual(items[0]["feedbacks"], 1234)

    def test_campaign_window(self):
        self.assertTrue(_campaign_in_window({"fromDate": "", "toDate": ""}, "2026-11-20"))
        self.assertFalse(_campaign_in_window({"toDate": "2026-11-01"}, "2026-11-20"))
        self.assertFalse(_campaign_in_window({"fromDate": "2026-12-01"}, "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
