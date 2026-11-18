import asyncio
import html
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import LOGS_DIR, settings
from database.crud import get_all_products
from products_catalog import PRODUCTS_CATALOG
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.common.orders_report import sync_and_generate_orders_report
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


def _build_proxy_candidates() -> List[Tuple[str, Optional[str], bool]]:
    proxy_url = str(getattr(settings, "LOCAL_PROXY_URL", "") or "").strip()
    if proxy_url:
        return [(f"Локальный прокси ({proxy_url})", proxy_url, False)]
    return [
        ("Системный прокси", None, True),
        ("Прямое соединение", None, False),
    ]


PROXY_CANDIDATES: List[Tuple[str, Optional[str], bool]] = _build_proxy_candidates()

EXCLUDED_MODEL_TOKENS: Tuple[str, ...] = (
    "tts",
    "audio",
    "imagen",
    "embedding",
    "aqa",
    "vision",
    "veo",
    "whisper",
    "robotics",
    "computer-use",
)

FALLBACK_MODELS: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def _extract_model_name(model_item: Any) -> str:
    """Безопасно извлекает строковое имя модели Gemini без обращения к .name у строк."""
    if isinstance(model_item, str):
        raw = model_item
    elif isinstance(model_item, dict):
        raw = str(model_item.get("name") or "")
    else:
        raw = str(model_item or "")
    return raw.strip().strip('"').strip("'").replace("models/", "").strip()


def _is_supported_text_model(model_name: str) -> bool:
    low = model_name.lower()
    if not low:
        return False
    return not any(tok in low for tok in EXCLUDED_MODEL_TOKENS)


async def _get_available_models(
    session: aiohttp.ClientSession,
    api_key: str,
    proxy: Optional[str],
) -> List[str]:
    configured = _extract_model_name(getattr(settings, "GEMINI_MODEL", "") or "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    discovered: List[str] = []
    try:
        timeout = aiohttp.ClientTimeout(total=12, connect=8, sock_connect=8, sock_read=10)
        async with session.get(url, proxy=proxy, timeout=timeout) as resp:
            if resp.status == 200:
                raw_text = await resp.text(encoding="utf-8", errors="replace")
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    for m in data.get("models") or []:
                        if not isinstance(m, dict):
                            continue
                        methods = m.get("supportedGenerationMethods") or []
                        if "generateContent" in methods:
                            m_name = _extract_model_name(m)
                            if _is_supported_text_model(m_name):
                                discovered.append(m_name)
    except Exception:
        pass

    discovered.sort(
        key=lambda x: (
            "2.5-flash" in x and "lite" not in x,
            "flash-lite" in x,
            "flash" in x,
            "2.5" in x,
        ),
        reverse=True,
    )
    ordered = [
        m
        for m in [configured, *discovered, *FALLBACK_MODELS]
        if _is_supported_text_model(m)
    ]
    return list(dict.fromkeys(ordered))


async def check_gemini_api_health() -> Dict[str, Any]:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return {
            "ok": False,
            "model": "none",
            "route": "none",
            "latency_ms": 0,
            "detail": "Ключ GEMINI_API_KEY не задан в .env",
        }

    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_connect=8, sock_read=15)
    last_error = "Неизвестная ошибка"

    for route_label, proxy_url, trust_env in _build_proxy_candidates():
        connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                trust_env=trust_env,
                timeout=timeout,
            ) as session:
                models_to_try = await _get_available_models(session, api_key, proxy_url)
                for model_name in models_to_try:
                    t0 = time.perf_counter()
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": "Ответь: ОК"}]}],
                        "generationConfig": {"maxOutputTokens": 128, "temperature": 0.0},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            body_text = await resp.text(encoding="utf-8", errors="replace")
                            if resp.status == 200:
                                data = json.loads(body_text)
                                candidates = data.get("candidates") or []
                                reply = ""
                                if candidates:
                                    parts = (candidates[0].get("content") or {}).get("parts") or []
                                    reply = "".join(
                                        str(p.get("text") or "")
                                        for p in parts
                                        if isinstance(p, dict) and not p.get("thought", False)
                                    ).strip()
                                return {
                                    "ok": True,
                                    "model": model_name,
                                    "route": route_label,
                                    "latency_ms": latency_ms,
                                    "detail": f"Отклик получен («{reply or 'ОК'}»)",
                                }
                            msg = body_text[:120]
                            try:
                                parsed_err = json.loads(body_text)
                                if isinstance(parsed_err, dict):
                                    msg = str((parsed_err.get("error") or {}).get("message") or msg)[:120]
                            except Exception:
                                pass
                            last_error = f"[{route_label} | {model_name}] HTTP {resp.status}: {msg}"
                            if resp.status in (401, 403):
                                break
                    except Exception as e_req:
                        last_error = f"[{route_label} | {model_name}] {type(e_req).__name__}: {e_req}"
        except Exception as e_sess:
            last_error = f"[{route_label}] {type(e_sess).__name__}: {e_sess}"
        finally:
            if not connector.closed:
                await connector.close()

    return {"ok": False, "model": "none", "route": "none", "latency_ms": 0, "detail": last_error}


async def _call_gemini_advisory(prompt: str) -> str:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return "❌ Не удалось получить ответ от Gemini API.\nПричины:\nКлюч GEMINI_API_KEY не задан в .env"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 8192},
    }
    timeout = aiohttp.ClientTimeout(total=95, connect=15, sock_connect=15, sock_read=80)
    errors_log: List[str] = []

    for route_label, proxy_url, trust_env in _build_proxy_candidates():
        connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                trust_env=trust_env,
                timeout=timeout,
            ) as session:
                models_to_try = await _get_available_models(session, api_key, proxy_url)
                for model_name in models_to_try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            err_body = await resp.text(encoding="utf-8", errors="replace")
                            if resp.status == 200:
                                data = json.loads(err_body)
                                candidates = data.get("candidates") or []
                                if not candidates:
                                    continue
                                parts = (candidates[0].get("content") or {}).get("parts") or []
                                text_out = "".join(
                                    str(p.get("text") or "")
                                    for p in parts
                                    if isinstance(p, dict) and not p.get("thought", False) and p.get("text")
                                ).strip()
                                if text_out:
                                    return text_out
                            else:
                                errors_log.append(f"{route_label}/{model_name} -> HTTP {resp.status}: {err_body[:160]}")
                                if resp.status in (401, 403):
                                    break
                    except Exception as exc:
                        errors_log.append(f"{route_label}/{model_name} -> {type(exc).__name__}: {exc}")
        except Exception as exc:
            errors_log.append(f"{route_label} -> {type(exc).__name__}: {exc}")
        finally:
            if not connector.closed:
                await connector.close()

    diag = "\n".join(errors_log[:4])
    return f"❌ Не удалось получить ответ от Gemini API.\nПричины:\n{diag}"


async def generate_monday_ai_board_report(
    ozon_service: OzonSellerAPI, wb_service: WBStatisticsAPI
) -> Tuple[str, Path]:
    fin_res, top5_res, oz_items, wb_items, db_prods = await asyncio.gather(
        sync_and_generate_orders_report(ozon_service, wb_service, days=7),
        generate_weekly_top_and_unprofitable_report(ozon_service, wb_service, days=7),
        ozon_service.fetch_stocks_and_prices(),
        wb_service.fetch_stocks_and_prices(),
        get_all_products(),
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
            comm_pct = float(it.get("commission_rate") or 0.0) * 100.0
            sku_lines.append(
                f"[{mp_label}] Арт.{sku} ({cat_name}): ЛК={it.get('current_price')}₽, "
                f"Покупатель={it.get('buyer_price')}₽, Себес={cogs_str}, "
                f"Комиссия={comm_pct:.1f}%, "
                f"Остаток FBO={it.get('stock_fbo', 0)} шт., FBS={it.get('stock_fbs', 0)} шт."
            )

    sku_snapshot = "\n".join(sku_lines[:35]) if sku_lines else "Срез остатков и цен временно недоступен."

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
    safe_ai_html = html.escape(ai_analysis)
    html_doc = (
        f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        f"<title>AI Board Report {now_str}</title></head>"
        f"<body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:920px;margin:20px auto;padding:20px;line-height:1.6;background:#f8fafc;color:#0f172a'>"
        f"<div style='background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,0.06)'>"
        f"<h1 style='margin-top:0'>🧠 Стратегический разбор Совета ИИ — YaSoKo ({now_str})</h1>"
        f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:15px'>{safe_ai_html}</pre><hr style='margin:24px 0'>"
        f"<h2>📊 Приложение: ТОП-5 и финансовые срезы</h2><pre style='white-space:pre-wrap;font-family:inherit'>{top5_text}</pre>"
        f"</div></body></html>"
    )
    report_file.write_text(html_doc, encoding="utf-8")

    tg_summary = ai_analysis[:3800]
    if len(ai_analysis) > 3800:
        tg_summary += "\n\n📎 Полный файл разбора прикреплен ниже."

    return tg_summary, report_file
