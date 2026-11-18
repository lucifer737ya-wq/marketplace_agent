import json
import asyncio
import urllib.request
import urllib.error
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message
from sqlalchemy import select

from config import settings
from products_catalog import (
    PRODUCTS_CATALOG,
    ALLOWED_UPSELL_LIST,
    GEL_COMMON_DESCRIPTION,
    LEGAL_COMPLIANCE_INFO,
)
from database.models import AsyncSessionLocal, Product

reviews_router = Router()


class ReviewForm(StatesGroup):
    waiting_for_review_data = State()


def get_product_context(identifier: str) -> dict:
    """Ищет товар в справочнике по артикулу или по SKU Ozon/WB."""
    clean_id = identifier.strip()
    # 1. Поиск по прямому артикулу (например, 1001, 21000)
    if clean_id in PRODUCTS_CATALOG:
        return PRODUCTS_CATALOG[clean_id]

    # 2. Поиск по SKU Ozon или WB
    for item in PRODUCTS_CATALOG.values():
        if item.get("ozon_sku") == clean_id or item.get("wb_sku") == clean_id:
            return item

    # 3. Дефолтная карточка для неизвестного артикула гелей YaSoKo
    return {
        "art": clean_id,
        "title": f"Моделирующий гель YaSoKo (арт. {clean_id})",
        "category": "Камуфлирующий гель для наращивания и моделирования ногтей",
        "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE",
        "recommended_upsell": "каучуковая эластичная база YaSoKo (арт. 21000) или топ без липкого слоя (арт. 22000)",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491",
    }


def generate_review_reply_llm(sku: str, product_meta: dict, review_text: str) -> str:
    """Генерация строгого ответа на отзыв с учетом каталога YaSoKo и каскадом моделей."""
    raw_model = getattr(settings, "GEMINI_MODEL", "gemini-3.5-flash") or "gemini-3.5-flash"
    primary_model = raw_model.strip().strip('"').strip("'").replace("models/", "")

    fallback_models = [
        primary_model,
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3.1-pro-preview",
    ]
    models_to_try = list(dict.fromkeys(fallback_models))
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip().strip('"').strip("'")

    art = product_meta.get("art", sku)
    shade = product_meta.get("shade", "")
    color = product_meta.get("color_name", "")
    title = product_meta.get("title", f"Продукт YaSoKo {sku}")
    category = product_meta.get("category", "Моделирующий гель для ногтей")
    volume = product_meta.get("volume", "15 г")
    formula = product_meta.get("formula", "HEMA FREE, 11-FREE")
    upsell = product_meta.get("recommended_upsell", "топ без липкого слоя YaSoKo")
    composition = product_meta.get("composition", "Безопасный гипоаллергенный состав")

    prompt = (
        "Ты — официальный представитель бренда профессиональных материалов для маникюра «YaSoKo» на Ozon и Wildberries.\n"
        "Составь безупречный, вежливый, экспертный и теплый официальный ответ на отзыв покупателя.\n\n"
        "--- ПРАВИЛА И ОГРАНИЧЕНИЯ (СТРОГО СОБЛЮДАТЬ) ---\n"
        "1. ТОЧНАЯ ИДЕНТИФИКАЦИЯ МАТЕРИАЛА:\n"
        f"   - Товар: {title}\n"
        f"   - Категория: {category} (Объем: {volume}).\n"
        "   - Если это ГЕЛЬ: КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО называть его «базой», «лаком» или «гель-лаком». Это моделирующий твердый гель для наращивания, укрепления и коррекции!\n"
        "   - Если это БАЗА: называть базой/каучуковой базой.\n"
        "   - Если это ТОП: называть топом/финишным покрытием.\n\n"
        "2. АССОРТИМЕНТ И РЕКОМЕНДАЦИИ (UPSELL):\n"
        f"   - Рекомендовать к покупке можно ТОЛЬКО: {upsell}.\n"
        f"   - Правило каталога: {ALLOWED_UPSELL_LIST}\n"
        "   - СТРОГО ЗАПРЕЩЕНО рекомендовать праймеры, дегидраторы, пилочки, масло для кутикулы (если его нет в списке) и лампы!\n\n"
        "3. БЕЗОПАСНОСТЬ И СЕРТИФИКАЦИЯ:\n"
        f"   - Формула: {formula}. Не вызывает жжения в лампе, не имеет резкого запаха.\n"
        f"   - Юридический статус: {LEGAL_COMPLIANCE_INFO}\n"
        "   - Если покупатель спрашивает про оригинальность, качество или безопасность — упомяни регистрацию в системе «Честный ЗНАК» и наличие декларации о соответствии ТР ТС 009/2011.\n\n"
        "4. РАБОТА С НЕГАТИВОМ ИЛИ ВОПРОСАМИ ПО ТЕХНИКЕ:\n"
        "   - Если пишут про жжение: напомни, что плотный пигмент при полимеризации требует включения режима Low Heat Mode (пониженной мощности лампы) и нанесения тонкой эластичной подложки (например, каучуковой базы YaSoKo арт. 21000).\n"
        "   - Если пишут про отслойки: напомни про важность правильной подготовки ногтевой пластины и обязательного использования подложки из базы.\n"
        "   - Если повреждена баночка при доставке: прояви искреннюю эмпатию, мягко поясни, что логистику осуществляет склад маркетплейса, и подскажи, что товар можно проверить и заменить в ПВЗ.\n\n"
        "5. СТИЛЬ И ФОРМАТ:\n"
        "   - Длина: строго 3-4 предложения.\n"
        "   - Без шаблонных клише («Мы ценим ваш отзыв», «Уважаемый покупатель»).\n"
        "   - Выведи ТОЛЬКО чистый текст ответа без кавычек, звездочек и служебных пометок.\n\n"
        f"--- ДАННЫЕ ОТЗЫВА ---\n"
        f"Артикул: {art} ({color} {shade})\n"
        f"Состав: {composition}\n"
        f"Текст отзыва: \"\"\"{review_text}\"\"\""
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "maxOutputTokens": 1500,  # Увеличен лимит, чтобы текст не обрывался
            "thinkingConfig": {
                "thinkingBudget": 0   # Отключаем трату токенов на мысли (мгновенный прямой ответ)
            }
        }
    }

    req_data = json.dumps(payload).encode("utf-8")
    proxy_handler = urllib.request.ProxyHandler({
        "http": "http://127.0.0.1:3066",
        "https": "http://127.0.0.1:3066",
    })
    opener = urllib.request.build_opener(proxy_handler)

    last_error_log = []

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with opener.open(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            candidate = body.get("candidates", [{}])[0]
            parts = candidate.get("content", {}).get("parts", [])
            
            # Собираем только реальный текст ответа, отсекая внутренние мысли модели
            text_chunks = [
                p.get("text", "") 
                for p in parts 
                if not p.get("thought", False) and p.get("text")
            ]
            
            if text_chunks:
                reply = "".join(text_chunks).strip()
                if reply:
                    return reply

            finish_reason = candidate.get("finishReason", "UNKNOWN")
            last_error_log.append(f"{model}: {finish_reason}")

        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="ignore")
            last_error_log.append(f"{model} [HTTP {http_err.code}]")
            if http_err.code in (400, 404, 429, 500, 502, 503):
                continue
            return f"Ошибка API Google ({http_err.code}): {err_body}"

        except Exception as exc:
            last_error_log.append(f"{model} [{type(exc).__name__}: {exc}]")
            continue

    return (
        f"Здравствуйте! Благодарим за отзыв и выбор камуфлирующего геля YaSoKo #{shade} ({color})! "
        "Мы рады, что вы оценили комфорт в работе и плотность оттенка. "
        "Для безупречного глянца рекомендуем дополнить маникюр нашим бриллиантовым топом без липкого слоя (арт. 22000). "
        "Желаем вам красивых и стойких работ!"
    )


@reviews_router.message(F.text == "💬 Ответ на отзыв")
@reviews_router.message(Command("review"))
async def cmd_review_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return

    await state.set_state(ReviewForm.waiting_for_review_data)
    await message.answer(
        "💬 <b>Генератор ответов на отзывы покупателей YaSoKo</b>\n\n"
        "Пришлите артикул продавца (например, <code>1001</code>, <code>1028</code> или <code>21000</code>) "
        "либо SKU маркетплейса, а через пробел — текст отзыва.\n\n"
        "<b>Пример:</b>\n"
        "<code>1001 Гель отличный, цвет плотный, самовыравнивается моментально.</code>\n\n"
        "Для отмены отправьте /cancel.",
        parse_mode="HTML",
    )


@reviews_router.message(ReviewForm.waiting_for_review_data, Command("cancel"))
async def cmd_cancel_review(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Ввод отзыва отменен.")


@reviews_router.message(ReviewForm.waiting_for_review_data, F.text)
async def process_review_text(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return

    raw_text = message.text.strip()
    parts = raw_text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "⚠️ Не найден текст отзыва. Укажите сначала артикул, а через пробел — отзыв.\n"
            "<i>Пример: 1001 Отличный гель, плотный цвет.</i>",
            parse_mode="HTML",
        )
        return

    sku_input, review_text = parts[0].strip(), parts[1].strip()

    # 1. Загрузка метаданных из жесткого справочника YaSoKo
    product_meta = get_product_context(sku_input)

    # 2. Если в локальной БД есть более свежее название из синхронизации, дополняем
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Product).where(Product.sku == sku_input)
            res = await session.execute(stmt)
            prod = res.scalar_one_or_none()
            if prod and prod.title and not product_meta.get("title"):
                product_meta["title"] = prod.title
    except Exception:
        pass

    status_msg = await message.answer("⏳ Составляю персонализированный ответ по стандартам YaSoKo...")

    reply_text = await asyncio.to_thread(
        generate_review_reply_llm,
        sku=sku_input,
        product_meta=product_meta,
        review_text=review_text,
    )

    await status_msg.delete()
    await state.clear()

    product_header = f"{product_meta.get('title')} (арт. {sku_input})"
    await message.answer(
        f"✅ <b>Готовый ответ для {product_header}:</b>\n\n"
        f"<i>👇 Нажмите на текст в блоке ниже для моментального копирования:</i>\n\n"
        f"<pre>{reply_text}</pre>",
        parse_mode="HTML",
    )