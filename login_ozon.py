"""Один раз войдите в seller.ozon.ru в профиле бота — без этого автоответы на отзывы и вопросы не работают."""
import asyncio

from config import prepare_runtime_dirs
from services.ozon.browser_reviews import run_interactive_login

if __name__ == "__main__":
    prepare_runtime_dirs()
    asyncio.run(run_interactive_login())
