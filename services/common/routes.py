"""Proxy routes for Telegram/Gemini. Direct (no-proxy) is allowed and preferred for Gemini."""

from hashlib import sha256
from ipaddress import ip_address
from urllib.parse import urlsplit
from typing import List, Optional

from config import LOGS_DIR, settings
from services.common.storage import DiskCache

_cache = DiskCache(LOGS_DIR / "routes")

# Sentinel for "no proxy / direct connection"
DIRECT = ""


def _fingerprint(url: str) -> str:
    return sha256((url or "direct").encode()).hexdigest()


def _is_valid_local_proxy(url: str) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    try:
        local = (
            parsed.hostname == "localhost"
            or ip_address(parsed.hostname or "").is_loopback
        )
    except (ValueError, TypeError):
        local = False
    return bool(local and parsed.scheme == "http" and parsed.port)


def proxy_routes(service: str, *, allow_direct: bool = False) -> List[Optional[str]]:
    """
    Returns ordered list of routes to try.
    - For Telegram: only configured local HTTP proxies (required).
    - For Gemini (allow_direct=True): direct connection first, then local proxies as fallback.
    """
    routes: List[Optional[str]] = []

    if allow_direct:
        # Direct is preferred: user reports ~300 ms and stable without local proxy.
        routes.append(DIRECT)

    for url in (settings.LOCAL_PROXY_URL, settings.LOCAL_PROXY_FALLBACK_URL):
        if not url or url in routes:
            continue
        if not _is_valid_local_proxy(url):
            if not allow_direct:
                raise ValueError("Необходим локальный HTTP-прокси с указанием порта")
            continue
        routes.append(url)

    if not routes:
        if allow_direct:
            routes.append(DIRECT)
        else:
            raise ValueError("Не задан локальный прокси; прямой маршрут отключён")

    last = _cache.get(service, 7 * 86400)
    # Prefer the last successful route, but keep direct first for Gemini if it worked before or is new.
    return sorted(
        routes,
        key=lambda url: (
            0 if (allow_direct and url == DIRECT and last in (None, _fingerprint(DIRECT))) else
            1 if _fingerprint(url or "") == last else 2
        ),
    )


def remember_route(service: str, url: Optional[str]) -> None:
    fingerprint = _fingerprint(url or DIRECT)
    if _cache.get(service, 7 * 86400) != fingerprint:
        _cache.put(service, fingerprint)
