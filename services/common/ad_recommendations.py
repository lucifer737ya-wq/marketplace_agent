import json
import aiohttp
from typing import Dict, Any, List
from config import settings
from services.common.ai_advisory_board import _get_available_models, PROXY_CANDIDATES


async def _call_gemini_unlimited(prompt: str) -> str:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return "⚠️ GEMINI_API_KEY не задан."

    timeout = aiohttp.ClientTimeout(total=120)
    sorted_candidates = sorted(PROXY_CANDIDATES, key=lambda x: 0 if x[1] else 1)
    for _, proxy_url, trust_env in sorted_candidates:
        try:
            async with aiohttp.ClientSession(trust_env=trust_env, timeout=timeout) as session:
                models = await _get_available_models(session, api_key, proxy_url)
                for m in models:
                    m_name = (m if isinstance(m, str) else getattr(m, "name", str(m))).replace("models/", "").strip()
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 16384},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                cands = data.get("candidates") or []
                                if cands:
                                    parts = cands[0].get("content", {}).get("parts") or []
                                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
                                    if len(txt) > 100:
                                        return txt
                    except Exception:
                        continue
        except Exception:
            continue
    return "⚠️ Не удалось получить ответ от моделей ИИ."


async def generate_weekly_ads_report_and_recommendations(ozon_api, wb_api) -> str:
    oz_stocks = await ozon_api.fetch_stocks_and_prices()
    wb_stocks = await wb_api.fetch_stocks_and_prices()
    oz_fin = await ozon_api.fetch_real_finance_breakdown(days=7)
    wb_fin = await wb_api.fetch_real_finance_breakdown(days=7)

    oz_campaigns = await ozon_api.fetch_active_ad_campaigns(days=7) if hasattr(ozon_api, "fetch_active_ad_campaigns") else []
    wb_campaigns = await wb_api.fetch_active_ad_campaigns(days=7) if hasattr(wb_api, "fetch_active_ad_campaigns") else []

    oz_orders_cnt = max(int(oz_fin.get("orders_count") or 172), 1)
    oz_actual_deliv = round(float(oz_fin.get("deliv_total") or 6686.0) / oz_orders_cnt, 2)

    oz_summary = [
        f"SKU {i['sku']} (Ozon prod_id {i.get('product_id')}): цена {i['current_price']:.0f}₽, в акции={i.get('in_promo')}, "
        f"комиссия={i.get('commission_rate', 0.23)*100:.1f}%, факт. доставка={oz_actual_deliv:.0f}₽, ПВЗ FBS={i.get('fbs_pvz_dropoff_rub', 11):.0f}₽, "
        f"ОСТАТКИ: FBO={i.get('stock_fbo', 0)} шт., FBS={i.get('stock_fbs', 0)} шт., продаж/день={i.get('daily_sales', 0)}"
        for i in oz_stocks if (i.get("stock_fbo", 0) + i.get("stock_fbs", 0)) > 0
    ]

    wb_summary = [
        f"SKU {i['sku']} (nmID {i['external_id']}): цена {i['current_price']:.0f}₽, комиссия WB={i.get('commission_rate', 0.25)*100:.1f}%, "
        f"ОСТАТКИ: FBO={i.get('stock_fbo', 0)} шт., FBS={i.get('stock_fbs', 0)} шт., продаж/день={i.get('daily_sales', 0)}"
        for i in wb_stocks if (i.get("stock_fbo", 0) + i.get("stock_fbs", 0)) > 0
    ]

    oz_camp_dump = json.dumps(oz_campaigns, ensure_ascii=False, indent=2)
    wb_camp_dump = json.dumps(wb_campaigns, ensure_ascii=False, indent=2) if wb_campaigns else "Активных РК со списаниями за 7 дней нет"

    prompt = (
        "Ты — финансовый аналитик и performance-директор бренда YaSoKo (моделирующие гели, базы и топы для ногтей 15 г).\n\n"
        "1. ТОЧНЫЕ ПАРАМЕТРЫ 2 ЗАПУЩЕННЫХ РЕКЛАМНЫХ КАМПАНИЙ OZON:\n"
        "- Кампания №1: 4 товара в продвижении, фактический ДРР в продвижении = 6,3% (механика: Оплата за клик по целевому расходу + сниженная оплата за заказ).\n"
        "- Кампания №2: 2 товара в продвижении, фактический ДРР в продвижении = 15,4% (механика: Оплата за клик по целевому расходу + сниженная оплата за заказ).\n"
        "- Остальные товары продаются органически (в рекламе не участвуют).\n"
        f"Детальный слепок API по этим кампаниям:\n{oz_camp_dump}\n"
        f"(Общие финансовые итоги Ozon за 7 дней: Выручка = {oz_fin.get('sales_total', 0):,.0f} ₽, Всего заказов = {oz_orders_cnt} шт., "
        f"Фактическая доставка 1 заказа = {oz_actual_deliv} ₽ [{oz_fin.get('deliv_total', 0):,.0f} ₽ / {oz_orders_cnt} шт.], "
        f"Общие списания на рекламу = {oz_fin.get('ads_total', 0):,.0f} ₽: сниженный CPO = {oz_fin.get('promo_cpo', 0):,.0f} ₽, кликовый CPC = {oz_fin.get('promo_cpc', 0):,.0f} ₽).\n\n"
        "2. ДАННЫЕ ПО РЕКЛАМНЫМ КАМПАНИЯМ WILDBERRIES:\n"
        f"{wb_camp_dump}\n\n"
        "3. АКТИВНЫЕ ТОВАРЫ И ОСТАТКИ OZON (FBO и FBS):\n"
        + ("\n".join(oz_summary) or "Нет остатков") + "\n\n"
        "4. АКТИВНЫЕ ТОВАРЫ И ОСТАТКИ WILDBERRIES (FBO и FBS):\n"
        + ("\n".join(wb_summary) or "Нет остатков") + "\n\n"
        "СОСТАВЬ СТРУКТУРИРОВАННЫЙ ОТЧЕТ И КОНКРЕТНЫЙ ПЛАН ДЕЙСТВИЙ БЕЗ ВОДЫ:\n"
        "Раздел 1. ДЕТАЛЬНЫЙ АУДИТ КАМПАНИИ №1 (4 товара, ДРР 6,3%) И КАМПАНИИ №2 (2 товара, ДРР 15,4%) НА OZON:\n"
        "- Разбери отдельно Кампанию №1 (4 товара, ДРР 6,3% = ~18,8 ₽ рекламы на баночку 299 ₽): почему она дает сверхвысокую рентабельность (+55 ₽ чистой прибыли с банки после рекламы) и как масштабировать в ней трафик без роста ДРР.\n"
        "- Разбери отдельно Кампанию №2 (2 товара, ДРР 15,4% = ~46,0 ₽ рекламы на баночку 299 ₽): покажи, что при ДРР 15,4% чистая прибыль с баночки падает до +27,5 ₽. Дай четкую инструкцию: какую предельную ставку целевого расхода (%) выставить в настройках Кампании №2 (например, снизить потолок с 15,4% до 10–11%), или какие товары перенести между кампаниями.\n"
        "- Разбери 2 товара вне рекламы: стоит ли добавлять их в Кампанию №1 при текущих остатках FBO и FBS.\n\n"
        "Раздел 2. СРАВНЕНИЕ ЧИСТОЙ ПРИБЫЛИ OZON (при реальной доставке 39 ₽/заказ!):\n"
        "- Покажи точный расклад на 1 баночку при цене 299 ₽:\n"
        "  • В Кампании №1 (при ДРР 6,3% / -19 ₽): Чистая прибыль = +54,5 ₽/шт.\n"
        "  • В Кампании №2 (при ДРР 15,4% / -46 ₽): Чистая прибыль = +27,5 ₽/шт.\n"
        "  • При выходе из акции и цене 740 ₽ (комиссия 52%): Чистая прибыль = +83 ₽/шт.\n\n"
        "Раздел 3. ДЕТАЛЬНЫЙ ПЛАН ПО РЕКЛАМЕ WILDBERRIES (с учетом текущих FBS-остатков, дневного бюджета, целевого CPM и выбора артикулов).\n\n"
        "Раздел 4. КОНТРОЛЬНЫЕ ТОЧКИ ОТСЛЕЖИВАНИЯ (24ч, 72ч, 7 дней).\n"
        "Используй только HTML-теги <b> и <i>. Не используй символы **, ### или таблицы Markdown."
    )

    return await _call_gemini_unlimited(prompt)