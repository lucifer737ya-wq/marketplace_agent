import json
from typing import List, Optional

import aiohttp

from config import settings
from products_catalog import get_catalog_product
from services.common.ai_advisory_board import (
    PROXY_CANDIDATES,
    _extract_model_name,
    _get_available_models,
    _is_supported_text_model,
)

BRAND_MATRIX_RULES = """
БАЗА ЗНАНИЙ И КЛАССИФИКАЦИЯ СЕРИЙ БРЕНДА YASOKO (банки 15 г):

1. КЛАССИФИКАЦИЯ СЕРИЙ ПО АРТИКУЛАМ:
   - Серия с 1001 по 1036: классические моделирующие гели БЕЗ ДОБАВОК и без шиммера (плотный молочный #01, камуфлирующие нюдовые, бежевые, розовые оттенки, кристально-прозрачный).
   - Серия с 1101 по 1307: моделирующие гели С ДОБАВЛЕНИЕМ ДЕЛИКАТНОГО ШИММЕРА (сияющие светоотражающие микрочастицы).
   - Серия с 1401 по 1408: гели БЕЗ шиммера, но ярких НЕОНОВЫХ оттенков (сочная палитра).
   - Серия с 1501 по 1512: моделирующие ГЕЛИ-ЖЕЛЕ С ШИММЕРОМ И ГЛИТТЕРОМ (густая плотная текстура желе для выкладки бортиков, углов и длины без растекания).

2. ПРАВИЛА ОТВЕТОВ ПО ТЕМАТИКАМ:
   А. НАЛИЧИЕ И ПОСТУПЛЕНИЕ («когда появится», «будет ли еще»):
      - Отвечать ТОЛЬКО о поступлении партии: товар скоро поступит в продажу, партия готовится к поставке на склад Ozon.
      - Предложить подписаться на магазин (нажав «сердечко» в карточке бренда), чтобы сразу получить уведомление.
      - ЗАПРЕЩЕНО писать про вязкость, усадку, апекс, лампы и базы!

   Б. ЦВЕТА И ОТТЕНКИ («будут ли другие цвета», поиск цвета):
      - Если спрашивают про гель-желе: палитра гелей-желе 1501–1512 активно расширяется, новые оттенки готовятся к выпуску.
      - Если ищут цвет без блесток: предложить классическую серию 1001–1036 (например, молочный #01 для цвета сгущенки).
      - Если ищут отсутствующий цвет (например, желтый): предложить максимально близкий смежный цвет (оранжевый или персиковый) и сказать, что палитра пополняется — подпишитесь на магазин.

   В. ЖЕСТКОСТЬ И ПЛАСТИЧНОСТЬ:
      - В тонком слое гель пластичный (амортизирует у кутикулы, нет отслоек), в толстом слое — твердый каркас (держит углы квадрата без усадки).

   Г. КОНСИСТЕНЦИЯ:
      - Для 1001–1408: средне-вязкая масса, самовыравнивается за 3–5 секунд, не затекая на валики.
      - Для гелей-желе 1501–1512: плотное неподвижное желе, держит высоту и бортики, не течет.

   Д. БАЗА И ТОП:
      - Рекомендовать дождаться фирменную базу и топ YaSoKo, которые сейчас в разработке и скоро поступят в продажу.

   Е. СОПУТСТВУЮЩИЕ ТОВАРЫ (праймер, наклейки):
      - Праймер в данный момент не разрабатывается, наклейки на типсы в набор не входят.
"""

_DEFAULT_QUESTION_FALLBACK = (
    "Здравствуйте! Товар скоро поступит в продажу. Рекомендуем подписаться на наш магазин, "
    "нажав сердечко в карточке бренда. Желаем приятных покупок и красивого маникюра! "
    "С уважением, команда YaSoKo."
)


async def call_fast_gemini(prompt: str, raise_on_error: bool = False) -> str:
    """Вызывает легковесную рабочую модель через динамический перебор моделей Google AI Studio."""
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        if raise_on_error:
            raise RuntimeError("GEMINI_API_KEY не задан в .env")
        return _DEFAULT_QUESTION_FALLBACK

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=20)
    last_error = "Нет ответа от доступных моделей Gemini"

    for route_label, proxy_url, trust_env in PROXY_CANDIDATES:
        connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                trust_env=trust_env,
                timeout=timeout,
            ) as session:
                models = await _get_available_models(session, api_key, proxy_url)
                cleaned_models = [
                    _extract_model_name(m)
                    for m in models
                    if _is_supported_text_model(_extract_model_name(m))
                ]
                fast_candidates: List[str] = list(
                    dict.fromkeys(
                        [m for m in cleaned_models if "flash" in m.lower() or "lite" in m.lower()]
                        + cleaned_models
                    )
                )

                for model_name in fast_candidates:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            body_text = await resp.text(encoding="utf-8", errors="replace")
                            if resp.status == 200:
                                data = json.loads(body_text)
                                candidates = data.get("candidates") or []
                                if candidates:
                                    parts = (candidates[0].get("content") or {}).get("parts") or []
                                    txt = "".join(
                                        str(p.get("text") or "")
                                        for p in parts
                                        if isinstance(p, dict) and not p.get("thought", False)
                                    ).strip()
                                    if txt:
                                        return txt
                            else:
                                last_error = f"[{route_label} | {model_name}] HTTP {resp.status}: {body_text[:120]}"
                                if resp.status in (401, 403):
                                    break
                    except Exception as exc:
                        last_error = f"[{route_label} | {model_name}] {type(exc).__name__}: {exc}"
                        continue
        except Exception as exc:
            last_error = f"[{route_label}] {type(exc).__name__}: {exc}"
            continue
        finally:
            if not connector.closed:
                await connector.close()

    if raise_on_error:
        raise RuntimeError(last_error)

    return _DEFAULT_QUESTION_FALLBACK


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
1. ОБЯЗАТЕЛЬНО начни с вежливого приветствия: «Здравствуйте!» или «Добрый день!».
2. Отвечай СТРОГО на заданный вопрос покупателя:
   - Если вопрос про наличие/поступление — ответь ТОЛЬКО про скорое поступление и рекомендацию нажать «сердечко» в карточке бренда для получения уведомления. Ни слова про вязкость, лампы и базы.
   - Если вопрос про цвета/оттенки/блестки — ответь по классификации серий (1001–1036 без добавок, 1101–1307 шиммер, 1401–1408 неон, 1501–1512 желе). Если ищут отсутствующий цвет, предложи близкий оттенок и подписку.
   - Если вопрос про густоту/жесткость — ответь по свойствам материала.
3. ОБЯЗАТЕЛЬНО заверши ответ вежливым пожеланием: «Желаем приятных покупок и красивого маникюра! С уважением, команда YaSoKo.».
4. Объем: 2–4 предложения. Тон экспертный, заботливый, без лишней воды.
"""
    return await call_fast_gemini(prompt)


async def generate_review_reply(review_text: str, rating: int, sku: Optional[str] = None) -> str:
    sku_context = _build_sku_context(sku, "Товар из отзыва")

    prompt = f"""
Ты — специалист контроля качества бренда YaSoKo.
Напиши официальный ответ на отзыв покупателя на Ozon с оценкой {rating} из 5 звезд.

{BRAND_MATRIX_RULES}
{sku_context}

ОТЗЫВ ПОКУПАТЕЛЯ:
«{review_text}»

ТРЕБОВАНИЯ:
1. Начни с вежливого приветствия: «Здравствуйте!», поблагодари за выбор бренда YaSoKo и оценку.
2. Персонализируй ответ: обязательно упомяни артикул ({sku or ''}) или конкретные детали из комментария покупателя (оттенок, консистенция, результат на фото).
3. При 4–5 звездах: анонсируй скорый выход фирменной базы и топа YaSoKo для безупречной стойкости. При 1–3 звездах: вежливо подскажи технологию (подложка из базы, мощность лампы от 48W, подготовка ногтя).
4. Заверши теплым пожеланием: «Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова.».
5. Объем: 3–4 предложения.
"""
    try:
        return await call_fast_gemini(prompt, raise_on_error=True)
    except Exception:
        art_note = f" (арт. {str(sku).strip()})" if sku and str(sku).strip() else ""
        if rating <= 3:
            return (
                f"Здравствуйте! Благодарим за обратную связь и выбор бренда YaSoKo{art_note}. "
                "Для максимальной стойкости покрытия рекомендуем наносить тонкую подложку из эластичной базы "
                "на подготовленный ноготь и полимеризовать в лампе мощностью от 48W. "
                "Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова."
            )
        return (
            f"Здравствуйте! Благодарим за высокую оценку и выбор бренда YaSoKo{art_note}! "
            "Мы рады, что материал порадовал вас в работе, а для безупречной стойкости скоро порадуем вас "
            "фирменной базой и топом YaSoKo. "
            "Желаем красивого маникюра и отличного настроения! Будем рады видеть вас снова."
        )
