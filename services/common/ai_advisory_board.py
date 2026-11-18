import time
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
from config import settings, LOGS_DIR
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.common.orders_report import sync_and_generate_orders_report
from database.crud import get_all_products
from products_catalog import PRODUCTS_CATALOG

PROXY_CANDIDATES = [
    ("Karing HTTP 3066", "http://127.0.0.1:3066", False),
    ("Системный прокси Windows", None, True),
    ("Прямое соединение", None, False),
]

FALLBACK_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.8-flash",
    "gemini-3.1-pro-preview",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


async def _get_available_models(session: aiohttp.ClientSession, api_key: str, proxy: Optional[str]) -> List[str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                models = []
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        m_name = m.get("name", "").replace("models/", "")
                        models.append(m_name)
                if models:
                    return sorted(
                        models,
                        key=lambda x: (
                            "flash-lite" in x,
                            "3." in x and "flash" in x,
                            "flash" in x,
                            "3." in x,
                        ),
                        reverse=True,
                    )
    except Exception:
        pass
    return FALLBACK_MODELS


async def check_gemini_api_health() -> Dict[str, Any]:
    api_key = str(settings.GEMINI_API_KEY).strip()
    if not api_key:
        return {"ok": False, "model": "none", "route": "none", "latency_ms": 0, "detail": "Ключ GEMINI_API_KEY не задан в .env"}

    timeout = aiohttp.ClientTimeout(total=20, connect=8)
    last_error = "Неизвестная ошибка"

    for route_label, proxy_url, trust_env in PROXY_CANDIDATES:
        try:
            async with aiohttp.ClientSession(trust_env=trust_env, timeout=timeout) as session:
                models_to_try = await _get_available_models(session, api_key, proxy_url)
                for model_name in models_to_try:
                    t0 = time.perf_counter()
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": "Ответь: ОК"}]}],
                        "generationConfig": {"maxOutputTokens": 10, "temperature": 0.0},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            if resp.status == 200:
                                data = await resp.json()
                                candidates = data.get("candidates") or []
                                reply = ""
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts") or []
                                    reply = "".join(p.get("text", "") for p in parts).strip()
                                return {
                                    "ok": True,
                                    "model": model_name,
                                    "route": route_label,
                                    "latency_ms": latency_ms,
                                    "detail": f"Отклик получен («{reply or 'ОК'}»)",
                                }
                            else:
                                err_txt = await resp.text()
                                try:
                                    msg = (await resp.json()).get("error", {}).get("message", err_txt[:120])
                                except Exception:
                                    msg = err_txt[:120]
                                last_error = f"[{route_label} | {model_name}] HTTP {resp.status}: {msg}"
                    except Exception as e_req:
                        last_error = f"[{route_label} | {model_name}] {type(e_req).__name__}: {e_req}"
        except Exception as e_sess:
            last_error = f"[{route_label}] {type(e_sess).__name__}: {e_sess}"

    return {"ok": False, "model": "none", "route": "none", "latency_ms": 0, "detail": last_error}


async def _call_gemini_advisory(prompt: str) -> str:
    api_key = str(settings.GEMINI_API_KEY).strip()
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 4096},
    }
    timeout = aiohttp.ClientTimeout(total=95, connect=15)
    errors_log: List[str] = []

    for route_label, proxy_url, trust_env in PROXY_CANDIDATES:
        try:
            async with aiohttp.ClientSession(trust_env=trust_env, timeout=timeout) as session:
                models_to_try = await _get_available_models(session, api_key, proxy_url)
                for model_name in models_to_try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                candidates = data.get("candidates") or []
                                if not candidates:
                                    continue
                                parts = candidates[0].get("content", {}).get("parts") or []
                                text_out = "".join(
                                    p.get("text", "") for p in parts if not p.get("thought", False) and p.get("text")
                                ).strip()
                                if text_out:
                                    return text_out
                            else:
                                err_body = await resp.text()
                                errors_log.append(f"{route_label}/{model_name} -> HTTP {resp.status}: {err_body[:160]}")
                    except Exception as exc:
                        errors_log.append(f"{route_label}/{model_name} -> {type(exc).__name__}: {exc}")
        except Exception as exc:
            errors_log.append(f"{route_label} -> {type(exc).__name__}: {exc}")

    diag = "\n".join(errors_log[:4])
    return f"❌ Не удалось получить ответ от Gemini API.\nПричины:\n{diag}"


async def generate_monday_ai_board_report(
    ozon_service: OzonSellerAPI, wb_service: WBStatisticsAPI
) -> Tuple[str, Path]:
    fin_7d_text = await sync_and_generate_orders_report(ozon_service, wb_service, days=7)
    top5_text = await generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7)

    oz_items, wb_items, db_prods = await asyncio.gather(
        ozon_service.fetch_stocks_and_prices(),
        wb_service.fetch_stocks_and_prices(),
        get_all_products(),
        return_exceptions=True,
    )

    cogs_map = {f"{p.marketplace}_{p.sku}": p for p in (db_prods if isinstance(db_prods, list) else [])}
    sku_lines: List[str] = []
    for mp_label, items in [("OZON", oz_items), ("WB", wb_items)]:
        if not isinstance(items, list):
            continue
        for it in items[:30]:
            sku = str(it.get("sku", ""))
            p_db = cogs_map.get(f"{mp_label.lower()}_{sku}")
            cogs = (p_db.cost_price + p_db.packaging_cost + p_db.first_mile_unit_cost) if p_db else 70.0
            cat_name = PRODUCTS_CATALOG.get(sku, {}).get("name", "")
            sku_lines.append(
                f"[{mp_label}] Арт.{sku} ({cat_name}): ЛК={it.get('current_price')}₽, "
                f"Покупатель={it.get('buyer_price')}₽, Себес={cogs:.0f}₽, "
                f"Комиссия={float(it.get('commission_rate', 0))*100:.1f}%, "
                f"Остаток={it.get('stock_fbo')} шт."
            )

    sku_snapshot = "\n".join(sku_lines[:35])

    prompt = f"""
Ты — Совет директоров бренда косметики для ногтей YaSoKo (гели для моделирования, базы, топы в банках 15 г).
Склад и производство находятся в г. Иваново. ИП на УСН 6% (доходы). Налог 6% начисляется строго с цены покупки покупателем.

РЕАЛЬНЫЕ УСЛОВИЯ И ОГРАНИЧЕНИЯ (УЧИТЫВАТЬ СТРОГО):
1. Логистика из Иваново: сдача по FBS Ozon на месте обходится в 10 ₽/шт. Доставка первой мили на склады FBO (МО: Коледино/Электросталь, Казань, Ярославль) выходит в среднем 12 ₽/шт. (на 2 ₽ дороже FBS). Не советовать FBO ради мнимой экономии приемки. FBO оправдан только для 2-3 бестселлеров ради скорости доставки и буста СПП.
2. Ценообразование Ozon: НЕ советовать цены 301–450 ₽ (тарифная ловушка Ozon, где резко подскакивает процент логистики и срезается СПП). Либо держать до 299 ₽ или выше 450 ₽, либо комбо-наборы от 490 ₽.
3. Реклама: не писать абстрактное "снизить ДРР". Предлагать конкретную механику: выделять 1-2 локомотива (прозрачный 1001 / молочный 1002) в АРК/поиске с отсечением мусорных ключей ("лак", "шеллак"), а в карточке локомотива через серии и рич-контент допродавать оттенки.
4. Доставка маркетплейса — объективная данность, на нее нельзя повлиять напрямую, компенсировать ее нужно ростом среднего чека и наборами.

ФАКТИЧЕСКИЕ ДАННЫЕ ЗА 7 ДНЕЙ:
{fin_7d_text}

ТОП-5 ТОВАРОВ И УБЫТОЧНЫЕ:
{top5_text}

СРЕЗ ЦЕН И ОСТАТКОВ:
{sku_snapshot}

ТРЕБОВАНИЯ К ФОРМАТУ ОТВЕТА:
Каждый эксперт дает четкую рекомендацию по схеме: ИДЕЯ -> АРГУМЕНТАЦИЯ С ЦИФРАМИ -> ПРОГНОЗ В РУБЛЯХ. Никакой воды и шаблонных фраз.

Структура:
🏛️ СВОДНОЕ РЕШЕНИЕ СОВЕТА: 3 главных приоритетных шага на 7 дней с указанием артикулов и эффекта в рублях.
🔧 ОПЕРАЦИОННЫЙ ДИРЕКТОР: FBO против FBS с учетом отправки из Иваново (+2 ₽/шт. за FBO). Какие именно 2-3 артикула отгрузить на склад МО/Казань, а что оставить строго на FBS.
💰 ФИНАНСОВЫЙ ДИРЕКТОР: Расчет чистой прибыли по убыточным артикулам, налог УСН 6%, точные цены в ЛК (с обходом ловушки 301-450 ₽ на Ozon).
⚠️ РИСК-МЕНЕДЖЕР / СКЕПТИК: Анализ скрытых сливов (платное хранение, реклама без конверсий, возвраты, декомпенсации).
👤 МАРКЕТОЛОГ: Конкретные рекламные комбинации (локомотив + допродажи оттенков), воронка фото/видео (демонстрация средне-вязкой капли и опила).
🚀 ДИРЕКТОР ПО РАЗВИТИЮ: Создание сетов и комбо (база 15г + гель 15г + топ 15г) с чеком 550-750 ₽ для снижения удельной доли доставки маркетплейса с 25% до 12%.
"""

    ai_analysis = await _call_gemini_advisory(prompt)

    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
    report_file = LOGS_DIR / f"AI_Board_Report_{now_str}.html"
    html_doc = (
        f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        f"<title>AI Board Report {now_str}</title></head>"
        f"<body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:920px;margin:20px auto;padding:20px;line-height:1.6;background:#f8fafc;color:#0f172a'>"
        f"<div style='background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.06)'>"
        f"<h1 style='margin-top:0'>🧠 Стратегический разбор Совета ИИ — YaSoKo ({now_str})</h1>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:15px'>{ai_analysis}</pre><hr style='margin:24px 0'>"
        f"<h2>📊 Приложение: ТОП-5 и финансовые срезы</h2><pre style='white-space:pre-wrap;font-family:inherit'>{top5_text}</pre>"
        f"</div></body></html>"
    )
    report_file.write_text(html_doc, encoding="utf-8")

    tg_summary = ai_analysis[:3800]
    if len(ai_analysis) > 3800:
        tg_summary += "\n\n📎 <i>Полный файл разбора прикреплен ниже.</i>"

    return tg_summary, report_file