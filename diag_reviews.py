"""Диагностика + запись координат для ответов на отзывы Ozon (запуск: diag_reviews.bat).

Что делает:
  1. Открывает кабинет Ozon в том же профиле/разрешении (1440x900), что и агент.
  2. После вашего Enter снимает «слепок» таблицы: как прочитана шапка, какие ячейки в строках
     (индекс, текст, есть ли ссылка, X/ширина), сколько таблиц/ARIA-заголовков на странице,
     HTML шапки и первой строки.
  3. Просит по очереди кликнуть 6 элементов. Для КАЖДОГО клика записывает координаты (X, Y) и
     описание элемента (тег, текст, aria-label, data-testid, цепочка родителей, индекс ячейки).
  4. Пишет logs/review_click_points.json (его читает агент) и logs/review_diag.json
     (его достаточно прислать для точной настройки), а в конце печатает итог прямо в консоль.

В поле ответа НИЧЕГО не вводите и ответ не отправляйте — достаточно кликнуть по значку.
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

from config import LOGS_DIR, prepare_runtime_dirs
from services.ozon.browser_reviews import (
    BROWSER_DATA_DIR, DIRECT_BROWSER_ARGS, REVIEW_POINTS_FILE, REVIEWS_URL, VIEWPORT,
    _get_direct_env, _header_texts, _row_cells_dump,
)

DIAG_FILE = LOGS_DIR / "review_diag.json"

STEPS = [
    ("header", "Кликните по ЗАГОЛОВКУ столбца «Отзыв» (в шапке таблицы)"),
    ("cell", "Кликните по ТЕКСТУ в столбце «Отзыв» нужной строки (не по названию товара!)"),
    ("reply", "Кликните по кнопке «Ответить на отзыв» в открывшемся окне"),
    ("input", "Кликните по области, куда вводится ответ (ничего не печатайте)"),
    ("send", "Кликните по значку-самолётику справа от области (поле пустое — ничего не уйдёт)"),
    ("close", "Кликните по крестику в правом верхнем углу окна"),
]

HUD_JS = r"""
() => {
  if (window.__hud) return; window.__hud = true;
  const hud = document.createElement('div');
  hud.style.cssText = 'position:fixed;top:8px;right:60px;z-index:2147483647;background:#000c;color:#0f6;' +
    'font:bold 15px monospace;padding:6px 12px;border:2px solid #0f6;border-radius:8px;pointer-events:none';
  hud.innerText = 'X: 0 | Y: 0';
  document.documentElement.appendChild(hud);
  const path = el => {
    const out = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && i < 6; n = n.parentElement, i++) {
      let s = n.tagName.toLowerCase();
      if (n.id) s += '#' + n.id;
      const cls = (typeof n.className === 'string' ? n.className : '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      const tid = n.getAttribute('data-testid'); if (tid) s += '[data-testid=' + tid + ']';
      const role = n.getAttribute('role'); if (role) s += '[role=' + role + ']';
      out.push(s);
    }
    return out.join(' < ');
  };
  addEventListener('mousemove', e => { hud.innerText = `X: ${e.clientX} | Y: ${e.clientY}`; });
  addEventListener('click', e => {
    const t = e.target;
    const td = t.closest('td,th,[role=cell],[role=gridcell],[role=columnheader]');
    const row = td ? td.parentElement : null;
    const b = t.getBoundingClientRect();
    const near = (sel, attr) => { const n = t.closest(sel); return n ? (n.getAttribute(attr) || '') : ''; };
    window.reportClick({
      x: e.clientX, y: e.clientY,
      tag: t.tagName.toLowerCase(),
      text: ((t.innerText || '').trim().replace(/\s+/g, ' ')).slice(0, 80),
      ariaLabel: t.getAttribute('aria-label') || near('[aria-label]', 'aria-label'),
      testid: t.getAttribute('data-testid') || near('[data-testid]', 'data-testid'),
      role: t.getAttribute('role') || '',
      insideLink: !!t.closest('a[href]'),
      cellIndex: td && row ? Array.from(row.children).indexOf(td) : null,
      cellsInRow: row ? row.children.length : null,
      rect: [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)],
      path: path(t),
    });
  }, true);
}
"""

_SNAPSHOT_JS = r"""() => {
  const q = s => Array.from(document.querySelectorAll(s));
  const head = document.querySelector('thead');
  const row = document.querySelector('tbody tr');
  return {
    tables: q('table').length,
    theadCount: q('thead').length,
    columnheaders: q('[role=columnheader]').map(e => (e.innerText || '').trim()),
    grids: q('[role=grid],[role=table]').length,
    theadHtml: head ? head.outerHTML.slice(0, 3000) : '',
    firstRowHtml: row ? row.outerHTML.slice(0, 3500) : '',
  };
}"""


async def main() -> None:
    prepare_runtime_dirs()
    diag = {"viewport": VIEWPORT, "clicks": []}
    points = {}
    state = {"i": 0, "armed": False}
    done = asyncio.Event()

    def prompt() -> None:
        i = state["i"]
        if i >= len(STEPS):
            REVIEW_POINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            REVIEW_POINTS_FILE.write_text(json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8")
            DIAG_FILE.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
            print("\n" + "=" * 60)
            print("ГОТОВО. Координаты (агент читает их сам):")
            print(json.dumps(points, ensure_ascii=False))
            print(f"Полная диагностика: {DIAG_FILE}")
            print("Пришлите содержимое этого файла (или текст выше).")
            print("=" * 60)
            done.set()
            return
        print(f"\nШаг {i + 1}/{len(STEPS)} [{STEPS[i][0]}]: {STEPS[i][1]}")

    def on_click(info) -> None:
        if not state["armed"] or state["i"] >= len(STEPS):
            return
        name = STEPS[state["i"]][0]
        points[name] = [int(info["x"]), int(info["y"])]
        diag["clicks"].append({"step": name, **info})
        print(f"   [клик] {name}: X={info['x']} Y={info['y']} <{info['tag']}> «{info['text'][:40]}» "
              f"ячейка={info['cellIndex']}/{info['cellsInRow']} ссылка={info['insideLink']}")
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
        print("Окно 1440x900 — не меняйте его размер.")
        print("Дождитесь, пока в окне загрузится ТАБЛИЦА отзывов (вкладка «Все отзывы»/«Новые»).")
        await asyncio.to_thread(input, "Когда таблица видна — нажмите Enter здесь, в консоли... ")

        try:
            rows = page.locator("tbody tr")
            n_rows = await rows.count()
            first = rows.first if n_rows else None
            diag["rows_on_page"] = n_rows
            diag["headers_as_agent_sees_them"] = await _header_texts(page, first)
            diag["first_rows_cells"] = [await _row_cells_dump(rows.nth(i)) for i in range(min(n_rows, 3))]
            diag.update(await page.evaluate(_SNAPSHOT_JS))
            print(f"\nСтрок в таблице: {n_rows}")
            print(f"Шапка глазами агента: {diag['headers_as_agent_sees_them']}")
            print(f"Таблиц: {diag['tables']}, ARIA-заголовков: {len(diag['columnheaders'])}")
            if diag["first_rows_cells"]:
                for c in diag["first_rows_cells"][0]:
                    print(f"   ячейка {c['i']}: x={c['x']} w={c['w']} ссылка={c['link']} «{c['text'][:40]}»")
        except Exception as exc:  # noqa: BLE001 — снимок вспомогательный, клики важнее
            diag["snapshot_error"] = f"{type(exc).__name__}: {exc}"
            print(f"Снимок таблицы не снялся: {diag['snapshot_error']}")

        state["armed"] = True
        prompt()
        while not done.is_set() and context.pages:
            await asyncio.sleep(0.5)
        if not done.is_set():  # окно закрыли раньше времени — сохраняем, что успели
            DIAG_FILE.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Окно закрыто до конца. Частичная диагностика: {DIAG_FILE}")
        try:
            await context.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
