from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from services.common.buyer_questions import generate_buyer_question_answer
from bot.keyboards import get_main_menu_keyboard

questions_router = Router()


class QuestionFSM(StatesGroup):
    waiting_for_question = State()


@questions_router.message(F.text == "❓ Ответ на вопрос")
async def cmd_question_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.TG_ADMIN_ID:
        return
    await state.set_state(QuestionFSM.waiting_for_question)
    await message.answer(
        "💬 <b>Генератор ответов на вопросы покупателей YaSoKo</b>\n\n"
        "Отправьте текст вопроса с Ozon или WB (можно указать артикул, например: <i>«1001 густой ли гель и печет ли в лампе?»</i>):\n\n"
        "<i>Для отмены отправьте /menu</i>",
        parse_mode="HTML",
    )


@questions_router.message(QuestionFSM.waiting_for_question)
async def process_question_text(message: Message, state: FSMContext) -> None:
    if message.text in ("/menu", "/start", "Отмена"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_menu_keyboard())
        return

    user_text = message.text.strip()
    words = user_text.split()
    sku = None
    if words and words[0].isdigit() and len(words[0]) in (4, 5):
        sku = words[0]

    wait_msg = await message.answer("⏳ Технолог YaSoKo формирует экспертный ответ...")
    answer_text = await generate_buyer_question_answer(user_text, sku=sku)
    await wait_msg.delete()

    await message.answer(
        f"<b>Готовый ответ для маркетплейса:</b>\n\n"
        f"<code>{answer_text}</code>\n\n"
        f"<i>(Нажмите на текст, чтобы скопировать и вставить в ЛК Ozon/WB)</i>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
    await state.clear()