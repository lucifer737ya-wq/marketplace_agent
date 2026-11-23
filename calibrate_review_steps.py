"""Пошаговая калибровка координат для ответа на отзыв Ozon (резерв, если DOM-поиск не сработал).

Запуск: calibrate_review_steps.bat  (или venv\\Scripts\\python.exe calibrate_review_steps.py)

Вы по очереди кликаете по 5 элементам — скрипт запоминает координаты и сохраняет их в
logs/review_click_points.json. Агент использует их ТОЛЬКО как запасной вариант для того этапа,
где элемент не найден по DOM (OZON_REVIEWS_CLICK_MODE=auto). Режим «только DOM» — dom.

Порядок (в поле ответа НИЧЕГО НЕ ВВОДИТЕ, ответ не отправляйте — достаточно кликнуть по значку):
  1. cell   — текст в столбце «Отзыв» нужной строки (не название товара!)
  2. reply  — кнопка «Ответить на отзыв» в открывшемся окне
  3. input  — область для текста ответа
  4. send   — значок-самолётик справа от области
  5. close  — крестик в правом верхнем углу окна
"""

import asyncio
import json

from playwright.async_api import async_playwright

from config import prepare_runtime_dirs
from services.ozon.browser_reviews import (
    BROWSER_DATA_DIR, DIRECT_BROWSER_ARGS, REVIEW_POINTS_FILE, REVIEWS_URL, VIEWPORT, _get_direct_env,
)

STEPS = [
    ("cell", "Кликните по ТЕКСТУ в столбце «Отзыв» (не по названию товара)"),
    ("reply", "Кликните по кнопке «Ответить на отзыв» в открывшемся окне"),
    ("input", "Кликните по области, куда вставляется ответ"),
    ("send", "Кликните по значку-самолётику справа от области (поле пустое — ничего не уйдёт)"),
    ("close", "Кликните по крестику в правом верхнем углу окна"),
]

HUD_JS = """
() => {
  if (window.__hud) return; window.__hud = true;
  const hud = document.createElement('div');
  hud.style.cssText = 'position:fixed;top:8px;right:60px;z-index:2147483647;background:#000c;color:#0f6;' +
    'font:bold 15px monospace;padding:6px 12px;border:2px solid #0f6;border-radius:8px;pointer-events:none';
  hud.innerText = 'X: 0 | Y: 0';
  document.documentElement.appendChild(hud);
  addEventListener('mousemove', e => { hud.innerText = `X: ${e.clientX} | Y: ${e.clientY}`; });
  addEventListener('click', e => window.reportClick(e.clientX, e.clientY), true);
}
"""


async def main() -> None:
    prepare_runtime_dirs()
    points = {}
    done = asyncio.Event()
    state = {"i": 0}

    def prompt() -> None:
        i = state["i"]
        if i >= len(STEPS):
            REVIEW_POINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            REVIEW_POINTS_FILE.write_text(json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n✅ Готово. Сохранено в {REVIEW_POINTS_FILE}:\n{json.dumps(points, ensure_ascii=False)}")
            done.set()
            return
        print(f"\nШаг {i + 1}/{len(STEPS)} [{STEPS[i][0]}]: {STEPS[i][1]}")

    def on_click(x, y) -> None:
        i = state["i"]
        if i >= len(STEPS):
            return
        name = STEPS[i][0]
        points[name] = [int(x), int(y)]
        print(f"   📍 {name}: X={int(x)} Y={int(y)}")
        state["i"] += 1
        prompt()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR), headless=False, args=DIRECT_BROWSER_ARGS,
            env=_get_direct_env(), viewport=VIEWPORT,
        )
        await context.expose_function("reportClick", on_click)
        await context.add_init_script(f"document.addEventListener('DOMContentLoaded', {HUD_JS});")
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=60000)
        print("Окно 1440x900 — не меняйте его размер. Закройте окно браузера, чтобы выйти.")
        prompt()
        while not done.is_set() and context.pages:
            await asyncio.sleep(0.5)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
