import os
import asyncio
from playwright.async_api import async_playwright
from config import LOGS_DIR

BROWSER_DATA_DIR = LOGS_DIR / "ozon_browser_profile"

DIRECT_BROWSER_ARGS = [
    "--no-proxy-server",
    "--proxy-server=direct://",
    "--proxy-bypass-list=*",
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
]


def _get_direct_env():
    clean_env = os.environ.copy()
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        clean_env.pop(var, None)
    return clean_env


async def main():
    print("=" * 60)
    print("🎯 ЗАПУСК ИНТЕРАКТИВНОГО КАЛИБРАТОРА КООРДИНАТ OZON")
    print("1. В правом верхнем углу окна появится плашка с текущими X и Y курсора.")
    print("2. Кликните мышью по крестику закрытия шторки.")
    print("3. Кликните мышью по ячейке с текстом нужного отзыва.")
    print("Точные координаты каждого клика сразу отобразятся прямо в этом окне консоли.")
    print("Для завершения просто закройте окно браузера.")
    print("=" * 60 + "\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            args=DIRECT_BROWSER_ARGS,
            env=_get_direct_env(),
            viewport={"width": 1440, "height": 900},  # Ровно то же разрешение, что и в агенте!
        )
        page = await context.new_page()

        # Привязка перехватчика кликов в Python-консоль
        await page.expose_function("reportCoordinates", lambda x, y, tag, text: print(
            f"📍 [КЛИК]: X = {x:4d} | Y = {y:4d} | Элемент: <{tag}> | Текст: {text[:40]}"
        ))

        # Переход в кабинет Ozon Seller
        await page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded", timeout=60000)

        # Внедрение HUD-индикатора и обработчика кликов на страницу
        js_code = """
        () => {
            // Создаем плавающий счетчик координат
            const hud = document.createElement('div');
            hud.id = 'coord-hud';
            hud.style.position = 'fixed';
            hud.style.top = '10px';
            hud.style.right = '10px';
            hud.style.zIndex = '9999999';
            hud.style.backgroundColor = 'rgba(0, 0, 0, 0.85)';
            hud.style.color = '#00FF66';
            hud.style.fontFamily = 'monospace';
            hud.style.fontSize = '16px';
            hud.style.fontWeight = 'bold';
            hud.style.padding = '8px 14px';
            hud.style.borderRadius = '8px';
            hud.style.border = '2px solid #00FF66';
            hud.style.pointerEvents = 'none';
            hud.innerText = 'X: 0 | Y: 0';
            document.body.appendChild(hud);

            // Обновление при движении мыши
            window.addEventListener('mousemove', (e) => {
                hud.innerText = `X: ${e.clientX} | Y: ${e.clientY}`;
            });

            // Отправка в Python при клике
            window.addEventListener('click', (e) => {
                const target = e.target;
                const text = (target.innerText || target.getAttribute('aria-label') || '').trim().replace(/\\n/g, ' ');
                window.reportCoordinates(e.clientX, e.clientY, target.tagName.toLowerCase(), text);
            }, true);
        }
        """

        await page.evaluate(js_code)

        # Переподключение счетчика при переходе по вкладкам
        page.on("load", lambda: asyncio.create_task(page.evaluate(js_code)))

        # Ожидание закрытия браузера пользователем
        while len(context.pages) > 0:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())