import asyncio
import functools
import html
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.methods import GetUpdates
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import LOGS_DIR, prepare_runtime_dirs, settings
from services.common.routes import proxy_routes, remember_route
from maintenance.supervisor import apply_code_patch, queue_if_supervised
from database.crud import bulk_update_product_market_state, recover_interrupted_actions
from database.models import close_db, init_db
from maintenance.api_inspector import run_monthly_api_audit
from maintenance.self_healer import run_full_self_check_and_heal
from services.common.ad_recommendations import generate_weekly_ads_report_and_recommendations
from services.common.ai_advisory_board import generate_monday_ai_board_report
from services.common.financial_pnl import (
    generate_monthly_pnl_report,
    generate_weekly_top_and_unprofitable_report,
    get_previous_month_bounds,
)
from services.common.orders_report import sync_and_generate_orders_report
from services.common.price_auditor import run_daily_price_audit
from services.ozon.browser_reviews import (
    auto_reply_unanswered_questions,
    auto_reply_unanswered_reviews,
)
from services.common.market_api import CircuitBreakerOpenError
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

from bot.handlers.callbacks import callbacks_router
from bot.handlers.costs_fsm import cmd_costs_start, costs_router
from bot.handlers.logistics_fsm import cmd_first_mile_start, logistics_router
from bot.handlers.patch_fsm import patch_router
from bot.handlers.questions_fsm import questions_router
from bot.handlers.reviews_fsm import reviews_router
from bot.keyboards import get_main_menu_keyboard


def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог при скрытом запуске через pythonw.exe (start_hidden.vbs)."""
    log_path = LOGS_DIR / "worker.log"
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)


MSK_TZ = ZoneInfo(settings.TIMEZONE)

_WORKER_LOCK_SOCKET: Optional[socket.socket] = None
_BROWSER_JOB_LOCK = asyncio.Lock()
_PRICE_AUDIT_LOCK = asyncio.Lock()


bot: Optional[Bot] = None  # initialized after acquiring the process lock
dp = Dispatcher()

dp.include_router(costs_router)
dp.include_router(logistics_router)
dp.include_router(callbacks_router)
dp.include_router(patch_router)
dp.include_router(reviews_router)
dp.include_router(questions_router)


async def ensure_working_telegram_route() -> None:
    global bot
    for proxy in proxy_routes('telegram'):
        candidate = Bot(token=settings.TG_BOT_TOKEN, session=AiohttpSession(proxy=proxy, timeout=45))
        try:
            await candidate.get_me()
            remember_route('telegram', proxy)
        except (TelegramAPIError, asyncio.TimeoutError, OSError):
            await candidate.session.close()
            continue
        except BaseException:
            await candidate.session.close()
            raise
        bot = candidate
        return
    raise RuntimeError('Telegram недоступен через настроенный локальный прокси')


def _split_html_chunks(text: str, limit: int = 3900) -> List[str]:
    """Keep entities and formatting intact, even across one long AI-generated line."""
    if len(text.encode('utf-16-le')) // 2 <= limit:
        return [text]
    chunks, parts, tags = [], [], []
    used = 0
    for match in re.finditer(r"</?[a-zA-Z][^<>]*>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|.", text, re.S):
        token = match[0]
        tag = re.fullmatch(r"<(/?)([a-zA-Z][\w-]*)[^<>]*>", token)
        if tag:
            parts.append(token)
            if tag[1]:
                if tags and tag[2] == tags[-1][0]:
                    tags.pop()
            elif not token.endswith('/>'):
                tags.append((tag[2], token))
            continue
        size = len(html.unescape(token).encode('utf-16-le')) // 2
        if used and used + size > limit:
            chunks.append(''.join(parts) + ''.join(f'</{name}>' for name, _ in reversed(tags)))
            parts = [opening for _, opening in tags]
            used = 0
        parts.append(token)
        used += size
    if used:
        chunks.append(''.join(parts))
    return chunks


async def send_admin_html(text: str, disable_notification: bool = False) -> None:
    for chunk in _split_html_chunks(text):
        await bot.send_message(
            chat_id=settings.TG_ADMIN_ID,
            text=chunk,
            parse_mode="HTML",
            disable_notification=disable_notification,
        )


async def notify_admin(text: str) -> None:
    try:
        await send_admin_html(text, disable_notification=False)
    except Exception as exc:
        print(f"[Worker] Ошибка отправки сообщения в TG: {exc}", flush=True)


ozon_service = OzonSellerAPI(notify_callback=notify_admin)
wb_service = WBStatisticsAPI(notify_callback=notify_admin)


def _write_heartbeat_sync(payload_str: str) -> None:
    tmp_file = settings.HEARTBEAT_FILE.with_suffix(".tmp")
    tmp_file.write_text(payload_str, encoding="utf-8")
    tmp_file.replace(settings.HEARTBEAT_FILE)


async def write_heartbeat() -> None:
    payload = {
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "pid": os.getpid(),
        "ozon_degraded": ozon_service.is_degraded,
        "wb_degraded": wb_service.is_degraded,
    }
    try:
        await asyncio.to_thread(_write_heartbeat_sync, json.dumps(payload))
    except Exception as exc:
        print(f"[Worker] Ошибка записи heartbeat: {exc}", flush=True)


async def _heartbeat_loop(polling_ready: asyncio.Event) -> None:
    # A running heartbeat is a readiness signal, not just proof the event loop exists.
    await polling_ready.wait()
    while True:
        await write_heartbeat()
        await asyncio.sleep(10)


def acquire_worker_single_instance_lock() -> bool:
    """Захватывает эксклюзивный TCP-сокет, исключая параллельный запуск двух worker.py и TelegramConflictError."""
    global _WORKER_LOCK_SOCKET
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE"), 1)
    try:
        sock.bind(("127.0.0.1", settings.WORKER_LOCK_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False

    _WORKER_LOCK_SOCKET = sock
    try:
        settings.WORKER_PID_FILE.write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        release_worker_single_instance_lock()
        raise
    return True


def release_worker_single_instance_lock() -> None:
    global _WORKER_LOCK_SOCKET
    if _WORKER_LOCK_SOCKET is not None:
        try:
            settings.WORKER_PID_FILE.unlink(missing_ok=True)
        finally:
            _WORKER_LOCK_SOCKET.close()
            _WORKER_LOCK_SOCKET = None


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

    if items:
        try:
            await bulk_update_product_market_state(marketplace=mp_name, items=items)
        except Exception as exc:
            print(f"[DB Sync {mp_name.upper()} Error]: {exc}", flush=True)
    return items


async def scheduled_marketplaces_poll() -> None:
    await asyncio.gather(
        sync_marketplace_silent("ozon"),
        sync_marketplace_silent("wb"),
        return_exceptions=True,
    )


async def scheduled_reviews_and_questions_job() -> None:
    """Запуск при старте и каждые 30 минут, один persistent-контекст за раз."""
    if _BROWSER_JOB_LOCK.locked():
        print("[Worker] Предыдущий цикл Playwright еще выполняется, пропуск дублирующего запуска.", flush=True)
        return

    async with _BROWSER_JOB_LOCK:
        for job in (auto_reply_unanswered_reviews, auto_reply_unanswered_questions):
            try:
                result = await job(headless=True, dry_run=settings.OZON_REPLY_DRY_RUN, max_per_run=15)
                if result['status'] not in {'success', 'limit_reached'}:
                    await notify_admin(html.escape(str(result['message'])))
            except Exception as exc:
                await notify_admin(f'Ozon {job.__name__}: {html.escape(str(exc))}')


async def scheduled_daily_price_audit_job() -> None:
    """Ежедневный аудит цен Ozon (3 кнопки: порог 299 ₽, >300 ₽ с 52%, Отклонить) и WB (FBO+FBS по числовому nmID)."""
    if _PRICE_AUDIT_LOCK.locked():
        return
    async with _PRICE_AUDIT_LOCK:
        await scheduled_marketplaces_poll()
        await run_daily_price_audit(bot, settings.TG_ADMIN_ID, ozon_service, wb_service)


async def scheduled_weekly_ads_job() -> None:
    """Еженедельный отчет и рекомендации по внутренней рекламе Ozon (2 гибридные РК) и WB."""
    report = await generate_weekly_ads_report_and_recommendations(ozon_service, wb_service)
    await send_admin_html(f"📈 <b>Еженедельный аудит и план рекламы Ozon & WB</b>\n\n{report}")


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
    await send_admin_html(report_html)


async def scheduled_monday_top5_report() -> None:
    report_html = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
    await send_admin_html(report_html)


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
    await send_admin_html(audit_html)


async def scheduled_night_self_heal() -> None:
    """Тихая ночная самопроверка в 04:30. Присылает алерт только если были проблемы."""
    if queue_if_supervised("night"):
        return
    res = await run_full_self_check_and_heal()
    if res["total_issues"] > 0:
        details_txt = "\n".join(res["details"])
        await notify_admin(
            f"🛠 <b>[Автономная самопроверка 04:30]</b>\n"
            f"Обнаружено ошибок: <b>{res['total_issues']}</b>\n"
            f"Успешно исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}"
        )


# --- Хэндлеры текстовых команд и экранных кнопок ---


@dp.message(F.text.in_({"/start", "/menu"}))
async def cmd_start_menu(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await state.clear()
    await message.answer(
        "👋 Панель управления магазинами Ozon и Wildberries активна.\n"
        "Используйте кнопки меню внизу экрана для быстрого доступа:",
        reply_markup=get_main_menu_keyboard(),
    )


@dp.message(F.text.in_({"📊 P&L отчет (Месяц)", "📊 P&L отчет"}))
@dp.message(Command("pnl"))
async def cmd_manual_pnl(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Собираю финансовый P&L отчет по итогам прошлого месяца из Ozon и WB...")
    try:
        report = await generate_monthly_pnl_report(ozon_service, wb_service)
        await status_msg.delete()
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка P&L отчета:\n<code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@dp.message(F.text == "🏆 ТОП-5 и Убыточные")
@dp.message(Command("top5"))
async def cmd_top5_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Анализирую поштучную рентабельность товаров за 7 дней...")
    try:
        report = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        for chunk in _split_html_chunks(report):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(f"❌ Ошибка расчета ТОП-5:\n<code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@dp.message(F.text == "🧠 ИИ-Советник (Разбор)")
@dp.message(Command("ai_board"))
async def cmd_ai_board(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
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
        await status_msg.edit_text(f"❌ Ошибка ИИ-Советника:\n<code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@dp.message(F.text == "📦 Отчет по заказам")
@dp.message(Command("orders_pnl"))
async def cmd_orders_report(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Запрашиваю заказы и финансовые начисления из Ozon и WB за 7 дней...")
    try:
        report_text = await sync_and_generate_orders_report(ozon_service, wb_service, days=7)
        await status_msg.delete()
        for chunk in _split_html_chunks(report_text):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await status_msg.edit_text(
            f"❌ <b>Ошибка при формировании отчета:</b>\n<code>{type(exc).__name__}: {html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )


@dp.message(F.text == "🔍 Аудит API")
@dp.message(Command("audit"))
async def cmd_manual_audit(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer("⏳ Проверяю маршруты Ozon, WB и Gemini...")
    try:
        audit = await run_monthly_api_audit()
        await status_msg.delete()
        for chunk in _split_html_chunks(audit):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001 — отчёт по кнопке не должен молчать
        await status_msg.edit_text(
            f"❌ Ошибка аудита API:\n<code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )


@dp.message(F.text == "📦 Себестоимость (поштучно)")
async def btn_costs_entry(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_costs_start(message, state)


@dp.message(F.text == "🚚 Первая миля")
async def btn_first_mile_entry(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    await cmd_first_mile_start(message, state)


@dp.message(F.text == "🔄 Синхронизировать МП")
async def btn_sync_mp(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    status_msg = await message.answer(
        "⏳ Запущена синхронизация остатков FBO/FBS и эталонный аудит цен Ozon & WB..."
    )
    try:
        await scheduled_daily_price_audit_job()
    except Exception as exc:  # noqa: BLE001
        await status_msg.edit_text(
            f"❌ Ошибка синхронизации:\n<code>{html.escape(str(exc))}</code>\n"
            f"<i>Остатки и цены остались прежними; ночная самопроверка повторит попытку.</i>",
            parse_mode="HTML",
        )
        return
    await status_msg.edit_text("✅ Синхронизация остатков и аудит цен завершены.")


@dp.message(Command("patch"))
async def cmd_patch_cli(message: Message, command: CommandObject) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/patch [имя_файла] описание изменений</code>",
            parse_mode="HTML",
        )
        return

    if queue_if_supervised('patch', command.args):
        await message.answer('Запрос принят супервизором. Результат придёт отдельным сообщением.')
        return
    ok, detail = await apply_code_patch(command.args)
    await message.answer(('✅ ' if ok else '❌ ') + detail)


@dp.message(F.text == "🔎 Пробив конкурентов")
@dp.message(Command("competitors"))
async def cmd_competitors_audit(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    wait_msg = await message.answer(
        "🔎 <b>Запущен сбор данных конкурентов WB и Ozon...</b>\n"
        "<i>Парсинг 20–30 лидеров, расчет юнит-экономики и аудит болей займет 15–20 секунд.</i>",
        parse_mode="HTML",
    )
    try:
        from services.competitors.deep_parser import run_full_competitor_deep_audit

        report_text, _ = await run_full_competitor_deep_audit("гель для моделирования ногтей 15г")
        await wait_msg.delete()
        for chunk in _split_html_chunks(report_text):
            await message.answer(chunk, parse_mode="HTML")
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка парсинга конкурентов: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


@dp.message(F.text == "🛠 Самопроверка и автопочинка")
@dp.message(Command("heal"))
async def cmd_manual_self_heal(message: Message) -> None:
    if not message.from_user or message.from_user.id != settings.TG_ADMIN_ID:
        return
    if queue_if_supervised('heal'):
        await message.answer('Самопроверка передана супервизору; отчёт придёт сюда.')
        return
    wait_msg = await message.answer("🔍 Запущена комплексная самопроверка всех модулей системы...")
    try:
        res = await run_full_self_check_and_heal()
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Ошибка самодиагностики: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return

    await wait_msg.delete()
    if res["total_issues"] == 0:
        await message.answer(
            "✅ <b>Все модули работают исправно:</b> ошибок не обнаружено, вмешательство ИИ не требуется.",
            parse_mode="HTML",
        )
    else:
        details_txt = "\n".join(res["details"])
        await message.answer(
            f"🛠 <b>Результаты самодиагностики и починки:</b>\n\n"
            f"Обнаружено сбоев: <b>{res['total_issues']}</b>\n"
            f"Исправлено: <b>{res['healed_count']}</b>\n\n"
            f"{details_txt}",
            parse_mode="HTML",
        )


async def setup_bot_commands(bot_instance: Bot) -> None:
    commands = [
        BotCommand(command="menu", description="Главное меню с кнопками"),
        BotCommand(command="pnl", description="P&L отчет за прошлый месяц"),
        BotCommand(command="orders_pnl", description="Отчет по заказам за 7 дней"),
        BotCommand(command="top5", description="ТОП-5 популярных и убыточных товаров"),
        BotCommand(command="ai_board", description="Разбор магазина от ИИ-Совета"),
        BotCommand(command="competitors", description="Глубокий аудит конкурентов"),
        BotCommand(command="question", description="Ответить на вопрос покупателя"),
        BotCommand(command="review", description="Сгенерировать ответ на отзыв"),
        BotCommand(command="costs", description="Себестоимость товара (поштучно)"),
        BotCommand(command="bulk_costs", description="Массовая загрузка себестоимости"),
        BotCommand(command="first_mile", description="Затраты первой мили"),
        BotCommand(command="audit", description="Аудит API Ozon, WB и ИИ"),
        BotCommand(command="heal", description="Самопроверка и автопочинка"),
    ]
    await bot_instance.set_my_commands(commands)


async def _guarded_job(name: str, job) -> None:
    """Обёртка фоновых задач: сбой одной задачи не теряется молча.

    APScheduler при необработанном исключении только пишет в свой лог, а
    скрытый запуск через pythonw.exe лога не видит. Поэтому любая ошибка
    уходит админу в Telegram, а планировщик продолжает работать.
    """
    try:
        result = await job()
        if isinstance(result, str) and result.strip():
            await send_admin_html(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — фоновая задача не должна убивать планировщик
        print(f"[Scheduler {name} Error]: {exc}", flush=True)
        try:
            await notify_admin(
                f"⚠️ <b>Фоновая задача {html.escape(name)} завершилась ошибкой:</b>\n"
                f"<code>{html.escape(str(exc)[:900])}</code>\n"
                f"<i>Планировщик работает, остальные задачи не затронуты. "
                f"Ночная самопроверка в 04:30 повторит восстановление.</i>"
            )
        except Exception as notify_exc:  # noqa: BLE001 — Telegram может быть недоступен
            print(f"[Scheduler {name} Notify Error]: {notify_exc}", flush=True)


def configure_scheduler() -> AsyncIOScheduler:
    """Регистрирует все фоновые задачи без дублей с защитой от параллельного наслоения (max_instances=1)."""
    scheduler = AsyncIOScheduler(
        timezone=MSK_TZ,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 600,
        },
    )
    now_msk = datetime.now(MSK_TZ)

    # 1. Тихая синхронизация остатков FBO/FBS и цен Ozon + WB при старте и каждые 30 минут
    scheduler.add_job(
        functools.partial(_guarded_job, "mp_poll_job", scheduled_marketplaces_poll),
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="mp_poll_job",
        replace_existing=True,
    )

    # 2. Автоответчик Ozon: при старте и каждые 30 минут, с проверкой даты публикации
    scheduler.add_job(
        functools.partial(_guarded_job, "ozon_rev_q_30m", scheduled_reviews_and_questions_job),
        IntervalTrigger(minutes=30, timezone=MSK_TZ),
        next_run_time=now_msk,
        id="ozon_rev_q_30m",
        replace_existing=True,
    )

    # 3. Единый эталонный ежедневный аудит цен Ozon (299 ₽ / >300 ₽ с 52%, 3 кнопки) и WB (FBO+FBS) в 10:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "daily_price_audit_job", scheduled_daily_price_audit_job),
        CronTrigger(hour=10, minute=0, timezone=MSK_TZ),
        id="daily_price_audit_job",
        replace_existing=True,
    )

    # 4. Еженедельный аудит и рекомендации по рекламе Ozon (2 гибридные РК) и WB — понедельник 10:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_ads_audit_job", scheduled_weekly_ads_job),
        CronTrigger(day_of_week="mon", hour=10, minute=30, timezone=MSK_TZ),
        id="weekly_ads_audit_job",
        replace_existing=True,
    )

    # 5. Еженедельный отчет ТОП-5 популярных и убыточных товаров — понедельник 09:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_top5_monday_morning", scheduled_monday_top5_report),
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=MSK_TZ),
        id="weekly_top5_monday_morning",
        replace_existing=True,
    )

    # 6. Стратегический Совет ИИ-специалистов — понедельник 19:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "weekly_ai_board_monday_evening", scheduled_monday_evening_ai_board),
        CronTrigger(day_of_week="mon", hour=19, minute=0, timezone=MSK_TZ),
        id="weekly_ai_board_monday_evening",
        replace_existing=True,
    )

    # 7. Уведомление финансового календаря 1-го числа в 09:00 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_pnl_notice_1st", scheduled_1st_day_pnl_notice),
        CronTrigger(day=1, hour=9, minute=0, timezone=MSK_TZ),
        id="monthly_pnl_notice_1st",
        replace_existing=True,
    )

    # 8. Ежемесячный аудит доступности API Ozon, WB и Gemini 1-го числа в 09:05 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_audit_job", scheduled_monthly_audit),
        CronTrigger(day=1, hour=9, minute=5, timezone=MSK_TZ),
        id="monthly_audit_job",
        replace_existing=True,
    )

    # 9. Итоговый месячный P&L отчет по закрывающим актам 5-го числа в 09:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "monthly_pnl_final_5th", scheduled_5th_day_monthly_pnl),
        CronTrigger(day=5, hour=9, minute=30, timezone=MSK_TZ),
        id="monthly_pnl_final_5th",
        replace_existing=True,
    )

    # 10. Автономная ночная самопроверка и автопочинка в 04:30 МСК
    scheduler.add_job(
        functools.partial(_guarded_job, "night_self_heal_job", scheduled_night_self_heal),
        CronTrigger(hour=4, minute=30, timezone=MSK_TZ),
        id="night_self_heal_job",
        replace_existing=True,
    )

    return scheduler


async def _run_polling_with_conflict_guard(polling_ready: asyncio.Event) -> None:
    # aiogram 3 catches API errors inside _listen_updates. Observe the 409 in request middleware,
    # stop this poller, and tell the watchdog not to start a competing getUpdates loop.
    conflict = asyncio.Event()

    async def observe(make_request, bot, method):
        try:
            result = await make_request(bot, method)
            if isinstance(method, GetUpdates):
                polling_ready.set()
            return result
        except TelegramConflictError:
            if isinstance(method, GetUpdates):
                conflict.set()
            raise

    bot.session.middleware(observe)
    await bot.delete_webhook(drop_pending_updates=False)
    polling = asyncio.create_task(dp.start_polling(bot, close_bot_session=False,
                                  allowed_updates=dp.resolve_used_update_types()))
    waiter = asyncio.create_task(conflict.wait())
    try:
        await asyncio.wait((polling, waiter), return_when=asyncio.FIRST_COMPLETED)
        if conflict.is_set():
            await dp.stop_polling()
            await notify_admin('Получен Telegram 409. Остановите другой polling-процесс перед перезапуском.')
            raise SystemExit(75)
        await polling
    finally:
        waiter.cancel()
        if not polling.done():
            polling.cancel()
        await asyncio.gather(waiter, polling, return_exceptions=True)


async def main() -> None:
    prepare_runtime_dirs()
    _ensure_stdio_streams()
    if not acquire_worker_single_instance_lock():
        print(
            f"[Worker] Другой экземпляр worker.py уже удерживает порт {settings.WORKER_LOCK_PORT}. "
            "Запуск отменен во избежание TelegramConflictError.",
            flush=True,
        )
        return

    scheduler: Optional[AsyncIOScheduler] = None
    heartbeat_task: Optional[asyncio.Task] = None

    try:
        await init_db()
        interrupted = await recover_interrupted_actions()

        await ensure_working_telegram_route()
        if interrupted:
            await notify_admin(f"Прерванные ценовые действия: {interrupted}. Сверьте результат в кабинете; повтор отключён.")
        try:
            await setup_bot_commands(bot)
        except Exception as exc:
            print(f"[Worker] Предупреждение: не удалось обновить меню команд TG: {exc}", flush=True)

        scheduler = configure_scheduler()
        scheduler.start()
        polling_ready = asyncio.Event()
        heartbeat_task = asyncio.create_task(_heartbeat_loop(polling_ready))

        await _run_polling_with_conflict_guard(polling_ready)
    finally:
        try:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            if bot is not None:
                await bot.session.close()
        finally:
            try:
                await close_db()
            finally:
                release_worker_single_instance_lock()


if __name__ == "__main__":
    asyncio.run(main())
