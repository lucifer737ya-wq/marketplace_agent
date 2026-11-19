"""Gemini client: direct Google endpoint first, local proxies only as fallback.

Keys travel in the ``x-goog-api-key`` header, never in a query string, so error
texts stay clean. Any route failure (transport or anti-bot) automatically moves
to the next route — this is what makes the module survive a dead proxy.
"""

import asyncio
import logging
import re
from typing import Any, Optional

import aiohttp

from config import settings
from services.common.routes import (
    DIRECT,
    mark_route_failed,
    mark_route_ok,
    request_routes,
)

logger = logging.getLogger(__name__)

# Актуальные текстовые модели Google (проверено по документации 11.2026).
# Старые gemini-2.5-* автоматически переадресуются Google с 01.12.2026,
# поэтому фолбэк-цепочка начинается с текущего поколения.
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]
FAST_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.8-flash",
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
from services.common.routes import DIRECT, proxy_routes, remember_route

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
    "gemini-flash-lite-latest",
    "gemini-1.5-flash",
]


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
    """Server-reported models; a failure here must not kill generation."""
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
    return [
        _extract_model_name(item)
        for item in data.get("models", [])
        if isinstance(item, dict)
        and "generateContent" in item.get("supportedGenerationMethods", [])
        and _is_supported_text_model(_extract_model_name(item))
    ]


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

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    total = timeout or settings.GEMINI_TIMEOUT
    headers = {
        "x-goog-api-key": settings.GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        routes = request_routes("gemini")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    errors: list[str] = []
    for route in routes:
        proxy = {} if route == DIRECT else {"proxy": route}
        route_label = "напрямую" if route == DIRECT else route
        route_broken = False
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=total, connect=15),
                headers=headers,
            ) as session:
                models = await _get_available_models(session, route)
                chain = list(
                    dict.fromkeys(models + _model_chain(fast))
                )
                if fast:
                    chain.sort(key=lambda n: ("lite" not in n, "flash" not in n))
                for name in chain[:6]:
                    url = f"{BASE_URL}/{name}:generateContent"
                    try:
                        async with session.post(url, json=payload, **proxy) as resp:
                            if resp.status != 200:
                                errors.append(f"{name}: HTTP {resp.status}")
                                if resp.status in (400, 401, 403, 404):
                                    continue  # model/permission problem, next model
                                route_broken = resp.status in (408, 425, 429, 498, 500, 502, 503, 504)
                                break  # route problem, next route
                            data = await resp.json()
                    except (
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        OSError,
                        ValueError,
                    ) as exc:
                        errors.append(f"{route_label}: {type(exc).__name__}")
                        route_broken = True
                        break
                    for candidate in data.get("candidates") or []:
                        text = _text_from_candidate(candidate)
                        if text:
                            mark_route_ok("gemini", route)
                            return text, name
                    errors.append(f"{name}: пустой или незавершённый ответ")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            mark_route_failed("gemini", route, type(exc).__name__)
            errors.append(f"{route_label}: {type(exc).__name__}")
            continue
        if route_broken:
            # Транспорт или шлюз маршрута: карантин, следующий маршрут.
            mark_route_failed("gemini", route, errors[-1] if errors else "route")
            continue
        # Route answered; a model-level failure here is not a route failure but
        # the next route may still have a different model catalogue.
        logger.warning("Gemini через %s не дал ответа: %s", route_label, errors[-3:])

    raise RuntimeError(
        "Gemini недоступен ни по одному маршруту: " + ("; ".join(errors[-4:]) or "нет ответа")
    )
