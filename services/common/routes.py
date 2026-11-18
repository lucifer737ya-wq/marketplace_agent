"""Only configured loopback HTTP proxies. Cache fingerprints, never proxy credentials."""

from hashlib import sha256
from ipaddress import ip_address
from urllib.parse import urlsplit

from config import LOGS_DIR, settings
from services.common.storage import DiskCache

_cache = DiskCache(LOGS_DIR / "routes")


def _fingerprint(url: str) -> str:
    return sha256(url.encode()).hexdigest()


def proxy_routes(service: str) -> list[str]:
    routes = []
    for url in (settings.LOCAL_PROXY_URL, settings.LOCAL_PROXY_FALLBACK_URL):
        if not url or url in routes:
            continue
        parsed = urlsplit(url)
        try:
            local = (
                parsed.hostname == "localhost"
                or ip_address(parsed.hostname).is_loopback
            )
        except (ValueError, TypeError):
            local = False
        if not local or parsed.scheme != "http" or not parsed.port:
            raise ValueError("Необходим локальный HTTP-прокси с указанием порта")
        routes.append(url)
    if not routes:
        raise ValueError("Не задан локальный прокси; прямой маршрут отключён")
    last = _cache.get(service, 7 * 86400)
    return sorted(routes, key=lambda url: _fingerprint(url) != last)


def remember_route(service: str, url: str) -> None:
    fingerprint = _fingerprint(url)
    if _cache.get(service, 7 * 86400) != fingerprint:
        _cache.put(service, fingerprint)
