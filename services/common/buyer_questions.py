import asyncio
import re
from typing import Optional

from products_catalog import get_catalog_product
from services.common.gemini import GeminiUnavailable, generate

BRAND_MATRIX_RULES = """
ПОДТВЕРЖДЁННАЯ БАЗА ЗНАНИЙ ПО СЕРИЯМ БРЕНДА YASOKO (банки 15 г):
1. Классификация серий по артикулам:
   - 1001–1036: классические моделирующие гели без добавок и шиммера (плотный молочный #01, камуфлирующие нюдовые, бежевые, розовые оттенки, прозрачный).
   - 1101–1307: моделирующие гели с деликатным светоотражающим шиммером.
   - 1401–1408: моделирующие гели без шиммера ярких неоновых оттенков.
   - 1501–1512: моделирующие гели-желе с шиммером и глиттером (плотная текстура желе для выкладки бортиков, углов и длины без растекания).
2. Консистенция и свойства:
   - Гели 1001–1408: средне-вязкие, комфортно самовыравниваются за 3–5 секунд, не затекают на валики.
   - Гели-желе 1501–1512: плотное неподвижное желе, держит высоту и бортики, не течёт.
   - Жёсткость: в тонком слое гель пластичный (амортизирует у кутикулы), в толстом слое образует жёсткий каркас и держит углы квадрата.
3. Палитра и сопутствующие позиции:
   - Если ищут цвет без блёсток — рекомендуем классическую серию 1001–1036.
   - Праймер сейчас не разрабатывается; наклейки на типсы в комплект не входят.
4. Жёсткие ограничения безопасности:
   - Не выдумывай точные даты поставок, неподтверждённые сертификаты или состав вне карточки.
   - При вопросах о наличии/поступлении отвечай по существу (предложи добавить магазин в избранное для уведомления о поступлении) и не пиши про вязкость и лампы.
   - Категорически запрещено советовать разбавление, спирт, растворители, нагрев продукта или непроверенные процедуры.
"""


async def call_fast_gemini(prompt: str, raise_on_error: bool = False) -> str:
    # Параметр сохранён для совместимости, но фиктивный ответ недопустим.
    # Лимит 1200: модели 3.x тратят часть токенов на «размышления», при 800
    # ответ обрезался по MAX_TOKENS и отбрасывался как пустой.
    text, _ = await generate(prompt, max_tokens=1200, fast=True, timeout=30)
    return text


# --- Фильтр опасных советов -------------------------------------------------
# Прежний фильтр искал подстроки «спирт/нагре» во всём ответе и отклонял
# безопасные фразы вида «не разбавляйте гель спиртом» или «в лампе гель может
# нагреваться — это нормально». Теперь проверяется каждое предложение отдельно:
# отрицание («не», «нельзя», «без») снимает флаг, а «нагре*» рядом с лампой
# и полимеризацией — описание процесса, а не совет подогревать продукт.
_HARD_RISK_RE = re.compile(
    r"спирт|растворител|ацетон|разбав|подогре|подогрев|обезжирь", re.IGNORECASE
)
_HEAT_RE = re.compile(r"нагре|нагрев|прогре|прогрев", re.IGNORECASE)
_NEGATION_RE = re.compile(
    r"\b(?:не|нельзя|запрещ\w*|избегайте|избегать|без|никогда|ни в коем случае)\b",
    re.IGNORECASE,
)
_LAMP_CONTEXT_RE = re.compile(
    r"лампа|лампе|лампы|полимериз|тепл|жж[её]т|печ[её]т|ощущени|ощущать|реакци",
    re.IGNORECASE,
)


def has_unsafe_advice(text: str) -> bool:
    """True, если в ответе есть совет разбавлять/подогревать/протирать продукт."""
    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", text or ""):
        if not sentence.strip() or _NEGATION_RE.search(sentence):
            continue
        if _HARD_RISK_RE.search(sentence):
            return True
        if _HEAT_RE.search(sentence) and not _LAMP_CONTEXT_RE.search(sentence):
            return True
    return False


_STRICT_SUFFIX = (
    "\n\nВАЖНО: в ответе вообще не упоминай спирт, растворители, разбавление, "
    "подогрев и нагрев продукта. Только факты из базы знаний выше."
)
_REVIEW_CLOSING = "Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова."


def _ensure_review_shape(reply: str) -> str:
    """Приветствие и завершающее пожелание обязательны по ТЗ (раньше терялись)."""
    text = reply.strip()
    if not text.lower().startswith("здравствуйте"):
        text = "Здравствуйте! " + text
    if "желаем" not in text.lower()[-200:]:
        text = text.rstrip() + " " + _REVIEW_CLOSING
    return text


def _build_sku_context(sku: Optional[str], label: str) -> str:
    if not sku:
        return ""
    sku_clean = str(sku).strip()
    if not sku_clean:
        return ""
    meta = get_catalog_product(sku_clean)
    if meta:
        art = meta.get("art") or sku_clean
        name = meta.get("name") or meta.get("title") or ""
        return f"{label}: Артикул {art}. Название: {name}."
    return f"{label}: Артикул {sku_clean}."


async def generate_buyer_question_answer(question_text: str, sku: Optional[str] = None) -> str:
    sku_context = _build_sku_context(sku, "Товар покупателя")

    prompt = f"""
Ты — профессиональный консультант-технолог бренда YaSoKo.
Ответь на вопрос покупателя на Ozon или Wildberries.

{BRAND_MATRIX_RULES}
{sku_context}

РЕАЛЬНЫЙ ВОПРОС ПОКУПАТЕЛЯ:
«{question_text}»

ГЛАВНЫЕ ТРЕБОВАНИЯ:
1. Поздоровайся и дай конкретный, полезный ответ по матрице YaSoKo и данным карточки выше.
2. Если вопрос выходит за рамки известных характеристик, честно попроси уточнить у продавца; не обещай конкретные даты поставок.
3. Не упоминай спирт, растворители, разбавление и нагрев продукта. Объём: 2–4 предложения.
4. Не предлагай другие линейки и товары, если об этом не спросили.
"""
    last_problem = "Gemini вернул пустой ответ; публикация отменена"
    for attempt in range(3):
        reply = await call_fast_gemini(
            prompt + (_STRICT_SUFFIX if attempt else ""), raise_on_error=True
        )
        if not reply.strip():
            last_problem = "Gemini вернул пустой ответ; публикация отменена"
            continue
        if has_unsafe_advice(reply):
            last_problem = "Ответ содержит неподтверждённый совет; публикация отменена"
            continue
        return reply
    raise RuntimeError(last_problem)


async def generate_review_reply(review_text: str, rating: Optional[int], sku: Optional[str] = None) -> str:
    sku_context = _build_sku_context(sku, "Товар из отзыва")

    prompt = f"""
Ты — специалист контроля качества бренда YaSoKo.
Напиши официальный ответ на отзыв покупателя на Ozon (оценка: {rating if rating is not None else "не указана, не придумывай"}).

{BRAND_MATRIX_RULES}
{sku_context}

ОТЗЫВ ПОКУПАТЕЛЯ:
«{review_text}»

ТРЕБОВАНИЯ:
0. Не упоминай спирт, растворители, разбавление, подогрев и нагрев продукта.
1. Начни с вежливого приветствия: «Здравствуйте!», поблагодари за выбор бренда YaSoKo и обратную связь.
2. Персонализируй ответ: учти серию товара по артикулу ({sku or ''}) и конкретные детали из комментария покупателя (оттенок, консистенция, результат).
3. Если покупатель отмечает плотность/густоту — тактично поясни особенность серии (средне-вязкий гель 1001–1408 или плотное желе 1501–1512) без запрещённых советов.
4. Если оценка 1–3 — выскажи сожаление и предложи написать в чат магазина для разбора; не спорь и не обвиняй покупателя.
5. Не предлагай другие линейки и товары: упоминай только то, что есть в базе знаний и нужно по сути отзыва.
6. Заверши тёплым пожеланием: «{_REVIEW_CLOSING}».
7. Объём: 3–4 предложения.
"""
    last_error: Optional[RuntimeError] = None
    for attempt in range(3):
        try:
            reply = await call_fast_gemini(
                prompt + (_STRICT_SUFFIX if attempt else ""), raise_on_error=True
            )
            if not reply.strip():
                raise RuntimeError("Gemini вернул пустой ответ")
            if has_unsafe_advice(reply):
                raise RuntimeError("Ответ содержит неподтвержденный совет; публикация отменена")
            return _ensure_review_shape(reply)
        except GeminiUnavailable:
            raise  # все маршруты уже перебраны внутри generate(); повторы только тратят время
        except RuntimeError as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    raise last_error or RuntimeError("Не удалось подготовить ответ")
