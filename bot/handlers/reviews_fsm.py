import html
import logging
from contextlib import suppress

from aiogram.exceptions import TelegramAPIError
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
from products_catalog import get_catalog_product
from services.common.buyer_questions import generate_review_reply

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


async def generate_review_reply_llm(sku: str, product_meta: dict, review_text: str) -> str:
    """Тот же генератор, что у автоответов Ozon: единый тон, приветствие и пожелание.

    Раньше бот использовал отдельный сокращённый промпт, и ответы в Telegram
    расходились с опубликованными (без приветствия/завершения, с советом «присмотреться
    к другим линейкам»).
    """
    art = product_meta.get("art", sku)
    try:
        return await generate_review_reply(review_text, None, str(art or sku))
    except RuntimeError as exc:
        raise RuntimeError(
            f"Не удалось подготовить безопасный ответ: {exc}"
        ) from exc


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
    except Exception as exc:
        logging.getLogger(__name__).warning("Каталог БД недоступен: %s", type(exc).__name__)

    status_msg = await message.answer("⏳ Составляю персонализированный ответ по стандартам YaSoKo...")
    try:
        reply_text = await generate_review_reply_llm(
            sku=sku_input,
            product_meta=product_meta,
            review_text=review_text,
        )
    except Exception as exc:
        with suppress(TelegramAPIError):
            await status_msg.delete()
        await state.clear()
        await message.answer(
            f"❌ Ошибка генерации ответа на отзыв: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    with suppress(TelegramAPIError):
        await status_msg.delete()
    await state.clear()

    product_title = product_meta.get("title") or product_meta.get("name") or "товар YaSoKo"
    product_header = f"{product_title} (арт. {sku_input})"
    await message.answer(
        f"✅ <b>Готовый ответ для {html.escape(product_header)}:</b>\n\n"
        f"<i>👇 Нажмите на текст в блоке ниже для моментального копирования:</i>\n\n"
        f"<pre>{html.escape(reply_text)}</pre>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
