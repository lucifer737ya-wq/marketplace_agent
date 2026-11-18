"""Offline Playwright acceptance tests. CI may set CHROMIUM_EXECUTABLE_PATH."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import async_playwright

from tests.offline import STATE
from services.common import buyer_questions
from services.ozon import browser_reviews as reviews

UI = """<button role="tab" onclick="show('new')">Новые</button>
<button role="tab" onclick="show('viewed')">Просмотренные</button>
<table><thead><tr><th>Дата</th><th>Артикул</th><th>Статус</th><th>Отзыв</th></tr></thead><tbody></tbody></table>
<div role="dialog" hidden></div><script>
let rows = [ ['old','viewed','12.11.2026'], ['unknown','viewed','—'], ['answered','viewed','13.11.2026'],
             ['retry','new','13.11.2026'] ]; let tab='new', saves=0;
function show(t) {tab=t; document.querySelector('tbody').innerHTML=rows.filter(r=>r[1]===t).map(r=>
  `<tr data-review-id="${r[0]}"><td>${r[2]}</td><td>1001</td><td>${t}</td><td onclick="openCard('${r[0]}')">Прекрасный гель</td></tr>`).join(''); }
function openCard(id) {const r=rows.find(r=>r[0]===id); r[1]='viewed';show(tab);
const d=document.querySelector('[role=dialog]');d.hidden=false;d.innerHTML=`<h2>Отзыв на товар</h2><time>${r[2]}</time>
<p>Артикул 1001</p><p>Оценка 4</p><p>Комментарий: Прекрасный гель</p>
${id==='answered'?'<p>Ответ продавца</p><p>Ответили ранее</p>':''}
<button onclick="compose(this)">Ответить на отзыв</button><button onclick="this.parentElement.hidden=true">Закрыть</button>`; }
function compose(button) {button.remove();document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend',
  `<textarea></textarea><button onclick="publish(this)">Отправить</button>`);}
function publish(button) {saves++;const t=document.querySelector('textarea'); const p=document.createElement('p');
 p.textContent=t.value;t.remove();button.remove();if(!window.drop) document.querySelector('[role=dialog]').append(p); }
show(tab);</script>"""


class ReviewDOM(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        pw = await async_playwright().start()
        self.addAsyncCleanup(pw.stop)
        path = os.environ.get("CHROMIUM_EXECUTABLE_PATH") or pw.chromium.executable_path
        if not Path(path).is_file():
            self.skipTest("Install Playwright Chromium or set CHROMIUM_EXECUTABLE_PATH")
        browser = await pw.chromium.launch(
            executable_path=path, args=["--no-sandbox", "--disable-gpu"]
        )
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.set_content(UI)
        self.enterContext(
            patch.object(reviews, "TRACKER_FILE", STATE / (self.id() + ".json"))
        )
        self.enterContext(
            patch.object(
                reviews, "_scroll_main_table_down", AsyncMock(return_value=False)
            )
        )
        self.tracker = reviews._load_tracker()

    async def test_retry_in_viewed_then_confirm_only_once(self):
        with patch.object(
            buyer_questions,
            "generate_review_reply",
            AsyncMock(side_effect=RuntimeError("offline")),
        ) as llm:
            first = await reviews._scan_reviews(self.page, self.tracker, False, 1)
            self.assertEqual(first["replied"], 0)
            llm.assert_awaited_once()  # not the old/undated/already-answered reviews
        self.assertFalse(reviews.TRACKER_FILE.exists())
        with patch.object(
            buyer_questions,
            "generate_review_reply",
            AsyncMock(return_value="Спасибо за отзыв!"),
        ) as llm:
            preview = await reviews._scan_reviews(self.page, self.tracker, True, 1)
            self.assertEqual(preview["reviews"][0]["subtab"], "Просмотренные")
            self.assertFalse(reviews.TRACKER_FILE.exists())
            real = await reviews._scan_reviews(self.page, self.tracker, False, 1)
            self.assertEqual(real["replied"], 1)
            self.assertEqual(
                reviews._load_tracker()["processed_review_ids"], ["review:retry"]
            )
            again = await reviews._scan_reviews(
                self.page, reviews._load_tracker(), False, 1
            )
            self.assertEqual(again["replied"], 0)
        self.assertEqual(await self.page.evaluate("saves"), 1)

    async def test_clear_editor_and_missing_opener_are_not_confirmation(self):
        await self.page.evaluate("window.drop=true")
        with patch.object(
            buyer_questions,
            "generate_review_reply",
            AsyncMock(return_value="Спасибо за отзыв!"),
        ):
            result = await reviews._scan_reviews(self.page, self.tracker, False, 1)
            self.assertEqual(result["status"], "partial")
            self.assertIn("не подтверждена", result["message"])
        self.assertEqual(self.tracker["replied_count"], 0)
        self.assertFalse(reviews.TRACKER_FILE.exists())
        self.assertEqual(await self.page.evaluate("saves"), 1)

    async def test_pagination_and_labeled_drawer_date(self):
        # Visited pages can consist entirely of already-answered reviews.
        await self.page.evaluate("""() => {
            rows = [['answered', 'viewed', '13.11.2026']]; show('viewed');
            const next = document.createElement('button'); next.textContent='Далее';
            next.onclick = () => { rows=[['page2','viewed','—']]; show('viewed');next.disabled=true; };
            document.body.append(next);
            const open = openCard; openCard = id => { open(id); if (id==='page2')
                document.querySelector('time').outerHTML = '<label>Дата отзыва:</label><span>13.11.2026</span>'; };
        }""")
        with patch.object(
            buyer_questions, "generate_review_reply", AsyncMock(return_value="Спасибо!")
        ):
            res = await reviews._scan_reviews(self.page, self.tracker, False, 1)
        self.assertEqual(res["replied"], 1)
        self.assertEqual(
            reviews._load_tracker()["processed_review_ids"], ["review:page2"]
        )

    async def test_question_date_gate_with_existing_click_and_submit_workflow(self):
        content = """<style>body{padding-top:150px}table{width:100%;border-spacing:0}td{height:44px}
          [role=dialog]{position:fixed;inset:0;background:white}textarea{position:fixed;left:880px;top:620px}
          #x{position:fixed;right:0;top:0;height:50px;width:56px}</style>
          <button>Вопросы и ответы</button><button>Новые</button><table><thead><tr>
          <th>Дата</th><th>Артикул</th><th>Статус</th><th>Вопрос</th><th>Ответы</th></tr></thead><tbody></tbody></table>
          <div role=dialog hidden><button id=x onclick="this.parentElement.hidden=true">Закрыть</button>
          <div>Артикул 1001</div><time></time><textarea></textarea><button id=send>Отправить</button></div>
          <script>for(const [id,stamp] of [['old','12.11.2026'],['unknown','—'],['new','13.11.2026']]) {
            const r=document.querySelector('tbody').insertRow();r.innerHTML=`<td>${stamp}</td><td>1001</td>
              <td>Новый</td><td>${id}: можно ли использовать после 18.11.2026?</td><td>0</td>`;
            r.onclick=()=>{document.querySelector('[role=dialog]').hidden=false;
              document.querySelector('time').textContent=stamp;
              document.querySelector('#send').onclick=()=>{r.cells[4].textContent='1';
                if(document.querySelector('textarea').value==='Ответ из теста') document.title=id;};};}</script>"""
        await self.page.set_viewport_size(reviews.VIEWPORT)
        context = self.page.context
        await context.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=content
            ),
        )
        # Route every navigation locally, but use real locators, geometry, filling and click handlers.
        result = []
        self.page.on("close", lambda _: result.append("closed"))
        api = MagicMock()
        api.chromium.launch_persistent_context = AsyncMock(return_value=context)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=api)
        manager.__aexit__ = AsyncMock(return_value=False)
        self.page.on("console", lambda msg: result.append(msg.text))
        await self.page.add_init_script(
            "new MutationObserver(()=>{if(document.title) console.log('published:'+document.title);}).observe(document,{subtree:true,childList:true});"
        )
        delay = MagicMock(wraps=asyncio)
        delay.sleep = AsyncMock()
        with (
            patch("playwright.async_api.async_playwright", return_value=manager),
            patch.object(reviews, "asyncio", delay),
            patch.object(
                buyer_questions,
                "generate_buyer_question_answer",
                AsyncMock(return_value="Ответ из теста"),
            ) as llm,
        ):
            res = await reviews.auto_reply_unanswered_questions(max_per_run=1)
        self.assertEqual(res["status"], "success", res.get("message"))
        self.assertEqual(res["replied"], 1)
        self.assertTrue(llm.await_args.kwargs["question_text"].startswith("new:"))
        llm.assert_awaited_once()
        self.assertIn("published:new", result)
        self.assertIn("closed", result)
