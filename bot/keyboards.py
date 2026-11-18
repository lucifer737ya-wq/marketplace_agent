from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="📊 P&L отчет (Месяц)"),
            KeyboardButton(text="📦 Отчет по заказам"),
        ],
        [
            KeyboardButton(text="🏆 ТОП-5 и Убыточные"),
            KeyboardButton(text="🧠 ИИ-Советник (Разбор)"),
        ],
        [
            KeyboardButton(text="🔎 Пробив конкурентов"),
            KeyboardButton(text="❓ Ответ на вопрос"),
        ],
        [
            KeyboardButton(text="💬 Ответ на отзыв"),
            KeyboardButton(text="🔄 Синхронизировать МП"),
        ],
        [
            KeyboardButton(text="🔍 Аудит API"),
            KeyboardButton(text="🛠 Самопроверка и автопочинка"),
        ],
        [
            KeyboardButton(text="📦 Себестоимость (поштучно)"),
            KeyboardButton(text="🚚 Первая миля"),
        ],
        [
            KeyboardButton(text="📑 Массовая загрузка себестоимости"),
            KeyboardButton(text="🛠 Редактировать код"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        persistent=True,
    )
