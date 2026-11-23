import unittest

from tests.offline import STATE  # noqa: F401  (включает офлайн-изоляцию и тестовый .env)
from services.ozon import browser_reviews as br


class ReviewColumnFallbacks(unittest.TestCase):
    CELLS = [
        {"i": 0, "text": "Гель для ногтей", "link": True, "linkText": "Гель для ногтей", "x": 100, "w": 300},
        {"i": 1, "text": "Новый", "link": False, "linkText": "", "x": 400, "w": 100},
        {"i": 2, "text": "Прекрасный гель", "link": False, "linkText": "", "x": 600, "w": 200},
    ]

    def test_cell_found_by_x(self):
        self.assertEqual(br.review_cell_index_by_x(self.CELLS, 698), 2)

    def test_link_only_cell_is_rejected(self):
        self.assertIsNone(br.review_cell_index_by_x(self.CELLS, 150))

    def test_x_outside_table(self):
        self.assertIsNone(br.review_cell_index_by_x(self.CELLS, 5000))

    def test_review_header_variants(self):
        cols = br.resolve_review_columns(["Товар", "Статус", "Текст отзыва", "Дата отзыва"], 4)
        self.assertEqual(cols["review"], 2)
        cols = br.resolve_review_columns(["Товар", "Дата отзыва", "Оценка"], 3)
        self.assertIsNone(cols["review"])


if __name__ == "__main__":
    unittest.main()
