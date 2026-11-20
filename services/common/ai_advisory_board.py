import asyncio
import html
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.common.expert_profiles import build_expert_preamble
from services.common.gemini import generate

from config import LOGS_DIR
from database.crud import get_all_products
from products_catalog import PRODUCTS_CATALOG
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.common.orders_report import sync_and_generate_orders_report
from services.common.price_calculator import build_price_report
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


async def check_gemini_api_health() -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        reply, model = await generate('Ответь: ОК', max_tokens=128, fast=True, timeout=20)
    except RuntimeError as exc:
        return {'ok': False, 'detail': str(exc)}
    return {'ok': True, 'model': model, 'route': 'авто (прямой/прокси)',
            'latency_ms': int((time.perf_counter() - start) * 1000), 'detail': reply}


async def _call_gemini_advisory(prompt: str) -> Tuple[str, bool]:
    """(текст, ИИ ответил). Сбой ИИ — не результат: вызывающий код включает расчётный режим."""
    try:
        text, _ = await generate(prompt, max_tokens=8192, temperature=0.35)
        return text, bool(text.strip())
    except RuntimeError as exc:
        return f'Совет ИИ недоступен: {exc}', False


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def _rules_based_board(
    ozon_service: OzonSellerAPI, wb_service: WBStatisticsAPI, reason: str
) -> str:
    """Резерв без ИИ: цены ЛК «в ноль» и по целевой марже из фактических ставок."""
    try:
        prices = _plain(await build_price_report(ozon_service, wb_service))
    except Exception as exc:  # noqa: BLE001 — резерв сам не должен падать
        prices = f"Расчёт цен недоступен: {type(exc).__name__}: {exc}"
    return (
        "⚠️ ИИ-Советник временно недоступен — ниже расчётный разбор по вашим фактическим ставкам "
        "(без текстовых рекомендаций ИИ).\n"
        f"Причина: {reason[:300]}\n\n"
        "Что делать сейчас:\n"
        "1) Артикулы с ценой ЛК ниже «в ноль» — поднять цену или вывести из рекламы.\n"
        "2) Проверьте «Аудит API» — маршрут Gemini/прокси.\n"
        "3) Повторите разбор позже кнопкой «🧠 ИИ-Советник».\n\n"
        + prices
    )


async def generate_monday_ai_board_report(
    ozon_service: OzonSellerAPI, wb_service: WBStatisticsAPI
) -> Tuple[str, Optional[Path]]:
    async def _safe(label: str, coro) -> Any:
        """Один упавший источник не должен обнулять весь разбор Совета."""
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return RuntimeError(f"{label}: {exc}")

    fin_res, top5_res, oz_items, wb_items, db_prods = await asyncio.gather(
        _safe("финансы 7 дней", sync_and_generate_orders_report(ozon_service, wb_service, days=7)),
        _safe(
            "ТОП-5",
            generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7),
        ),
        _safe("остатки Ozon", ozon_service.fetch_stocks_and_prices()),
        _safe("остатки WB", wb_service.fetch_stocks_and_prices()),
        _safe("каталог БД", get_all_products()),
        return_exceptions=True,
    )

    fin_7d_text = (
        fin_res
        if isinstance(fin_res, str)
        else f"Финансовый отчет временно недоступен: {fin_res}"
    )
    top5_text = (
        top5_res
        if isinstance(top5_res, str)
        else f"Отчет ТОП-5 временно недоступен: {top5_res}"
    )

    cogs_map = {
        f"{str(p.marketplace).lower()}_{str(p.sku).strip()}": p
        for p in (db_prods if isinstance(db_prods, list) else [])
    }
    sku_lines: List[str] = []
    for mp_label, items in [("OZON", oz_items), ("WB", wb_items)]:
        if not isinstance(items, list):
            continue
        for it in items[:30]:
            if not isinstance(it, dict):
                continue
            sku = str(it.get("sku") or "").strip()
            p_db = cogs_map.get(f"{mp_label.lower()}_{sku}")
            if p_db and float(p_db.cost_price or 0.0) > 0.0:
                cogs_val = float(p_db.cost_price) + float(p_db.packaging_cost or 0.0) + float(p_db.first_mile_unit_cost or 0.0)
                cogs_str = f"{cogs_val:.0f}₽"
            else:
                cogs_str = "не задана"
            cat_name = PRODUCTS_CATALOG.get(sku, {}).get("name", "")
            comm_pct = f"{float(it['commission_rate'])*100:.1f}%" if it.get('commission_rate') is not None else 'н/д'
            sku_lines.append(
                f"[{mp_label}] Арт.{sku} ({cat_name}): ЛК={it.get('current_price')}₽, "
                f"Покупатель={it.get('buyer_price')}₽, Себес={cogs_str}, "
                f"Комиссия={comm_pct}, "
                f"Остаток FBO={it.get('stock_fbo', 0)} шт., FBS={it.get('stock_fbs', 0)} шт."
            )

    sku_snapshot = "\n".join(sku_lines[:35]) if sku_lines else "Срез остатков и цен временно недоступен."

    # Шаг 1 (навыки/знания/опыт специалистов) → шаг 2 (разбор строго с их позиции).
    expert_preamble = await build_expert_preamble(
        ("cfo", "coo", "risk", "marketer", "bizdev"), with_cabinets=True
    )

    prompt = f"""
Ты — Совет директоров бренда косметики для ногтей YaSoKo (гели для моделирования, базы, топы в банках 15 г).
Склад и производство находятся в г. Иваново. ИП на УСН 6% (доходы). Используй налоговые базы из отчёта: реализация продавца на Ozon, покупка с учётом СПП на WB.

УСЛОВИЯ АНАЛИЗА:
1. Сравни фактические затраты FBS и FBO по данным ниже. Если нет тарифа ПВЗ или первой мили, укажи, что нужно запросить.
2. Ценообразование Ozon: сравни 299 ₽ (22–23%) и цену выше 300 ₽ с комиссией 52%. Не предлагай цену без расчёта затрат.
3. Реклама: не писать абстрактное "снизить ДРР". Предлагать конкретную механику: выделять 1-2 локомотива (прозрачный 1001 / молочный 1002) в АРК/поиске с отсечением мусорных ключей ("лак", "шеллак"), а в карточке локомотива через серии и рич-контент допродавать оттенки.
4. Доставка маркетплейса — объективная данность, на нее нельзя повлиять напрямую, компенсировать ее нужно ростом среднего чека и наборами.

{expert_preamble}

ФАКТИЧЕСКИЕ ДАННЫЕ ЗА 7 ДНЕЙ:
{fin_7d_text}

ТОП-5 ТОВАРОВ И УБЫТОЧНЫЕ:
{top5_text}

СРЕЗ ЦЕН И ОСТАТКОВ:
{sku_snapshot}

ТРЕБОВАНИЯ К ФОРМАТУ ОТВЕТА:
Каждый эксперт работает СТРОГО по «ОБЯЗАТЕЛЬНОМУ МЕТОДУ» выше, опираясь на свои навыки и знания:
ДИАГНОЗ → РАСЧЁТ (формула с цифрами) → ВАРИАНТЫ (минимум 3, с эффектом в ₽) → ВЫБОР (почему этот и почему не остальные) →
КЕЙС ПО ШАГАМ В ЛК → КОНТРОЛЬ. «Поднять цену» допустимо только как один из вариантов с расчётом чистой прибыли
(₽/шт и %) до и после, влияния на заказы и сравнением с альтернативами (снижение расходов, вывод из рекламы,
набор, FBS↔FBO, смена акции). Никакой воды. Если данных мало — «н/д» и что запросить.

Структура:
🏛️ СВОДНОЕ РЕШЕНИЕ СОВЕТА: 3 приоритетных шага на 7 дней с артикулами и эффектом в рублях.
💰 ФИНАНСОВЫЙ ДИРЕКТОР: убыточные артикулы, налог УСН 6%, расчёт цен ЛК и сравнение вариантов.
🔧 ОПЕРАЦИОННЫЙ ДИРЕКТОР: FBO против FBS с учётом отправки из Иваново, конкретные артикулы и количества.
⚠️ РИСК-МЕНЕДЖЕР / СКЕПТИК: скрытые сливы и какие данные ненадёжны.
👤 МАРКЕТОЛОГ: рекламные связки, воронка фото/видео, SEO — с ЛК-шагами.
🚀 ДИРЕКТОР ПО РАЗВИТИЮ: наборы/комбо (гель + гель), расчёт доли доставки. Базы и топы YaSoKo находятся в разработке —
не предлагай их как существующие товары и не называй сроков.
"""

    ai_analysis, ai_ok = await _call_gemini_advisory(prompt)
    if not ai_ok:
        ai_analysis = await _rules_based_board(ozon_service, wb_service, ai_analysis)
    ai_analysis = ai_analysis.replace("**", "").replace("__", "")

    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_file = LOGS_DIR / f"AI_Board_Report_{now_str}.html"
    safe_ai_html = html.escape(ai_analysis)
    html_doc = (
        f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        f"<title>AI Board Report {now_str}</title></head>"
        f"<body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:920px;margin:20px auto;padding:20px;line-height:1.6;background:#f8fafc;color:#0f172a'>"
        f"<div style='background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.06)'>"
        f"<h1 style='margin-top:0'>🧠 Стратегический разбор Совета ИИ — YaSoKo ({now_str})</h1>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:15px'>{safe_ai_html}</pre><hr style='margin:24px 0'>"
        f"<h2>📊 Приложение: ТОП-5 и финансовые срезы</h2><pre style='white-space:pre-wrap;font-family:inherit'>{html.escape(top5_text)}</pre>"
        f"</div></body></html>"
    )
    saved_path: Optional[Path] = None
    try:
        report_file.write_text(html_doc, encoding="utf-8")
        saved_path = report_file
    except OSError as exc:  # noqa: BLE001 — диск может быть занят или переполнен
        print(f"[AI Board] Не удалось записать HTML-отчёт: {exc}", flush=True)

    # Раньше текст резался на 3800 символах посреди фразы. Теперь отдаём целиком —
    # worker делит его на сообщения по абзацам; файл — полная копия.
    tg_summary = ai_analysis

    # None означает «файл не сохранён»: вызывающий код не должен пытаться
    # отправить в Telegram несуществующий документ.
    return tg_summary, saved_path
