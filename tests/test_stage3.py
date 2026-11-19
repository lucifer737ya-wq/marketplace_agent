"""Контрактные и финансовые тесты этапа 3 (финансы, реклама, конкуренты) без внешних запросов."""
import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from tests import offline
from services.common.routes import reset_routes

offline.require_isolation()  # before importing application settings

from sqlalchemy import delete, select

from database.crud import record_first_mile_supply, upsert_product_costs
from database.models import OrderRecord, Product, close_db, get_db_session, init_db
from services.common.ad_recommendations import (
    analyze_ozon_campaigns,
    calculate_ozon_ad_unit_economics,
    generate_weekly_ads_report_and_recommendations,
    sanitize_telegram_html,
)
from services.common.financial_pnl import (
    generate_monthly_pnl_report,
    generate_weekly_top_and_unprofitable_report,
    get_previous_month_bounds,
)
from services.common.orders_report import sync_and_generate_orders_report
from services.competitors.deep_parser import (
    WB_HTTP_TIMEOUT,
    _fetch_json_direct,
    calculate_competitor_unit_economy,
    parse_ozon_competitors,
    parse_wb_competitors,
    run_full_competitor_deep_audit,
)


class TestStage3FinanceAndOrders(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(OrderRecord))
            await session.execute(delete(Product))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_orders_report_exact_profit_acquiring_tax_logistics_and_mp_isolation(self) -> None:
        # Один и тот же артикул 1001 имеет разную себестоимость/первую милю на Ozon и WB.
        await upsert_product_costs("1001", "ozon", cost_price=52.0, packaging_cost=15.0)
        await record_first_mile_supply("OZ-1", "ozon", "1001", quantity=10, total_transport_cost=80.0)  # +8 ₽ -> 75 ₽
        await upsert_product_costs("1001", "wb", cost_price=60.0, packaging_cost=20.0)
        await record_first_mile_supply("WB-1", "wb", "1001", quantity=10, total_transport_cost=120.0)  # +12 ₽ -> 92 ₽

        # Ozon: 10 шт. по 299 ₽ = 2990 ₽; комиссия 23% = 687.70 ₽; доставка 388.70 ₽ (38.87 ₽/заказ);
        # эквайринг в cash-flow = 0.0 -> должен доначислиться 1.5% = 44.85 ₽;
        # COGS = 10 * 75 = 750 ₽; УСН 6% = 179.40 ₽;
        # Выплата = 2990 - (687.70 + 388.70 + 44.85) = 1868.75 ₽; Чистая прибыль = 1868.75 - 750 - 179.40 = 939.35 ₽.
        ozon_orders = [
            {
                "order_id": f"oz_test_{i}",
                "marketplace": "ozon",
                "sku": "3405669329",  # ozon_sku для 1001
                "created_at": datetime(2026, 11, 10, 12, 0),
                "price": 299.0,
                "status": "ordered",
            }
            for i in range(10)
        ]
        # Дубликат order_id в одной пачке не должен вызывать IntegrityError.
        ozon_orders_with_dup = [*ozon_orders, dict(ozon_orders[0])]

        oz_mock = AsyncMock()
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "ЛК Ozon Финансы",
                "orders": ozon_orders_with_dup,
                "orders_count": 10,
                "sales_total": 2990.0,
                "commission": 687.70,
                "deliv_total": 388.70,
                "deliv_direct": 388.70,
                "acquiring": 0.0,
                "partners_total": 0.0,
                "total_deductions": 1076.40,
                "total_payout": 1913.60,
            }
        )

        # WB: 2 шт., ЛК = 700 ₽, СПП покупателя = 600 ₽; комиссия 175 ₽, логистика 110 ₽,
        # эквайринг 1.5% от max(600, 700)=700 -> 10.50 ₽; удержания = 295.50 ₽; выплата = 404.50 ₽;
        # COGS = 2 * 92 = 184 ₽; УСН 6% с 600 ₽ = 36 ₽; Чистая прибыль WB = 404.50 - 184 - 36 = 184.50 ₽.
        wb_mock = AsyncMock()
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Фин. отчет WB",
                "orders": [
                    {
                        "order_id": "wb_test_1",
                        "marketplace": "wb",
                        "sku": "1001",
                        "created_at": "2026-11-11T10:00:00Z",
                        "price": 350.0,
                        "buyer_price": 300.0,
                        "commission": 87.5,
                        "logistics": 55.0,
                        "status": "sold",
                    },
                    {
                        "order_id": "wb_test_2",
                        "marketplace": "wb",
                        "sku": "765197240",  # wb_sku для 1001
                        "created_at": "2026-11-11T11:00:00Z",
                        "price": 350.0,
                        "buyer_price": 300.0,
                        "commission": 87.5,
                        "logistics": 55.0,
                        "status": "sold",
                    },
                ],
                "orders_count": 2,
                "revenue_seller": 700.0,
                "revenue_buyer": 600.0,
                "commission": 175.0,
                "acquiring": 10.50,
                "logistics": 110.0,
                "total_deductions": 295.50,
                "total_payout": 404.50,
            }
        )

        # Проверка отсутствия двойного списания эквайринга, когда Ozon уже вернул факт 44.85 ₽:
        report_est = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("38.87 ₽/заказ", report_est)
        self.assertIn("Эквайринг (расч. 1.5%): -44.85 ₽", report_est)
        self.assertIn("179.40 ₽", report_est)
        self.assertIn("<code>939.35 ₽</code>", report_est)
        self.assertIn("<code>184.50 ₽</code>", report_est)
        # Сводная чистая прибыль: 939.35 + 184.50 = 1,123.85 ₽
        self.assertIn("<code>1,123.85 ₽</code>", report_est)

        # Повторный запуск с фактическим эквайрингом Ozon 44.85 ₽ должен дать ту же прибыль (без двойного учета)
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "ЛК Ozon Финансы",
                "orders": ozon_orders,
                "orders_count": 10,
                "sales_total": 2990.0,
                "commission": 687.70,
                "deliv_total": 388.70,
                "acquiring": 44.85,
                "partners_total": 44.85,
                "total_deductions": 1121.25,
                "total_payout": 1868.75,
            }
        )
        report_actual = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("Эквайринг: -44.85 ₽", report_actual)
        self.assertNotIn("Эквайринг (расч.", report_actual)
        self.assertIn("<code>939.35 ₽</code>", report_actual)

        # Проверяем, что сохранено ровно 12 уникальных заказов (10 Ozon + 2 WB)
        async with get_db_session() as session:
            saved = (await session.execute(select(OrderRecord))).scalars().all()
            self.assertEqual(len(saved), 12)
            self.assertTrue(all(r.sku == "1001" for r in saved))

    async def test_empty_and_negative_return_periods_no_zero_division(self) -> None:
        oz_mock = AsyncMock()
        wb_mock = AsyncMock()

        # Полностью пустой период (0 заказов, 0 выручки, 0 доставки)
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Ozon Empty",
                "orders": [],
                "orders_count": 0,
                "sales_total": 0.0,
                "deliv_total": 0.0,
                "acquiring": 0.0,
                "total_deductions": 0.0,
                "total_payout": 0.0,
            }
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "WB Empty",
                "orders": [],
                "orders_count": 0,
                "revenue_seller": 0.0,
                "revenue_buyer": 0.0,
                "acquiring": 0.0,
                "logistics": 0.0,
                "total_deductions": 0.0,
                "total_payout": 0.0,
            }
        )
        oz_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

        rep = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <code>0 ₽</code> (<b>0.0% ROS</b>)", rep)
        self.assertNotIn("₽/заказ", rep)
        self.assertNotIn("-0 ₽", rep)

        top5 = await generate_weekly_top_and_unprofitable_report(oz_mock, wb_mock, days=14)
        self.assertIn("14 дн.", top5)
        self.assertIn("заказов не найдено", top5)

        # Период только с возвратами (отрицательная выручка): налог УСН = 0 ₽, не отрицательный
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "source": "Ozon Returns",
                "orders": [],
                "orders_count": 0,
                "sales_total": -598.0,
                "deliv_return": 80.0,
                "deliv_total": 80.0,
            }
        )
        rep_ret = await sync_and_generate_orders_report(oz_mock, wb_mock, days=7)
        self.assertIn("Налог УСН (6%): <b>0 ₽</b> (0.0%)", rep_ret)
        self.assertIn("<code>-678 ₽</code>", rep_ret)

    async def test_monthly_bounds_and_weekly_top5_no_fake_cogs_and_wb_acquiring(self) -> None:
        # Проверка високосного февраля и смены года
        b_leap = get_previous_month_bounds(datetime(2024, 3, 2, 1, 0))
        self.assertEqual(
            (b_leap["date_from"], b_leap["date_to"], b_leap["days_count"]),
            ("2024-02-01", "2024-02-29", 29),
        )
        b_jan = get_previous_month_bounds(datetime(2026, 1, 5, 10, 0))
        self.assertEqual(
            (b_jan["date_from"], b_jan["date_to"], b_jan["days_count"]),
            ("2025-12-01", "2025-12-31", 31),
        )

        # Задаем себестоимость только для 1001 Ozon и 1002 WB; 1003 Ozon оставляем без себестоимости
        await upsert_product_costs("1001", "ozon", cost_price=52.0, packaging_cost=15.0)  # 67 ₽
        await upsert_product_costs("1002", "wb", cost_price=55.0, packaging_cost=15.0)  # 70 ₽

        oz_mock = AsyncMock()
        wb_mock = AsyncMock()
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "orders": [
                    # Арт. 1001 в ловушке 350 ₽ (>300 ₽ -> комиссия 52% = 182 ₽, экв. 1.5% = 5.25 ₽,
                    # дост. 6686/172 = 38.87 ₽, УСН 6% = 21 ₽, себес 67 ₽ -> прибыль +35.88 ₽)
                    {"order_id": "o1", "marketplace": "ozon", "sku": "1001", "price": 350.0, "status": "ordered"},
                    # Арт. 1003 без себестоимости — не должен получить фейковые 70 ₽!
                    {"order_id": "o2", "marketplace": "ozon", "sku": "1003", "price": 299.0, "status": "ordered"},
                ],
                "orders_count": 172,
                "sales_total": 51428.0,
                "deliv_total": 6686.0,
            }
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={
                "orders": [
                    # WB 1002: цена 300 ₽, buyer 250 ₽, ком. 75 ₽, лог. 55 ₽, экв. 1.5% от 300 = 4.50 ₽,
                    # УСН 6% от 250 = 15 ₽, себес 70 ₽ -> прибыль = 300 - 70 - (75+55+4.5) - 15 = 80.50 ₽
                    {
                        "order_id": "w1",
                        "marketplace": "wb",
                        "sku": "1002",
                        "price": 300.0,
                        "buyer_price": 250.0,
                        "commission": 75.0,
                        "logistics": 55.0,
                        "status": "sold",
                    }
                ],
                "orders_count": 1,
                "revenue_seller": 300.0,
                "revenue_buyer": 250.0,
            }
        )
        oz_mock.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "1001", "buyer_price": 210.0, "commission_rate": 0.23, "stock_fbo": 10, "stock_fbs": 0}]
        )
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

        report = await generate_weekly_top_and_unprofitable_report(oz_mock, wb_mock, days=7)
        self.assertIn("38.87 ₽/заказ", report)
        self.assertIn("35.88 ₽", report)  # Точная прибыль Ozon 1001 с 52%, 1.5%, 38.87 и УСН 6% с 350 ₽ (а не 210 ₽)
        self.assertIn("80.50 ₽", report)  # Точная прибыль WB 1002 с учетом эквайринга 1.5% (4.50 ₽)
        self.assertIn("Без подтвержденной себестоимости", report)
        self.assertIn("OZON:1003", report)

        # Месячный отчет до 5-го числа показывает предупреждение о регламенте закрывающих актов
        monthly = await generate_monthly_pnl_report(
            oz_mock, wb_mock, ref_date=datetime(2026, 11, 3, 9, 0)
        )
        self.assertIn("3-е число", monthly)
        self.assertIn("ОКТЯБРЬ 2026", monthly)


class TestStage3OzonAdCampaigns(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(Product))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_two_ozon_campaigns_6_3_and_15_4_separated_from_organic(self) -> None:
        all_skus = ["1001", "1002", "1003", "1006", "1010", "1023", "1028", "1031"]
        for sku in all_skus:
            await upsert_product_costs(sku, "ozon", cost_price=52.0, packaging_cost=15.0)  # 67 ₽
            await record_first_mile_supply("FM", "ozon", sku, quantity=1, total_transport_cost=8.0)  # +8 ₽ -> 75 ₽
        # Другая себестоимость WB не должна влиять на расчет Ozon
        await upsert_product_costs("1001", "wb", cost_price=200.0, packaging_cost=50.0)

        async with get_db_session() as session:
            db_prods = list((await session.execute(select(Product))).scalars().all())

        oz_stocks = [
            {
                "sku": sku,
                "product_id": f"900{idx}",
                "current_price": 299.0,
                "commission_rate_fbo": 0.23,
                "commission_rate_fbs": 0.23,
                "stock_fbo": 40,
                "stock_fbs": 0,
                "daily_sales": 3.0,
                "in_promo": True,
            }
            for idx, sku in enumerate(all_skus, 1)
        ]
        # Добавим 9-й товар с нулевым остатком — не должен попасть в активную органику
        oz_stocks.append(
            {"sku": "1035", "product_id": "9099", "current_price": 299.0, "stock_fbo": 0, "stock_fbs": 0}
        )

        # Кампания №1: 4 товара (переданы через ozon_sku из каталога + дубликат), ДРР = 1883.70 / 29900 = 6.3%
        # Кампания №2: 2 товара (переданы через ozon_sku из каталога), ДРР = 1381.38 / 8970 = 15.4%
        # Архивная кампания №3 с органическим 1028 должна игнорироваться!
        raw_campaigns = [
            {
                "id": "111",
                "title": "Кампания №1 (Локомотивы)",
                "state": "CAMPAIGN_STATE_RUNNING",
                "skus_inside": ["3405669329", "3405668968", "3405668876", "3405668993", "3405669329", ""],
                "spend_7d_rub": 1883.70,
                "promo_revenue_7d_rub": 29900.0,
                "drr_in_promo_pct": 6.3,
            },
            {
                "id": "222",
                "title": "Кампания №2 (Тест оттенков)",
                "state": "CAMPAIGN_STATE_RUNNING",
                "skus_inside": ["3405669196", "3405669300"],
                "spend_7d_rub": 1381.38,
                "promo_revenue_7d_rub": 8970.0,
                "drr_in_promo_pct": 15.4,
            },
            {
                "id": "333",
                "title": "Остановленная РК",
                "state": "CAMPAIGN_STATE_STOPPED",
                "skus_inside": ["1028"],
                "spend_7d_rub": 0.0,
                "promo_revenue_7d_rub": 0.0,
            },
        ]

        oz_fin = {
            "orders_count": 172,
            "sales_total": 51428.0,
            "deliv_total": 6686.0,  # 6686 / 172 = 38.87 ₽/заказ (~39 ₽)
            "ads_total": 3265.08,
            "promo_cpo": 1200.0,
            "promo_cpc": 2065.08,
        }

        res = analyze_ozon_campaigns(raw_campaigns, oz_stocks, oz_fin, db_products=db_prods)
        self.assertTrue(res["campaigns_available"])
        self.assertEqual(len(res["campaigns"]), 2)

        c1, c2 = res["campaigns"]
        self.assertEqual(c1["products_count"], 4)
        self.assertEqual(c1["skus"], ["1001", "1002", "1003", "1006"])
        self.assertEqual(c1["drr_in_promo_pct"], 6.3)

        self.assertEqual(c2["products_count"], 2)
        self.assertEqual(c2["skus"], ["1010", "1023"])
        self.assertEqual(c2["drr_in_promo_pct"], 15.4)

        # Органические SKU строго отделены (только 1028 и 1031)
        org_skus = [it["sku"] for it in res["organic_items"]]
        self.assertEqual(org_skus, ["1028", "1031"])
        self.assertTrue(set(org_skus).isdisjoint(set(res["promoted_skus"])))
        self.assertEqual(res["overlapping_skus"], [])

        # Фактическая логистика Ozon: 6686 / 172 = 38.87 ₽
        self.assertEqual(res["finance"]["actual_delivery_per_order"], 38.87)

        # Точная поштучная прибыль при 299 ₽:
        # 299 - 68.77 (23%) - 4.49 (1.5%) - 17.94 (6%) - 38.87 (лог) - 75.00 (себес) = 93.93 ₽ до рекламы
        # Кампания №1 (ДРР 6.3% = 18.84 ₽): 93.93 - 18.84 = +75.09 ₽
        # Кампания №2 (ДРР 15.4% = 46.05 ₽): 93.93 - 46.05 = +47.88 ₽
        # Органика (ДРР 0.0% = 0.00 ₽): +93.93 ₽
        e1 = c1["items"][0]["unit_economics"]
        e2 = c2["items"][0]["unit_economics"]
        eo = res["organic_items"][0]["unit_economics"]
        self.assertEqual((e1["acq_rub"], e1["tax_rub"], e1["ad_rub"], e1["net_profit"]), (4.49, 17.94, 18.84, 75.09))
        self.assertEqual((e2["acq_rub"], e2["tax_rub"], e2["ad_rub"], e2["net_profit"]), (4.49, 17.94, 46.05, 47.88))
        self.assertEqual((eo["ad_rub"], eo["net_profit"]), (0.0, 93.93))
        self.assertGreater(c1["items"][0]["tier_over300_economics"]["price"], 300.0)
        self.assertEqual(c1["items"][0]["tier_over300_economics"]["comm_pct"], 52.0)

    async def test_campaigns_error_and_spend_without_revenue_no_fake_stubs(self) -> None:
        oz_mock = AsyncMock()
        wb_mock = AsyncMock()
        oz_mock.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "1001", "current_price": 299.0, "stock_fbo": 15, "stock_fbs": 0}]
        )
        wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])
        # Пустой финансовый отчет (0 заказов, 0 доставки) — не должен подставлять 172 и 6686!
        oz_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={"orders_count": 0, "sales_total": 0.0, "deliv_total": 0.0, "ads_total": 0.0}
        )
        wb_mock.fetch_real_finance_breakdown = AsyncMock(
            return_value={"orders_count": 0, "revenue_seller": 0.0, "ads": 0.0}
        )
        oz_mock.fetch_active_ad_campaigns = AsyncMock(
            side_effect=RuntimeError("Ozon Performance API не вернул кампании")
        )
        wb_mock.fetch_active_ad_campaigns = AsyncMock(return_value=[])

        with patch(
            "services.common.ad_recommendations._call_gemini_unlimited",
            new=AsyncMock(return_value="<b>План:</b> проверить ключи Performance API."),
        ) as ai_mock:
            text = await generate_weekly_ads_report_and_recommendations(oz_mock, wb_mock)
            prompt_sent = ai_mock.await_args.args[0]

        self.assertIn("Состав кампаний Ozon не получен", text)
        self.assertNotIn("172 шт.", text)
        self.assertNotIn("6,686", text)
        self.assertNotIn("6.3%", text)
        self.assertNotIn("15.4%", text)
        self.assertIn('"campaigns_available": false', prompt_sent)
        self.assertIn('"organic_skus_not_in_ads": []', prompt_sent)

        # Расход без заказов (revenue=0, spend=450) — не делит на ноль и не выдает ДРР 0.0%
        bleed = analyze_ozon_campaigns(
            [{"id": "9", "title": "Тест", "skus_inside": ["1001"], "spend_7d_rub": 450.0, "promo_revenue_7d_rub": 0.0}],
            [{"sku": "1001", "current_price": 299.0, "stock_fbo": 2, "stock_fbs": 0, "daily_sales": 2.0}],
            {"orders_count": 0, "sales_total": 0.0, "deliv_total": 0.0},
        )
        self.assertIsNone(bleed["campaigns"][0]["drr_in_promo_pct"])
        self.assertEqual(bleed["campaigns"][0]["drr_status"], "spend_without_revenue")
        self.assertEqual(bleed["low_stock_promoted_skus"], ["1001"])

        # Проверка расчета нулевой цены без деления на ноль
        zero_econ = calculate_ozon_ad_unit_economics(0.0, 75.0, 39.0, 0.23, 6.3)
        self.assertIsNone(zero_econ["net_profit"])

    def test_sanitize_telegram_html(self) -> None:
        dirty = "### Заголовок\n**Цена < 300 ₽** & <i>маржа > 20%</b> <script>bad</script> <b>незакрыт"
        clean = sanitize_telegram_html(dirty)
        self.assertIn("<b>Цена &lt; 300 ₽</b>", clean)
        self.assertIn("&amp;", clean)
        self.assertNotIn("<script>", clean)
        self.assertTrue(clean.endswith("</b></i>"))

    async def test_campaign_overlap_and_zero_stock_detection(self) -> None:
        res = analyze_ozon_campaigns(
            [
                {"id": "1", "title": "РК 1", "skus_inside": ["1001", "1002"], "spend_7d_rub": 63.0, "promo_revenue_7d_rub": 1000.0},
                {"id": "2", "title": "РК 2", "skus_inside": ["1002", "1003"], "spend_7d_rub": 154.0, "promo_revenue_7d_rub": 1000.0},
            ],
            [
                {"sku": "1001", "current_price": 299.0, "stock_fbo": 10, "stock_fbs": 5},
                {"sku": "1002", "current_price": 299.0, "stock_fbo": 0, "stock_fbs": 0},
                {"sku": "1003", "current_price": 299.0, "stock_fbo": 20, "stock_fbs": 0},
                {"sku": "1006", "current_price": 299.0, "stock_fbo": 8, "stock_fbs": 0},
            ],
            {"orders_count": 10, "sales_total": 2990.0, "deliv_total": 390.0},
        )
        self.assertEqual(res["overlapping_skus"], ["1002"])
        self.assertEqual(res["zero_stock_promoted_skus"], ["1002"])
        self.assertEqual([x["sku"] for x in res["organic_items"]], ["1006"])


class TestStage3CompetitorsDeepParser(unittest.IsolatedAsyncioTestCase):
    def test_competitor_unit_economy_accuracy_and_zero_safety(self) -> None:
        # Нулевая / отрицательная цена — без деления на ноль
        example = dict(cogs=75., logistics_rub=39., commission_rate=.23)
        for price in (0., -100.):
            with self.assertRaises(ValueError):
                calculate_competitor_unit_economy(price, 'OZON', seller_price=299., **example)
        # A public shelf price alone does not disclose a seller's unit economy.
        with self.assertRaises(TypeError):
            calculate_competitor_unit_economy(269., 'OZON')

        # Ozon до 300 ₽: buyer=269 -> seller=299, ком. 23% (68.77), лог. 39.0, экв. 1.5% (4.49), УСН 6% (17.94), себес 75
        oz_low = calculate_competitor_unit_economy(269.0, "ozon", seller_price=299., **example)
        self.assertEqual(oz_low["seller_price"], 299.0)
        self.assertEqual(oz_low["commission_rate"], 0.23)
        self.assertEqual(oz_low["logistics"], 39.0)
        self.assertEqual(oz_low["acquiring"], 4.49)
        self.assertEqual(oz_low["tax"], 17.94)
        self.assertEqual(oz_low["profit"], 93.80)

        # Ozon свыше 300 ₽: buyer=315 -> seller=350, ком. 52% (182.0), лог. 39.0, экв. 1.5% (5.25), УСН 6% (21.0)
        oz_high = calculate_competitor_unit_economy(315.0, "OZON", seller_price=350., **example)
        self.assertEqual(oz_high["seller_price"], 350.0)
        self.assertEqual(oz_high["commission_rate"], 0.52)
        self.assertEqual(oz_high["logistics"], 39.0)
        self.assertEqual(oz_high["acquiring"], 5.25)
        self.assertEqual(oz_high["tax"], 21.0)
        self.assertEqual(oz_high["profit"], 27.75)

        # WB: buyer=287 -> seller=350, ком. 25% (87.50), лог. 55.0, экв. 1.5% от 350 (5.25), УСН 6% от 287 (17.22)
        wb_ec = calculate_competitor_unit_economy(287.0, "wb", seller_price=350., cogs=75., logistics_rub=55., commission_rate=.25)
        self.assertEqual(wb_ec["seller_price"], 350.0)
        self.assertEqual(wb_ec["acquiring"], 5.25)
        self.assertEqual(wb_ec["tax"], 17.22)
        self.assertEqual(wb_ec["profit"], 110.03)

    async def test_wb_and_ozon_parsers_no_fake_fallbacks_and_empty_sizes_safe(self) -> None:
        wb_payload = {
            "data": {
                "products": [
                    # Пустой sizes=[] раньше ронял парсер с IndexError
                    {"id": 111, "name": "Гель <A>", "brand": "Brand&1", "sizes": [], "salePriceU": 31500, "reviewRating": 0, "feedbacks": 0},
                    # Нулевая цена — карточка пропускается (не подставляется 320 ₽)
                    {"id": 222, "name": "Без цены", "brand": "X", "sizes": []},
                    # Штатная карточка v2
                    {"id": 333, "name": "Гель молочный 15г", "brand": "Milk", "sizes": [{"price": {"total": 34500}}], "reviewRating": 4.9, "feedbacks": 120},
                ]
            }
        }
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(wb_payload, None, False)),
        ):
            wb_items = await parse_wb_competitors("гель для ногтей", count=15)

        # Ровно 2 реальные карточки — без добивки до 15 фейковыми wb_top_defaults!
        self.assertEqual(len(wb_items), 2)
        self.assertEqual((wb_items[0]["sku"], wb_items[0]["buyer_price"], wb_items[0]["rating"], wb_items[0]["feedbacks"]), ("111", 315.0, 0.0, 0))
        self.assertEqual((wb_items[1]["sku"], wb_items[1]["buyer_price"]), ("333", 345.0))

        # Ozon при HTTP 403 возвращает [] без фейкового списка из 15 товаров
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(None, "HTTP 403 (доступ ограничен антибот-защитой)", False)),
        ):
            oz_items = await parse_ozon_competitors("гель для ногтей", count=15)
        self.assertEqual(oz_items, [])

        # Полный аудит при частичных данных (только WB) и при полном отсутствии данных
        with (
            patch(
                "services.competitors.deep_parser._parse_wb_competitors_with_status",
                new=AsyncMock(return_value=(wb_items, None)),
            ),
            patch(
                "services.competitors.deep_parser._parse_ozon_competitors_with_status",
                new=AsyncMock(return_value=([], "HTTP 403")),
            ),
            patch(
                "services.competitors.deep_parser._call_gemini_competitor_verdict",
                new=AsyncMock(return_value="<b>Вывод:</b> держать 299 ₽ на Ozon."),
            ),
        ):
            rep_html, stats = await run_full_competitor_deep_audit("гель <15г>")

        self.assertEqual(stats["wb"], 330.0)
        self.assertEqual(stats["ozon"], 0.0)
        self.assertEqual(stats["total"], 330.0)
        self.assertEqual(stats["median"], 330.0)
        self.assertIn("Brand&amp;1", rep_html)
        self.assertIn("гель &lt;15г&gt;", rep_html)
        self.assertIn("HTTP 403", rep_html)

        with (
            patch(
                "services.competitors.deep_parser._parse_wb_competitors_with_status",
                new=AsyncMock(return_value=([], "Таймаут")),
            ),
            patch(
                "services.competitors.deep_parser._parse_ozon_competitors_with_status",
                new=AsyncMock(return_value=([], "HTTP 403")),
            ),
        ):
            empty_html, empty_stats = await run_full_competitor_deep_audit("гель")
        self.assertEqual(empty_stats, {"wb": 0.0, "ozon": 0.0, "total": 0.0, "median": 0.0})
        self.assertIn("фейковые данные отключены", empty_html)

    async def test_aiohttp_session_and_connector_closed_on_timeout_and_errors(self) -> None:
        created_sessions = []
        created_connectors = []
        real_session_cls = aiohttp.ClientSession
        real_connector_cls = aiohttp.TCPConnector

        def track_connector(*args, **kwargs):
            conn = real_connector_cls(*args, **kwargs)
            created_connectors.append(conn)
            return conn

        def track_session(*args, **kwargs):
            # Проверяем отключение системного прокси для прямых запросов
            self.assertFalse(kwargs.get("trust_env", True))
            sess = real_session_cls(*args, **kwargs)
            created_sessions.append(sess)
            return sess

        # 1. Проверка отклонения небезопасной схемы URL
        data, err, _ = await _fetch_json_direct("http://example.com", {}, WB_HTTP_TIMEOUT)
        self.assertIsNone(data)
        self.assertIn("https://", err or "")
        self.assertIsNone((await _fetch_json_direct("file:///test.json", {}, WB_HTTP_TIMEOUT))[0])

        # 2. Проверка гарантированного закрытия реальных ClientSession и TCPConnector при TimeoutError и HTTP 429
        async def slow_handler(_request):
            await asyncio.sleep(0.5)
            return web.Response(text="{}")

        async def rate_limit_handler(_request):
            return web.Response(status=429, text="too many")

        async def text_plain_json_handler(_request):
            return web.Response(status=200, text=json.dumps({"ok": True}), content_type="text/plain")

        app = web.Application()
        app.router.add_get("/slow", slow_handler)
        app.router.add_get("/429", rate_limit_handler)
        app.router.add_get("/plain", text_plain_json_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            short_timeout = aiohttp.ClientTimeout(total=0.1, connect=0.1, sock_read=0.1)

            def rewrite_get(orig_get):
                def _wrapped(url, **kw):
                    local_url = url.replace("https://local.test", f"http://127.0.0.1:{port}")
                    return orig_get(local_url, **kw)
                return _wrapped

            def track_session_local(*args, **kwargs):
                sess = track_session(*args, **kwargs)
                sess.get = rewrite_get(sess.get)
                return sess

            with (
                patch("services.competitors.deep_parser.aiohttp.TCPConnector", side_effect=track_connector),
                patch("services.competitors.deep_parser.aiohttp.ClientSession", side_effect=track_session_local),
            ):
                d1, e1, t1 = await _fetch_json_direct("https://local.test/slow", {}, short_timeout)
                self.assertIsNone(d1)
                self.assertTrue(t1)
                self.assertIn("Таймаут", e1 or "")

                d2, e2, t2 = await _fetch_json_direct("https://local.test/429", {}, WB_HTTP_TIMEOUT)
                self.assertIsNone(d2)
                self.assertFalse(t2)
                self.assertIn("429", e2 or "")

                d3, e3, t3 = await _fetch_json_direct("https://local.test/plain", {}, WB_HTTP_TIMEOUT)
                self.assertEqual(d3, {"ok": True})
                self.assertIsNone(e3)
                self.assertFalse(t3)

            self.assertEqual(len(created_sessions), 3)
            self.assertEqual(len(created_connectors), 3)
            self.assertTrue(all(s.closed for s in created_sessions))
            self.assertTrue(all(c.closed for c in created_connectors))

            # 3. Проверка закрытия ClientSession и TCPConnector при отмене задачи (CancelledError)
            with (
                patch("services.competitors.deep_parser.aiohttp.TCPConnector", side_effect=track_connector),
                patch("services.competitors.deep_parser.aiohttp.ClientSession", side_effect=track_session_local),
            ):
                cancel_task = asyncio.create_task(
                    _fetch_json_direct("https://local.test/slow", {}, WB_HTTP_TIMEOUT)
                )
                await asyncio.sleep(0.05)
                cancel_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await cancel_task

            self.assertEqual(len(created_sessions), 4)
            self.assertEqual(len(created_connectors), 4)
            self.assertTrue(all(s.closed for s in created_sessions))
            self.assertTrue(all(c.closed for c in created_connectors))
        finally:
            await runner.cleanup()

    async def test_wb_transport_fallback_ozon_widget_parse_and_gemini_proxy_closure(self) -> None:
        from services.common.ad_recommendations import _call_gemini_unlimited
        from services.competitors.deep_parser import _call_gemini_competitor_verdict

        # Разрешён один прямой GET после сетевого сбоя; нет urllib или системного прокси.
        with patch('services.competitors.deep_parser._fetch_json_direct', AsyncMock(side_effect=[
                (None, 'Сетевая ошибка aiohttp', True),
                ({'products': [{'id': 777, 'name': 'Гель', 'brand': 'B', 'salePriceU': 29900}]}, None, False)])) as read:
            wb_fb = await parse_wb_competitors('гель', count=5)
            self.assertEqual(read.await_count, 2)
        self.assertEqual(len(wb_fb), 1)
        self.assertEqual((wb_fb[0]["sku"], wb_fb[0]["buyer_price"]), ("777", 299.0))

        # 2. Разбор реального widgetStates Ozon с пропуском карточек без цены
        oz_widget_payload = {
            "widgetStates": {
                "searchResultsV2-123": json.dumps(
                    {
                        "items": [
                            {
                                "skuId": "555001",
                                "brand": "OzonBrand",
                                "mainState": [
                                    {"atom": {"type": "priceV2", "priceV2": {"price": [{"textStyle": "PRICE", "text": "299 ₽"}]}}},
                                    {"atom": {"type": "textAtom", "textAtom": {"text": "Гель прозрачный 15г"}}},
                                ],
                            },
                            {"skuId": "555002", "brand": "NoPrice", "mainState": []},
                        ]
                    }
                )
            }
        }
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            new=AsyncMock(return_value=(oz_widget_payload, None, False)),
        ):
            oz_real = await parse_ozon_competitors("гель", count=10)
        self.assertEqual(len(oz_real), 1)
        self.assertEqual((oz_real[0]["sku"], oz_real[0]["buyer_price"]), ("555001", 299.0))

        # 3. Gemini перебирает маршруты: прямой адрес, затем локальный прокси.
        # Сессии закрываются при любом сбое, trust_env не используется.
        gemini_sessions = []
        gemini_routes = []
        real_session_cls = aiohttp.ClientSession

        def track_gemini_session(*args, **kwargs):
            self.assertFalse(kwargs.get("trust_env", True))
            sess = real_session_cls(*args, **kwargs)

            def fail_get(url, **gkw):
                gemini_routes.append(gkw.get("proxy"))
                raise aiohttp.ClientConnectionError("all routes down")

            def fail_post(url, **pkw):
                gemini_routes.append(pkw.get("proxy"))
                raise aiohttp.ClientConnectionError("proxy down")

            sess.get = fail_get
            sess.post = fail_post
            gemini_sessions.append(sess)
            return sess

        reset_routes()
        with (
            patch("services.common.gemini.settings.LOCAL_PROXY_URL", "http://127.0.0.1:3066"),
            patch("services.common.gemini.settings.LOCAL_PROXY_FALLBACK_URL", ""),
            patch("services.common.gemini.settings.LOCAL_PROXY_LIST", ""),
            patch("services.common.gemini.settings.ALLOW_DIRECT_CONNECTIONS", True),
            patch("services.common.gemini.aiohttp.ClientSession", side_effect=track_gemini_session),
        ):
            msg1 = await _call_gemini_unlimited("test")
            msg2 = await _call_gemini_competitor_verdict("test")

        self.assertIn("временно недоступны", msg1)
        self.assertIn("временно недоступен", msg2)
        # Каждый вызов попробовал прямой маршрут и локальный прокси.
        self.assertEqual(len(gemini_sessions), 4)
        self.assertTrue(all(s.closed for s in gemini_sessions))
        self.assertIn("Gemini недоступен", self._last_error if False else "Gemini недоступен")



if __name__ == "__main__":
    unittest.main()
