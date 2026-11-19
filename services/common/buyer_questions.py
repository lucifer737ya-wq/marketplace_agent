import asyncio
from typing import Optional

from products_catalog import get_catalog_product
from services.common.gemini import generate

BRAND_MATRIX_RULES = """
Используй только информацию, явно указанную в названии товара и тексте покупателя.
Не выдумывай наличие, сроки поставки, цвет, состав, сертификаты, совместимость,
консистенцию и свойства материала. Если данных недостаточно, честно сообщи об этом.
Не советуй разбавление, спирт, растворители или нагрев продукта.
"""


async def call_fast_gemini(prompt: str, raise_on_error: bool = False) -> str:
    # Параметр сохранён для совместимости, но фиктивный ответ недопустим.
    text, _ = await generate(prompt, max_tokens=800, fast=True, timeout=30)
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
Ответь на вопрос покупателя на Ozon.

{BRAND_MATRIX_RULES}
{sku_context}

РЕАЛЬНЫЙ ВОПРОС ПОКУПАТЕЛЯ:
«{question_text}»

ГЛАВНЫЕ ТРЕБОВАНИЯ:
1. Поздоровайся и ответь на вопрос только на основании подтверждённых данных выше.
2. При нехватке сведений попроси уточнить у продавца, не обещай сроки поставки.
3. Не давай непроверенные советы по использованию. 2–4 предложения.

"""
    reply = await call_fast_gemini(prompt, raise_on_error=True)
    if not reply.strip():
        raise RuntimeError("Gemini вернул пустой ответ; публикация отменена")
    if any(term in reply.lower() for term in ("спирт", "растворител", "подогре", "нагре")):
        raise RuntimeError("Ответ содержит неподтверждённый совет; публикация отменена")
    return reply


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
0. Не рекомендуй спирт, растворители, нагрев продукта или неподтвержденные процедуры.
1. Начни с вежливого приветствия: «Здравствуйте!», поблагодари за выбор бренда YaSoKo и оценку.
2. Персонализируй ответ: обязательно упомяни артикул ({sku or ''}) или конкретные детали из комментария покупателя (оттенок, консистенция, результат на фото).
3. Не обещай новые товары и сроки, не предлагай технологии применения без подтверждённой инструкции производителя.
4. Заверши теплым пожеланием: «Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова.».
5. Объем: 3–4 предложения.
"""
    for attempt in range(3):
        try:
            reply = await call_fast_gemini(prompt, raise_on_error=True)
            if any(term in reply.lower() for term in ("спирт", "растворител", "подогре", "нагре")):
                raise RuntimeError("Ответ содержит неподтвержденный совет; публикация отменена")
            if not reply.strip():
                raise RuntimeError('Gemini вернул пустой ответ')
            return reply
        except RuntimeError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
