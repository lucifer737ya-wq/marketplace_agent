import time
from datetime import datetime
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI
from services.common.buyer_questions import call_fast_gemini
from services.common.ai_advisory_board import check_gemini_api_health


async def run_monthly_api_audit() -> str:
    ozon = OzonSellerAPI()
    wb = WBStatisticsAPI()

    # 1. Ozon
    t0 = time.perf_counter()
    try:
        oz_items = await ozon.fetch_stocks_and_prices()
        oz_ms = int((time.perf_counter() - t0) * 1000)
        oz_status = f"🟢 <b>OK</b> ({oz_ms} мс) — получено {len(oz_items)} SKU"
    except Exception as exc:
        oz_status = f"🔴 <b>СБОЙ:</b> <code>{str(exc)[:120]}</code>"

    # 2. WB
    t1 = time.perf_counter()
    try:
        wb_items = await wb.fetch_stocks_and_prices()
        wb_tariffs = await wb.fetch_wb_tariffs()
        wb_ms = int((time.perf_counter() - t1) * 1000)
        wb_status = (
            f"🟢 <b>OK</b> ({wb_ms} мс) — получено {len(wb_items)} SKU, "
            f"комиссия {wb_tariffs['commission_rate']*100:.1f}%, логистика {wb_tariffs['logistics_rub']:.0f} ₽"
        )
    except Exception as exc:
        wb_status = f"🔴 <b>СБОЙ:</b> <code>{str(exc)[:120]}</code>"

    # 3. Старшая модель (Совет ИИ)
    ai_health = await check_gemini_api_health()
    if ai_health["ok"]:
        ai_main_status = f"🟢 <b>OK</b> ({ai_health['latency_ms']} мс, <code>{ai_health['model']}</code>)"
    else:
        ai_main_status = f"🔴 <b>СБОЙ:</b> <code>{ai_health['detail'][:100]}</code>"

    # 4. Легковесная модель (Отзывы и Вопросы)
    t2 = time.perf_counter()
    try:
        fast_resp = await call_fast_gemini("Тест связи: ответь одним словом ОК")
        fast_ms = int((time.perf_counter() - t2) * 1000)
        ai_fast_status = f"🟢 <b>OK</b> ({fast_ms} мс) — эконом-модель активна"
    except Exception as exc:
        ai_fast_status = f"🔴 <b>СБОЙ:</b> <code>{str(exc)[:100]}</code>"

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return (
        f"🔍 <b>Диагностика и Аудит API ({now_str})</b>\n\n"
        f"🔵 <b>Ozon Seller API:</b>\n  └ {oz_status}\n\n"
        f"🟣 <b>Wildberries API:</b>\n  └ {wb_status}\n\n"
        f"🧠 <b>Gemini Pro/Flash (Совет директоров):</b>\n  └ {ai_main_status}\n\n"
        f"⚡ <b>Gemini Fast-Lite (Отзывы и Вопросы):</b>\n  └ {ai_fast_status}"
    )