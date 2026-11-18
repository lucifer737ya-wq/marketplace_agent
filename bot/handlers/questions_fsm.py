import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.keyboards import get_main_menu_keyboard
from config import settings
from products_catalog import get_catalog_product
from services.common.buyer_questions import generate_buyer_question_answer

questions_router = Router()


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class QuestionFSM(StatesGroup):
    waiting_for_question = State()


@questions_router.message(F.text == "❓ Ответ на вопрос")
@questions_router.message(Command("question"))
async def cmd_question_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(QuestionFSM.waiting_for_question)
    await message.answer(
        "💬 <b>Генератор ответов на вопросы покупателей YaSoKo</b>\n\n"
        "Отправьте текст вопроса с Ozon или WB (можно указать артикул, например: "
        "<i>«1001 густой ли гель и печет ли в лампе?»</i>):\n\n"
        "<i>Для отмены отправьте /cancel или /menu</i>",
        parse_mode="HTML",
    )


@questions_router.message(QuestionFSM.waiting_for_question, Command("cancel", "menu", "start"))
@questions_router.message(QuestionFSM.waiting_for_question, F.text == "Отмена")
async def cmd_cancel_question(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_main_menu_keyboard())


@questions_router.message(QuestionFSM.waiting_for_question, F.text)
async def process_question_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Отправьте текст вопроса или /cancel для отмены.")
        return

    words = user_text.split()
    sku = None
    if words:
        first_tok = words[0].strip(".,:;!?#")
        if (first_tok.isdigit() and len(first_tok) in (4, 5)) or get_catalog_product(first_tok) is not None:
            sku = first_tok

    wait_msg = await message.answer("⏳ Технолог YaSoKo формирует экспертный ответ...")
    try:
        answer_text = await generate_buyer_question_answer(user_text, sku=sku)
    except Exception as exc:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await state.clear()
        await message.answer(
            f"❌ Ошибка генерации ответа: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    try:
        await wait_msg.delete()
    except Exception:
        pass

    await state.clear()
    await message.answer(
        f"<b>Готовый ответ для маркетплейса:</b>\n\n"
        f"<code>{html.escape(answer_text)}</code>\n\n"
        f"<i>(Нажмите на текст, чтобы скопировать и вставить в ЛК Ozon/WB)</i>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
