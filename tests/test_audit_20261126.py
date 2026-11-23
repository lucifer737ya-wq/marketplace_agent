"""Патч 2026-11-26: оценка по цвету звёзд, цвет товара, фильтр «Контент» (чистые функции)."""
import unittest

from services.ozon import browser_reviews as br


class RatingVote(unittest.TestCase):
    def test_majority_wins(self):
        self.assertEqual(br.vote_rating(4, 5, 5), 5)
        self.assertEqual(br.vote_rating(2, None, 4), 2)  # ничья: текст приоритетнее таблицы
        self.assertEqual(br.vote_rating(3, 4, None), 4)  # ничья: цвет звёзд приоритетнее текста

    def test_nothing_known(self):
        self.assertIsNone(br.vote_rating(None, None, None))

    def test_text_patterns_ignore_bare_digits(self):
        self.assertEqual(br._rating_from_text("Оценка\n4\nКомментарий"), 4)
        self.assertEqual(br._rating_from_text("5 ★"), 5)
        self.assertEqual(br._rating_from_text("Артикул 1234, 13.11"), None)

    def test_stars_from_colors(self):
        self.assertEqual(br._stars_from_colors({"stars": 3, "colors": [], "aria": None}), 3)
        self.assertEqual(br._stars_from_colors({"stars": 0, "colors": [], "aria": 2}), 2)
        self.assertIsNone(br._stars_from_colors(None))


class ProductColor(unittest.TestCase):
    def test_color_line(self):
        self.assertEqual(br._extract_product_color("Артикул 1\nЦвет: Розовый нюд\nОценка 5"), "Розовый нюд")
        self.assertEqual(br._extract_product_color("Оттенок\nМолочный"), "Молочный")
        self.assertEqual(br._extract_product_color("Цвет: —"), "")


if __name__ == "__main__":
    unittest.main()
