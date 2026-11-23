"""Один раз откройте Chrome с профилем «Пробива конкурентов» и пройдите антибот WB/Ozon вручную."""
import asyncio
import os

os.environ["LISTINGS_BROWSER_HEADLESS"] = "false"  # окно должно быть видно (до импорта config)

from config import prepare_runtime_dirs  # noqa: E402
from services.competitors.browser_listings import LISTING_PROFILE_DIR, _launch, _playwright_factory  # noqa: E402

QUERY = "%D0%B3%D0%B5%D0%BB%D1%8C%20%D0%B4%D0%BB%D1%8F%20%D0%BD%D0%BE%D0%B3%D1%82%D0%B5%D0%B9"


async def main() -> None:
    prepare_runtime_dirs()
    from services.common.direct_guard import foreign_exit_message

    print(f"[Login] Профиль: {LISTING_PROFILE_DIR}_<браузер>")
    problem = await foreign_exit_message()
    if problem:
        print("\n[!] " + problem + "\n")
        print("VPN выключать не требуется — настройте direct-правила и запустите login_wb.bat снова.")
        return
    print("Откроется Chrome (выход с РФ-IP): пройдите проверку на WB и Ozon, дождитесь выдачи, закройте окно.")
    async with _playwright_factory()() as p:
        context = await _launch(p)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"https://www.wildberries.ru/catalog/0/search.aspx?search={QUERY}", timeout=60000)
        other = await context.new_page()
        await other.goto(f"https://www.ozon.ru/search/?text={QUERY}", timeout=60000)
        while context.pages:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
