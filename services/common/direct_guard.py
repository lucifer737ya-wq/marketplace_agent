"""Проверка, что браузер/HTTP выходят в сеть с российского IP (без VPN-узла).

Почему это нужно. Флаги Chromium ``--no-proxy-server`` / ``--proxy-server=direct://`` и очистка
``HTTP(S)_PROXY`` отключают только ПРОКСИ. Если Karing/Hiddify/другой клиент работает в TUN-режиме
(виртуальная сетевая карта), он перехватывает ВЕСЬ трафик ОС, включая Chrome, и WB/Ozon показывают
страницу «отключите VPN». Из кода это не обойти: нужно правило «direct» в самом VPN-клиенте.
Модуль определяет такую ситуацию ДО открытия окна и даёт точную инструкцию вместо пустого браузера.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Домены, которые должны идти напрямую (правила direct в Karing / Hiddify / v2rayN / Clash).
DIRECT_DOMAIN_SUFFIXES = (
    "wildberries.ru", "wb.ru", "wbbasket.ru", "wbcontent.net", "wbstatic.net", "wbx-static.ru",
    "ozon.ru", "ozone.ru", "ozonusercontent.com", "ozon.travel", "seller.ozon.ru",
)
# Процессы, которые должны идти напрямую (режим «по приложению»).
DIRECT_PROCESS_NAMES = ("chrome.exe", "msedge.exe", "chromium.exe", "python.exe", "pythonw.exe")

_GEO_ENDPOINTS = (
    ("https://api.country.is/", ("country",)),
    ("https://ipwho.is/", ("country_code",)),
    ("https://ipinfo.io/json", ("country",)),
)


def direct_rules_hint() -> str:
    domains = ", ".join(DIRECT_DOMAIN_SUFFIXES[:7])
    procs = ", ".join(DIRECT_PROCESS_NAMES)
    return (
        "Трафик Chrome идёт через VPN (TUN-режим перехватывает всю систему, флаги браузера тут бессильны). "
        "В VPN-клиенте (Karing → Настройки → Маршрутизация/Правила) добавьте правила «Напрямую / Direct»: "
        f"процессы: {procs}; домены (суффиксы): {domains}…; а также «geoip:ru → direct» и «geosite:category-ru → direct». "
        "Telegram и Gemini при этом остаются через прокси 127.0.0.1:3066. После правки перезапустите VPN-клиент и повторите."
    )


def _clean_env() -> None:
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)


async def detect_exit_country(timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """Страна выходного IP по прямому соединению (без прокси). ``None`` — определить не удалось."""
    try:
        import aiohttp
    except ImportError:  # pragma: no cover
        return None
    for url, keys in _GEO_ENDPOINTS:
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session:
                async with session.get(url, headers={"Accept": "application/json"}) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            continue
        for key in keys:
            code = str((data or {}).get(key) or "").strip().upper()
            if code:
                return {"country": code, "ip": str((data or {}).get("ip") or ""), "source": url}
    return None


async def foreign_exit_message() -> Optional[str]:
    """Текст проблемы, если выход точно НЕ из РФ; ``None`` — всё в порядке или проверить нельзя."""
    info = await detect_exit_country()
    if not info:
        return None
    if info["country"] == "RU":
        return None
    logger.warning("[DirectGuard] выход в сеть: %s (%s)", info["country"], info.get("ip"))
    return f"Выходной IP {info.get('ip') or '?'} — страна {info['country']}, а не RU. " + direct_rules_hint()


VPN_WALL_MARKERS = (
    "отключите vpn", "выключите vpn", "отключите впн", "выключите впн",
    "используете vpn", "обнаружен vpn", "vpn или прокси", "turn off vpn", "disable your vpn",
)


def looks_like_vpn_wall(page_text: str) -> bool:
    low = (page_text or "").lower()
    return any(marker in low for marker in VPN_WALL_MARKERS)
