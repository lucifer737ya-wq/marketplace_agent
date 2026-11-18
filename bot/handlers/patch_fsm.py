import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.keyboards import get_main_menu_keyboard
from config import settings
from maintenance.supervisor import apply_code_patch, get_project_tree_overview

patch_router = Router()


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class PatchFSM(StatesGroup):
    waiting_for_instruction = State()


@patch_router.message(F.text == "🛠 Редактировать код")
async def cmd_patch_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(PatchFSM.waiting_for_instruction)
    tree_info = get_project_tree_overview()
    await message.answer(
        "🛠 <b>Режим интеллектуального редактирования кода</b>\n\n"
        "📂 <b>Структура вашего проекта:</b>\n"
        f"{tree_info}\n\n"
        "✍️ <b>Как отправить задачу:</b>\n"
        "• Вы можете <b>просто описать проблему</b> обычным текстом (ИИ сам найдет нужный файл по смыслу задачи),\n"
        "• Либо указать путь к файлу в начале (например: <code>services/wb/statistics_api.py изменить...</code>).\n\n"
        "<i>Для отмены отправьте /cancel или /menu</i>",
        parse_mode="HTML",
    )


@patch_router.message(PatchFSM.waiting_for_instruction, Command("cancel", "menu", "start"))
@patch_router.message(PatchFSM.waiting_for_instruction, F.text == "Отмена")
async def cmd_cancel_patch(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_main_menu_keyboard())


@patch_router.message(PatchFSM.waiting_for_instruction, F.text)
async def process_patch_instruction(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Отправьте текстовое описание изменений или /cancel для отмены.")
        return

    parts = raw.split(maxsplit=1)
    target_file = None
    instruction = raw

    if len(parts) == 2 and (parts[0].endswith(".py") or "/" in parts[0] or "\\" in parts[0]):
        target_file = parts[0]
        instruction = parts[1]

    wait_msg = await message.answer("⏳ Определяю затронутый модуль, генерирую полный код и проверяю AST-синтаксис...")
    try:
        success, detail = await apply_code_patch(instruction=instruction, target_file_rel=target_file)
    except Exception as exc:
        success, detail = False, f"Ошибка при обращении к LLM: {exc}"

    try:
        await wait_msg.delete()
    except Exception:
        pass

    await state.clear()
    if success:
        await message.answer(
            f"✅ <b>Патч успешно применен:</b>\n{detail}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка при применении патча:</b>\n<code>{html.escape(str(detail))}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
