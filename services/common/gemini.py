"""Shared asynchronous REST client for Gemini. Direct connection preferred; local proxy is fallback only."""

import asyncio
import re
from typing import Any, Optional

import aiohttp

from config import settings
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
    return bool(re.fullmatch(r"gemini-[\w.-]+", name)) and not any(
        tag in name.lower()
        for tag in (
            "tts",
            "audio",
            "imagen",
            "embedding",
            "vision",
            "robotics",
            "computer-use",
            "image",
        )
    )


async def _get_available_models(session: aiohttp.ClientSession, proxy: Optional[str]) -> list[str]:
    names = [_extract_model_name(settings.GEMINI_MODEL)]
    kwargs = {"proxy": proxy} if proxy else {}
    try:
        async with session.get(BASE_URL, allow_redirects=False, **kwargs) as resp:
            if resp.status in (401, 403):
                raise RuntimeError(f"Gemini HTTP {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                names.extend(
                    _extract_model_name(item)
                    for item in data.get("models", [])
                    if isinstance(item, dict)
                    and "generateContent" in item.get("supportedGenerationMethods", [])
                )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        pass  # fall through to static list
    return list(
        dict.fromkeys(
            name
            for name in [*names, *FALLBACK_MODELS]
            if _is_supported_text_model(name)
        )
    )


async def generate(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.2,
    fast: bool = False,
    timeout: int = 90,
) -> tuple[str, str]:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Не задан ключ Gemini")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    # Direct first, then any configured local proxies
    routes = proxy_routes("gemini", allow_direct=True)
    error = "Нет доступной текстовой модели"
    async with aiohttp.ClientSession(
        trust_env=False,
        timeout=aiohttp.ClientTimeout(total=timeout, connect=12),
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
    ) as session:
        for route in routes:
            proxy = route if route else None  # None = direct
            try:
                models = await _get_available_models(session, proxy)
                if fast:
                    models.sort(key=lambda name: (0 if "flash" in name.lower() or "lite" in name.lower() else 1, name))
                for name in models[:6]:
                    url = f"{BASE_URL}/{name}:generateContent"
                    kwargs = {"proxy": proxy} if proxy else {}
                    async with session.post(
                        url, json=payload, allow_redirects=False, **kwargs
                    ) as resp:
                        if resp.status != 200:
                            error = f"{name}: HTTP {resp.status}"
                            if resp.status in (401, 403):
                                raise RuntimeError(error)
                            continue
                        data = await resp.json()
                        for c in data.get("candidates") or []:
                            if c.get("finishReason") != "STOP":
                                continue
                            text = "".join(
                                part.get("text", "")
                                for part in c.get("content", {}).get("parts", [])
                                if not part.get("thought")
                            ).strip()
                            if text:
                                remember_route("gemini", route)
                                return text, name
                        error = f"{name}: пустой или незавершённый ответ"
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                ValueError,
            ) as exc:
                error = f"{type(exc).__name__}: {exc}"
                continue
    raise RuntimeError(f"Gemini недоступен (прямой + прокси): {error}")
