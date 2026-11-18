import json
import time
import asyncio
from datetime import datetime
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BotCommand, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import settings, LOGS_DIR
from database.models import init_db
from database.crud import (
    update_product_market_state,
    get_all_products,
    create_pending_action,
)
from services.ozon.seller_api import OzonSellerAPI, CircuitBreakerOpenError
from services.wb.statistics_api import WBStatisticsAPI
from services.common.financial_pnl import (
    generate_monthly_pnl_report,
    generate_weekly_top_and_unprofitable_report,
    get_previous_month_bounds,
)
from services.common.orders_report import sync_and_generate_orders_report
from services.common.ai_advisory_board import generate_monday_ai_board_report
from maintenance.api_inspector import run_monthly_api_audit
from products_catalog import PRODUCTS_CATALOG
from services.ozon.browser_reviews import auto_reply_unanswered_reviews, auto_reply_unanswered_questions
from services.common.price_auditor import run_daily_price_audit
from services.common.ad_recommendations import generate_weekly_ads_report_and_recommendations

from bot.keyboards import get_main_menu_keyboard
from bot.handlers.costs_fsm import costs_router, cmd_costs_start
from bot.handlers.logistics_fsm import logistics_router, cmd_first_mile_start
from bot.handlers.callbacks import callbacks_router, build_approval_keyboard
from bot.handlers.patch_fsm import patch_router
from bot.handlers.reviews_fsm import reviews_router
from bot.handlers.questions_fsm import questions_router

PATCH_REQUEST_FILE = LOGS_DIR / "patch_request.json"

session = AiohttpSession(proxy="http://127.0.0.1:3066")
bot = Bot(token=settings.TG_BOT_TOKEN, session=session)
dp = Dispatcher()

dp.include_router(costs_router)
dp.include_router(logistics_router)
dp.include_router(callbacks_router)
dp.include_router(patch_router)
dp.include_router(reviews_router)
dp.include_router(questions_router)


async def notify_admin(text: str) -> None:
    try:
        await bot.send_message(
            chat_id=settings.TG_ADMIN_ID,
            text=text,
            parse_mode="HTML",
            disable_notification=False,
        )
    except Exception as exc:
        print(f"[Worker] Ошибка отправки сообщения в TG: {exc}", flush=True)


ozon_service = OzonSellerAPI(notify_callback=notify_admin)
wb_service = WBStatisticsAPI(notify_callback=notify_admin)


async def write_heartbeat() -> None:
    payload = {
        "timestamp": time.time(),
        "iso_time": datetime.utcnow().isoformat(),
        "ozon_degraded": ozon_service.is_degraded,
        "wb_degraded": wb_service.is_degraded,
    }
    settings.HEARTBEAT_FILE.write_text(json.dumps(payload), encoding="utf-8")


async def sync_marketplace_silent(mp_name: str) -> List[Dict[str, Any]]:
    try:
        if mp_name == "ozon":
            items = await ozon_service.fetch_stocks_and_prices()
        else:
            items = await wb_service.fetch_stocks_and_prices()
    except CircuitBreakerOpenError:
        return []
    except Exception as exc:
        print(f"[Silent Sync {mp_name.upper()} Error]: {exc}", flush=True)
        return []

    for item in items:
        await update_product_market_state(
            sku=item["sku"],
            marketplace=mp_name,
            external_id=item["external_id"],
            current_price=item["current_price"],
            stock_fbo=item["stock_fbo"],
            in_promo=item["in_promo"],
            promo_max_price=item["promo_max_price"],
            promo_id=item["promo_id"],
        )
    return items


async def scheduled_marketplaces_poll() -> None:
    await asyncio.gather(
        sync_marketplace_silent("ozon"),
        sync_marketplace_silent("wb"),
        return_exceptions=True,
    )

async def daily_18h_price_and_ads_digest() -> None:
    """Ежедневная сверка цен в 18:00 МСК с учетом цены покупателя и налога УСН 6%."""
    ozon_items, wb_items = await asyncio.gather(
        sync_marketplace_silent("ozon"),
        sync_marketplace_silent("wb"),
        return_exceptions=True,
    )

    live_data: Dict[str, Dict[str, Any]] = {}
    if isinstance(ozon_items, list):
        for it in ozon_items:
            live_data[f"ozon_{it['sku']}"] = it
    if isinstance(wb_items, list):
        for it in wb_items:
            live_data[f"wb_{it['sku']}"] = it

    recommendations_sent = 0

    for mp_name in ("ozon", "wb"):
        products = await get_all_products(marketplace=mp_name)
        for prod in products:
            cogs_unit = prod.cost_price + prod.packaging_cost + prod.first_mile_unit_cost
            if cogs_unit <= 0:
                continue

            live = live_data.get(f"{mp_name}_{prod.sku}", {})
            seller_price = float(live.get("current_price") or prod.current_price or 0.0)
            if seller_price <= 0:
                continue

            buyer_price = float(live.get("buyer_price") or seller_price)
            comm_rate = float(live.get("commission_rate") or 0.0)
            acquiring_rub = float(live.get("acquiring_rub") or 0.0)
            logistics_rub = float(live.get("fbo_logistics_cost") or 0.0)
            in_promo = bool(live.get("in_promo") or prod.in_promo)

            comm_rub = seller_price * comm_rate
            usn_tax_rub = buyer_price * 0.06

            total_unit_expenses = cogs_unit + comm_rub + acquiring_rub + logistics_rub + usn_tax_rub
            actual_net_profit = seller_price - total_unit_expenses
            target_profit = prod.target_margin_rub if prod.target_margin_rub > 0 else 80.0

            discount_ratio = (buyer_price / seller_price) if seller_price > 0 else 1.0
            denom = 1.0 - comm_rate - (0.06 * discount_ratio)
            if denom <= 0.2:
                denom = 0.6
            recommended_price = round((cogs_unit + logistics_rub + acquiring_rub + target_profit) / denom)

            # Обход ловушки Ozon 301–450 ₽
            if mp_name == "ozon" and 301 <= recommended_price <= 450:
                recommended_price = 299

            if abs(recommended_price - seller_price) < 5 and actual_net_profit >= target_profit:
                continue

            cat_info = PRODUCTS_CATALOG.get(prod.sku, {})
            prod_name = cat_info.get("name") or prod.title or f"Артикул {prod.sku}"
            promo_status = "Участвует в акции МП" if in_promo else "Не в акции"

            action_label = f"Установить цену {recommended_price} ₽"
            payload_data = {
                "product_id": prod.external_id,
                "offer_id": prod.sku,
                "nm_id": prod.external_id,
                "new_price": recommended_price,
            }

            pending = await create_pending_action(
                marketplace=mp_name,
                sku=prod.sku,
                action_type="SET_PRICE",
                payload=payload_data,
            )

            msg_text = (
                f"📊 <b>Расчет цены [{mp_name.upper()}] — {prod_name} (Арт: <code>{prod.sku}</code>)</b>\n"
                f"Статус акций: <i>{promo_status}</i> | Остаток: <b>{prod.stock_fbo} шт.</b>\n\n"
                f"💵 <b>Цены и налог УСН 6%:</b>\n"
                f"• Ваша цена в ЛК: <b>{seller_price:.0f} ₽</b>\n"
                f"• Цена для покупателя (со скидкой МП): <b>{buyer_price:.0f} ₽</b>\n"
                f"• Налог УСН 6% (с {buyer_price:.0f} ₽): <b>{usn_tax_rub:.2f} ₽</b>\n\n"
                f"📉 <b>Фактические затраты из API МП на 1 шт.:</b>\n"
                f"├ Себестоимость + упаковка: <b>{cogs_unit:.2f} ₽</b>\n"
                f"├ Комиссия МП ({comm_rate * 100:.1f}%): <b>{comm_rub:.2f} ₽</b>\n"
                f"├ Логистика (по данным API): <b>{logistics_rub:.2f} ₽</b>\n"
                f"└ Эквайринг: <b>{acquiring_rub:.2f} ₽</b>\n"
                f"👉 <b>Текущая чистая прибыль:</b> <code>{actual_net_profit:.2f} ₽/шт.</code> (цель: {target_profit:.0f} ₽)\n\n"
                f"🎯 <b>Рекомендация:</b> <b>{action_label}</b>"
            )

            kb = build_approval_keyboard(pending.id, custom_approve_text=f"✅ {action_label}")
            await bot.send_message(
                chat_id=settings.TG_ADMIN_ID,
                text=msg_text,
                reply_markup=kb,
                parse_mode="HTML",
            )
            recommendations_sent += 1

    if recommendations_sent == 0:
        await notify_admin(
            "✅ <b>Проверка цен завершена:</b> все товары с заполненной себестоимостью соответствуют целевой прибыли."
        )


async def scheduled_1st_day_pnl_notice() -> None:
    bounds = get_previous_month_bounds()
    await notify_admin(
        f"📅 <b>Финансовый календарь ({bounds['title']}):</b>\n"
        f"Ozon и Wildberries завершают формирование закрывающих актов и детализации начислений за {bounds['title']}.\n"
        f"Итоговый месячный P&L отчет со всеми опубликованными расходами придет автоматически <b>5-го числа в 09:30</b> "
        f"(либо вы можете запросить предварительный срез кнопкой «📊 P&L отчет (Месяц)»)."
    )


async def scheduled_5th_day_monthly_pnl() -> None:
    report_html = await generate_monthly_pnl_report(ozon_service, wb_service)
    await bot.send_message(chat_id=settings.TG_ADMIN_ID, text=report_html, parse_mode="HTML")


async def scheduled_monday_top5_report() -> None:
    report_html = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
    await bot.send_message(chat_id=settings.TG_ADMIN_ID, text=report_html, parse_mode="HTML")


async def scheduled_monday_evening_ai_board() -> None:
    summary_text, file_path = await generate_monday_ai_board_report(ozon_service, wb_service)
    await bot.send_message(chat_id=settings.TG_ADMIN_ID, text=summary_text)
    await bot.send_document(
        chat_id=settings.TG_ADMIN_ID,
        document=FSInputFile(str(file_path)),
        caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
    )


async def scheduled_monthly_audit() -> None:
    audit_html = await run_monthly_api_audit()
    await bot.send_message(chat_id=settings.TG_ADMIN_ID, text=audit_html, parse_mode="HTML")


# --- Хэндлеры текстовых команд и экранных кнопок ---

@dp.message(F.text.in_({"/start", "/menu"}))
async def cmd_start_menu(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    await message.answer(
        "👋 Панель управления магазинами Ozon и Wildberries активна.\n"
        "Используйте кнопки меню внизу экрана для быстрого доступа:",
        reply_markup=get_main_menu_keyboard(),
    )


@dp.message(F.text.in_({"📊 P&L отчет (Месяц)", "📊 P&L отчет"}))
@dp.message(Command("pnl"))
async def cmd_manual_pnl(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Собираю финансовый P&L отчет по итогам прошлого месяца из Ozon и WB...")
    try:
        report = await generate_monthly_pnl_report(ozon_service, wb_service)
        await status_msg.delete()
        await message.answer(report, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка P&L отчета:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "🏆 ТОП-5 и Убыточные")
@dp.message(Command("top5"))
async def cmd_top5_report(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Анализирую поштучную рентабельность товаров за 7 дней...")
    try:
        report = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        await message.answer(report, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка расчета ТОП-5:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "🧠 ИИ-Советник (Разбор)")
@dp.message(Command("ai_board"))
async def cmd_ai_board(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer(
        "🧠 Созываю Совет из 5 ИИ-специалистов (Операционист, Скептик, Маркетолог, Финансист, Директор по развитию)...\n"
        "<i>Анализ реальных отчетов Ozon и WB займет около 20–30 секунд.</i>",
        parse_mode="HTML",
    )
    try:
        summary_text, file_path = await generate_monday_ai_board_report(ozon_service, wb_service)
        await status_msg.delete()
        await message.answer(summary_text)
        await message.answer_document(
            document=FSInputFile(str(file_path)),
            caption="📎 Полный стратегический отчет Совета ИИ-специалистов (откройте в браузере)",
        )
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка ИИ-Советника:\n<code>{exc}</code>", parse_mode="HTML")


@dp.message(F.text == "📦 Отчет по заказам")
@dp.message(Command("orders_pnl"))
async def cmd_orders_report(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Запрашиваю заказы и финансовые начисления из Ozon и WB за 7 дней...")
    try:
        report_text = await sync_and_generate_orders_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        await message.answer(report_text, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(
            f"❌ <b>Ошибка при формировании отчета:</b>\n<code>{type(exc).__name__}: {exc}</code>",
            parse_mode="HTML",
        )


@dp.message(F.text == "🔍 Аудит API")
@dp.message(Command("audit"))
async def cmd_manual_audit(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    audit = await run_monthly_api_audit()
    await message.answer(audit, parse_mode="HTML")


@dp.message(F.text == "📦 Себестоимость (поштучно)")
async def btn_costs_entry(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_costs_start(message, state)


@dp.message(F.text == "🚚 Первая миля")
async def btn_first_mile_entry(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_first_mile_start(message, state)


@dp.message(F.text == "🔄 Синхронизировать МП")
async def btn_sync_mp(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    await message.answer("⏳ Запущена синхронизация остатков, цен и расчет рекомендаций...")
    await daily_18h_price_and_ads_digest()
    await message.answer("✅ Синхронизация и проверка цен завершены.")


@dp.message(Command("patch"))
async def cmd_patch_cli(message: Message, command: CommandObject) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/patch [имя_файла] описание изменений</code>",
            parse_mode="HTML",
        )
        return

    PATCH_REQUEST_FILE.write_text(
        json.dumps({"instruction": command.args.strip()}, ensure_ascii=False),
        encoding="utf-8",
    )
    await message.answer(
        "🛠 Запрос передан в <code>supervisor.py</code>. "
        "Создается резервная копия, генерируется код и проверяется синтаксис...",
        parse_mode="HTML",
    )



@dp.message(F.text == "🔎 Пробив конкурентов")
@dp.message(Command("competitors"))
async def cmd_competitors_audit(message: Message):
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    wait_msg = await message.answer(
        "🔎 <b>Запущен сбор данных конкурентов WB и Ozon...</b>\n"
        "<i>Парсинг 20–30 лидеров, расчет юнит-экономики и аудит болей займет 15–20 секунд.</i>",
        parse_mode="HTML"
    )
    try:
        from services.competitors.deep_parser import run_full_competitor_deep_audit
        report_text, avg_price = await run_full_competitor_deep_audit("гель для моделирования ногтей 15г")
        await wait_msg.delete()
        for chunk in [report_text[i:i+3900] for i in range(0, len(report_text), 3900)]:
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка парсинга конкурентов: <code>{exc}</code>", parse_mode="HTML")


async def setup_bot_commands(bot_instance: Bot) -> None:
    commands = [
        BotCommand(command="menu", description="Главное меню с кнопками"),
        BotCommand(command="pnl", description="P&L отчет за прошлый месяц"),
        BotCommand(command="top5", description="ТОП-5 популярных и убыточных товаров"),
        BotCommand(command="ai_board", description="Разбор магазина от ИИ-Совета"),
        BotCommand(command="question", description="Ответить на вопрос покупателя"),
        BotCommand(command="orders_pnl", description="Отчет по заказам за 7 дней"),
        BotCommand(command="review", description="Сгенерировать ответ на отзыв"),
        BotCommand(command="costs", description="Себестоимость товара"),
        BotCommand(command="first_mile", description="Затраты первой мили"),
        BotCommand(command="audit", description="Аудит API Ozon, WB и ИИ"),
    ]
    await bot_instance.set_my_commands(commands)


async def scheduled_night_self_heal() -> None:
    """Тихая ночная самопроверка в 04:30. Присылает алерт только если были проблемы."""
    res = await run_full_self_check_and_heal()
    if res["total_issues"] > 0:
        details_txt = "\n".join(res["details"])
        await notify_admin(
            f"🛠 <b>[Автономная самопроверка 04:30]</b>\n"
            f"Обнаружено ошибок: <b>{res['total_issues']}</b>\n"
            f"Успешно исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}"
        )


@dp.message(F.text == "🛠 Самопроверка и автопочинка")
@dp.message(Command("heal"))
async def cmd_manual_self_heal(message: Message) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    wait_msg = await message.answer("🔍 Запущена комплексная самопроверка всех модулей системы...")
    res = await run_full_self_check_and_heal()
    await wait_msg.delete()

    if res["total_issues"] == 0:
        await message.answer("✅ <b>Все модули работают исправно:</b> ошибок не обнаружено, вмешательство ИИ не требуется.")
    else:
        details_txt = "\n".join(res["details"])
        await message.answer(
            f"🛠 <b>Результаты самодиагностики и починки:</b>\n\n"
            f"Обнаружено сбоев: <b>{res['total_issues']}</b>\n"
            f"Исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}",
            parse_mode="HTML"
        )


async def scheduled_reviews_and_questions_job() -> None:
    """Запускается сразу при старте и далее каждые 30 минут: отвечает на вопросы и отзывы с 13.11.2026."""
    await auto_reply_unanswered_questions(headless=True, dry_run=False, max_per_run=15)
    await auto_reply_unanswered_reviews(headless=True, dry_run=False, max_per_run=15)


async def scheduled_daily_price_audit_job() -> None:
    """Ежедневный аудит цен Ozon (порог 299 ₽ и >300 ₽ с 52%) и WB (FBO+FBS)."""
    await run_daily_price_audit(bot, settings.TG_ADMIN_ID, OzonSellerAPI(), WBStatisticsAPI())


async def scheduled_weekly_ads_job() -> None:
    """Еженедельный отчет и рекомендации по внутренней рекламе Ozon и WB."""
    report = await generate_weekly_ads_report_and_recommendations(OzonSellerAPI(), WBStatisticsAPI())
    await bot.send_message(
        settings.TG_ADMIN_ID,
        f"📈 <b>Еженедельный аудит и план рекламы Ozon & WB</b>\n\n{report}",
        parse_mode="HTML",
    )


async def main() -> None:
    await init_db()
    await write_heartbeat()
    await setup_bot_commands(bot)

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(write_heartbeat, IntervalTrigger(seconds=10), id="heartbeat_job")
    scheduler.add_job(scheduled_marketplaces_poll, IntervalTrigger(minutes=30), id="mp_poll_job")

    # Отзывы и вопросы Ozon с 13.11.2026 — запуск сразу при старте и далее каждые 30 минут
    scheduler.add_job(
        scheduled_reviews_and_questions_job,
        IntervalTrigger(minutes=30),
        next_run_time=datetime.now(),
        id="ozon_rev_q_30m",
        replace_existing=True,
    )
    # Ежедневный аудит цен Ozon и WB в 10:00
    scheduler.add_job(
        scheduled_daily_price_audit_job,
        CronTrigger(hour=10, minute=0),
        id="daily_price_audit_job",
        replace_existing=True,
    )
    # Еженедельный отчет и рекомендации по рекламе в понедельник в 10:30
    scheduler.add_job(
        scheduled_weekly_ads_job,
        CronTrigger(day_of_week="mon", hour=10, minute=30),
        id="weekly_ads_audit_job",
        replace_existing=True,
    )
    scheduler.add_job(
        daily_18h_price_and_ads_digest,
        CronTrigger(hour=18, minute=0),
        id="daily_18h_digest_job",
    )
    scheduler.add_job(
        scheduled_1st_day_pnl_notice,
        CronTrigger(day=1, hour=9, minute=0),
        id="monthly_pnl_notice_1st",
    )
    scheduler.add_job(
        scheduled_5th_day_monthly_pnl,
        CronTrigger(day=5, hour=9, minute=30),
        id="monthly_pnl_final_5th",
    )
    scheduler.add_job(
        scheduled_monday_top5_report,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_top5_monday_morning",
    )
    scheduler.add_job(
        scheduled_monday_evening_ai_board,
        CronTrigger(day_of_week="mon", hour=19, minute=0),
        id="weekly_ai_board_monday_evening",
    )
    scheduler.add_job(
        scheduled_monthly_audit,
        CronTrigger(day=1, hour=9, minute=5),
        id="monthly_audit_job",
    )
    # Автономная ночная самопроверка и лечение модулей в 04:30
    scheduler.add_job(
        scheduled_night_self_heal,
        CronTrigger(hour=4, minute=30),
        id="night_self_heal_job",
    )
    scheduler.start()

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=False)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())