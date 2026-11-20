"""Gemini client: direct Google endpoint first, local proxies only as fallback.

Keys travel in the ``x-goog-api-key`` header, never in a query string, so error
texts stay clean. Any route failure (transport or anti-bot) automatically moves
to the next route — this is what makes the module survive a dead proxy.
"""

import asyncio
import logging
import re
import time
from typing import Any

import aiohttp

from config import settings
from services.common.routes import (
    DIRECT,
    mark_route_failed,
    mark_route_ok,
    request_routes,
    route_available,
)

logger = logging.getLogger(__name__)

# Статусы, при которых виновата модель/квота/перегрузка, а не маршрут: пробуем
# следующую модель на том же маршруте. Раньше 503 (перегрузка модели) объявлял
# маршрут мёртвым, и рабочий прокси уходил в карантин.
_MODEL_LEVEL_STATUSES = frozenset({400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504})
_CATALOG_TTL_SEC = 3600
_catalog_cache: dict[str, tuple[float, list[str]]] = {}


class GeminiUnavailable(RuntimeError):
    """Gemini недоступен по всем маршрутам/моделям. Повторять сразу бессмысленно."""

# Актуальные текстовые модели Google (сверено с документацией на 20.11.2026):
# * gemini-3.8-flash (02.09.2026) — текущая Flash-модель;
# * gemini-3.6-flash — без объявленной даты отключения в Gemini API;
# * gemini-3.5-flash / 3.5-flash-lite — действуют (3.5-flash маршрутизируется на 3.6);
# * gemini-3.1-flash-lite — действует до 07.05.2027 (эконом-модель).
# gemini-2.5-* объявлены устаревшими 02.11.2026 («только существующим
# пользователям», с 01.12.2026 перенаправляются Google) — в цепочке их больше нет:
# новому ключу они отвечают 404/403 и только тратят время.
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
FAST_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]
_NON_TEXT_TAGS = (
    "tts",
    "audio",
    "imagen",
    "embedding",
    "vision",
    "robotics",
    "computer-use",
    "image",
    "video",
    "live",
    "native-audio",
)


BASE_URL = (
    str(settings.GEMINI_DIRECT_URL or "https://generativelanguage.googleapis.com").rstrip("/")
    + "/v1beta/models"
)


def base_url() -> str:
    return str(settings.GEMINI_DIRECT_URL or "").rstrip("/") + "/v1beta/models"


def _extract_model_name(item: Any) -> str:
    name = item.get("name") if isinstance(item, dict) else item
    return str(name or "").strip().removeprefix("models/")


def _is_supported_text_model(name: str) -> bool:
    if not re.fullmatch(r"gemini-[\w.-]+", name or ""):
        return False
    lowered = name.lower()
    return not any(tag in lowered for tag in _NON_TEXT_TAGS)


def _model_chain(fast: bool) -> list[str]:
    preferred = settings.GEMINI_FAST_MODEL if fast else settings.GEMINI_MODEL
    extra = FAST_MODELS if fast else FALLBACK_MODELS
    names = [_extract_model_name(preferred), *extra, *FALLBACK_MODELS, *FAST_MODELS]
    return list(dict.fromkeys(n for n in names if _is_supported_text_model(n)))


async def _get_available_models(session, route: str = DIRECT) -> list[str]:
    """Server-reported models (кэш на час); a failure here must not kill generation."""
    cached = _catalog_cache.get(route)
    if cached and time.monotonic() - cached[0] < _CATALOG_TTL_SEC:
        return list(cached[1])
    try:
        async with (
            await session.get(
                BASE_URL, **({} if route == DIRECT else {"proxy": route})
            )
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
        return []
    models = [
        _extract_model_name(item)
        for item in data.get("models", [])
        if isinstance(item, dict)
        and "generateContent" in item.get("supportedGenerationMethods", [])
        and _is_supported_text_model(_extract_model_name(item))
    ]
    if models:
        _catalog_cache[route] = (time.monotonic(), models)
    return models


def _truncated(candidate: dict) -> bool:
    return candidate.get("finishReason") == "MAX_TOKENS"


def _text_from_candidate(candidate: dict) -> str:
    if candidate.get("finishReason") not in (None, "STOP"):
        # In particular, never return truncated MAX_TOKENS output as a report.
        return ""
    parts = candidate.get("content", {}).get("parts", []) or []
    return "".join(
        part.get("text", "") for part in parts if not part.get("thought")
    ).strip()


async def generate(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.2,
    fast: bool = False,
    timeout: int | None = None,
) -> tuple[str, str]:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Не задан ключ Gemini")
    total = timeout or settings.GEMINI_TIMEOUT
    # Жёсткий потолок на весь вызов: маршруты × модели × таймауты раньше могли
    # занимать минуты, и автоответы Ozon упирались в бюджет задания.
    overall = max(total * 2, 30)
    try:
        return await asyncio.wait_for(
            _generate(prompt, max_tokens, temperature, fast, total), timeout=overall
        )
    except asyncio.TimeoutError as exc:
        raise GeminiUnavailable(
            f"Gemini не ответил за {overall} с (маршруты/модели исчерпаны по времени)"
        ) from exc


async def _generate(
    prompt: str, max_tokens: int, temperature: float, fast: bool, total: int
) -> tuple[str, str]:
    headers = {
        "x-goog-api-key": settings.GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        raw_routes = request_routes("gemini")
    except ValueError as exc:
        raise GeminiUnavailable(str(exc)) from exc
    available = [r for r in raw_routes if route_available("gemini", r)]
    routes = available + [r for r in raw_routes if r not in available]

    errors: list[str] = []
    for route in routes:
        proxy = {} if route == DIRECT else {"proxy": route}
        route_label = "напрямую" if route == DIRECT else route
        route_broken = False
        model_5xx = 0
        model_tries = 0
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=total, connect=15),
                headers=headers,
            ) as session:
                models = await _get_available_models(session, route)
                preferred = _model_chain(fast)
                if models:
                    extras = [
                        n for n in models
                        if n not in preferred
                        and re.match(r"gemini-[3-9]", n)
                        and "pro" not in n
                    ][:2]
                    chain = [n for n in preferred if n in models] + extras
                else:
                    chain = preferred
                for name in chain:
                    url = f"{BASE_URL}/{name}:generateContent"
                    tokens = max_tokens
                    for attempt in range(2):
                        payload = {
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": temperature,
                                "maxOutputTokens": tokens,
                            },
                        }
                        try:
                            async with session.post(url, json=payload, **proxy) as resp:
                                if resp.status != 200:
                                    # Тело ошибки не выводим: там могут быть ключи и данные.
                                    api_status = ""
                                    try:
                                        error_data = await resp.json()
                                        status = (error_data.get("error") or {}).get("status")
                                        if status in {
                                            "INVALID_ARGUMENT", "FAILED_PRECONDITION",
                                            "PERMISSION_DENIED", "UNAUTHENTICATED",
                                            "NOT_FOUND", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
                                        }:
                                            api_status = f" ({status})"
                                    except (ValueError, TypeError, AttributeError, aiohttp.ClientError):
                                        pass
                                    model_tries += 1
                                    errors.append(f"{name}: HTTP {resp.status}{api_status}")
                                    if resp.status == 400 and api_status == " (FAILED_PRECONDITION)":
                                        # Геоблок маршрута (РФ-IP): в карантин, сразу на прокси.
                                        route_broken = True
                                    elif resp.status >= 500:
                                        model_5xx += 1
                                    elif resp.status not in _MODEL_LEVEL_STATUSES:
                                        route_broken = True
                                    data = None
                                else:
                                    data = await resp.json()
                        except (
                            aiohttp.ClientError,
                            asyncio.TimeoutError,
                            OSError,
                            ValueError,
                        ) as exc:
                            errors.append(f"{route_label}: {type(exc).__name__}")
                            route_broken = True
                            data = None
                        if data is None:
                            break  # к следующей модели (или маршруту при route_broken)
                        truncated = False
                        for candidate in data.get("candidates") or []:
                            text = _text_from_candidate(candidate)
                            if text:
                                mark_route_ok("gemini", route)
                                return text, name
                            truncated = truncated or _truncated(candidate)
                        if truncated and attempt == 0 and tokens < 8192:
                            # Модели 3.x тратят часть лимита на «размышления»: короткий
                            # лимит давал пустой ответ. Повторяем с удвоенным лимитом.
                            tokens = min(tokens * 2, 8192)
                            continue
                        errors.append(f"{name}: пустой или незавершённый ответ")
                        break
                    if route_broken:
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            mark_route_failed("gemini", route, type(exc).__name__)
            errors.append(f"{route_label}: {type(exc).__name__}")
            continue
        if not route_broken and model_tries and model_5xx >= model_tries:
            route_broken = True  # шлюз отвечает только 5xx по всем моделям
        if route_broken:
            mark_route_failed("gemini", route, errors[-1] if errors else "route")
            continue
        logger.warning("Gemini через %s не дал ответа: %s", route_label, errors[-3:])

    raise GeminiUnavailable(
        "Gemini недоступен ни по одному маршруту: " + ("; ".join(errors[-4:]) or "нет ответа")
    )
