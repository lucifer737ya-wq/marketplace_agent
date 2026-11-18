import asyncio
import html
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select

from bot.keyboards import get_main_menu_keyboard
from config import settings
from database.crud import get_db_session
from database.models import Product
from products_catalog import (
    ALLOWED_UPSELL_LIST,
    LEGAL_COMPLIANCE_INFO,
    get_catalog_product,
)
from services.common.ai_advisory_board import _extract_model_name, _is_supported_text_model

reviews_router = Router()


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class ReviewForm(StatesGroup):
    waiting_for_review_data = State()


def get_product_context(identifier: str) -> dict:
    """Ищет товар в справочнике по артикулу, штрихкоду или SKU Ozon/WB."""
    clean_id = (identifier or "").strip()
    found = get_catalog_product(clean_id)
    if found:
        return dict(found)

    return {
        "art": clean_id,
        "title": f"Моделирующий гель YaSoKo (арт. {clean_id})",
        "category": "Камуфлирующий гель для наращивания и моделирования ногтей",
        "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE",
        "recommended_upsell": "каучуковая эластичная база YaSoKo (арт. 21000) или топ без липкого слоя (арт. 22000)",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491",
    }


def _build_review_fallback_reply(product_meta: dict) -> str:
    shade = str(product_meta.get("shade") or "").strip()
    color = str(product_meta.get("color_name") or "").strip()
    title = str(product_meta.get("title") or "продукта YaSoKo").strip()
    if shade and color:
        item_label = f"камуфлирующего геля YaSoKo #{shade} ({color})"
    elif shade:
        item_label = f"камуфлирующего геля YaSoKo #{shade}"
    else:
        item_label = title

    return (
        f"Здравствуйте! Благодарим за отзыв и выбор {item_label}! "
        "Мы рады, что вы оценили комфорт в работе и качество покрытия. "
        "Для безупречного глянца рекомендуем дополнить маникюр нашим топом без липкого слоя (арт. 22000). "
        "Желаем вам красивых и стойких работ!"
    )


def _get_review_proxy_openers() -> List[urllib.request.OpenerDirector]:
    openers: List[urllib.request.OpenerDirector] = []
    proxy_url = str(getattr(settings, "LOCAL_PROXY_URL", "") or "").strip()
    if proxy_url:
        openers.append(
            urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        )
    openers.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    return openers


def generate_review_reply_llm(sku: str, product_meta: dict, review_text: str) -> str:
    """Генерация строгого ответа на отзыв с учетом каталога YaSoKo и каскадом моделей."""
    raw_model = _extract_model_name(str(getattr(settings, "GEMINI_MODEL", "") or "gemini-2.5-flash"))
    candidate_pool = [
        raw_model,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ]
    models_to_try = list(dict.fromkeys(m for m in candidate_pool if _is_supported_text_model(m)))
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip().strip('"').strip("'")
    if not api_key:
        return _build_review_fallback_reply(product_meta)

    art = product_meta.get("art", sku)
    shade = product_meta.get("shade", "")
    color = product_meta.get("color_name", "")
    title = product_meta.get("title", f"Продукт YaSoKo {sku}")
    category = product_meta.get("category", "Моделирующий гель для ногтей")
    volume = product_meta.get("volume", "15 г")
    formula = product_meta.get("formula", "HEMA FREE, 11-FREE")
    upsell = product_meta.get("recommended_upsell", "топ без липкого слоя YaSoKo")
    composition = product_meta.get("composition", "Безопасный гипоаллергенный состав")

    shade_suffix = f" ({color} {shade})".strip() if (color or shade) else ""
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
        f"Артикул: {art} {shade_suffix}\n"
        f"Состав: {composition}\n"
        f"Текст отзыва: \"\"\"{review_text}\"\"\""
    )

    for opener in _get_review_proxy_openers():
        for model in models_to_try:
            gen_config: Dict[str, Any] = {
                "temperature": 0.35,
                "maxOutputTokens": 1500,
            }
            if "2.5-flash" in model:
                gen_config["thinkingConfig"] = {"thinkingBudget": 0}

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_config,
            }
            req_data = json.dumps(payload).encode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
            )

            try:
                with opener.open(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8"))

                candidates = body.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    text_chunks = [
                        str(p.get("text") or "")
                        for p in parts
                        if isinstance(p, dict) and not p.get("thought", False) and p.get("text")
                    ]
                    reply = "".join(text_chunks).strip()
                    if reply:
                        return reply

            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403):
                    break
                continue
            except Exception:
                continue

    return _build_review_fallback_reply(product_meta)


@reviews_router.message(F.text == "💬 Ответ на отзыв")
@reviews_router.message(Command("review"))
async def cmd_review_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return

    await state.set_state(ReviewForm.waiting_for_review_data)
    await message.answer(
        "💬 <b>Генератор ответов на отзывы покупателей YaSoKo</b>\n\n"
        "Пришлите артикул продавца (например, <code>1001</code>, <code>1028</code> или <code>21000</code>) "
        "либо SKU маркетплейса, а через пробел — текст отзыва.\n\n"
        "<b>Пример:</b>\n"
        "<code>1001 Гель отличный, цвет плотный, самовыравнивается моментально.</code>\n\n"
        "Для отмены отправьте /cancel или /menu.",
        parse_mode="HTML",
    )


@reviews_router.message(ReviewForm.waiting_for_review_data, Command("cancel", "menu", "start"))
@reviews_router.message(ReviewForm.waiting_for_review_data, F.text == "Отмена")
async def cmd_cancel_review(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❌ Ввод отзыва отменен.", reply_markup=get_main_menu_keyboard())


@reviews_router.message(ReviewForm.waiting_for_review_data, F.text)
async def process_review_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "⚠️ Не найден текст отзыва. Укажите сначала артикул, а через пробел — отзыв.\n"
            "<i>Пример: 1001 Отличный гель, плотный цвет.</i>",
            parse_mode="HTML",
        )
        return

    sku_input, review_text = parts[0].strip(), parts[1].strip()
    product_meta = get_product_context(sku_input)

    try:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == sku_input).limit(1)
            res = await session.execute(stmt)
            prod = res.scalars().first()
            if prod and prod.title and not product_meta.get("title"):
                product_meta["title"] = prod.title
    except Exception:
        pass

    status_msg = await message.answer("⏳ Составляю персонализированный ответ по стандартам YaSoKo...")
    try:
        reply_text = await asyncio.to_thread(
            generate_review_reply_llm,
            sku=sku_input,
            product_meta=product_meta,
            review_text=review_text,
        )
    except Exception as exc:
        try:
            await status_msg.delete()
        except Exception:
            pass
        await state.clear()
        await message.answer(
            f"❌ Ошибка генерации ответа на отзыв: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    try:
        await status_msg.delete()
    except Exception:
        pass
    await state.clear()

    product_header = f"{product_meta.get('title')} (арт. {sku_input})"
    await message.answer(
        f"✅ <b>Готовый ответ для {html.escape(product_header)}:</b>\n\n"
        f"<i>👇 Нажмите на текст в блоке ниже для моментального копирования:</i>\n\n"
        f"<pre>{html.escape(reply_text)}</pre>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
