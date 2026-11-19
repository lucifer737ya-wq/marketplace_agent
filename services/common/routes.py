"""Route failover for every outbound API call.

Every request walks a list of candidate routes in priority order:

1. ``direct`` — plain HTTPS, no proxy (fastest, no middle man);
2. each configured local proxy (``http://127.0.0.1:3066``, ...).

A route that fails with a transport error (connection reset, DNS, TLS, timeout)
or with an anti-bot/edge status (403/407/498/502/503/504) is put on cooldown for
``ROUTE_COOLDOWN_SECONDS`` and the *next* route is tried. Successful routes are
remembered on disk so the next process starts with the working one first.

Only loopback HTTP proxies are ever accepted: credentials must never end up in a
cache file, and a proxy list must not silently become an open relay.
"""

import asyncio
import ipaddress
import logging
import time
from hashlib import sha256
from typing import Any, Awaitable, Callable, Iterable, Sequence
from urllib.parse import urlsplit

import aiohttp

from config import LOGS_DIR, settings
from services.common.storage import DiskCache

logger = logging.getLogger(__name__)

DIRECT = "direct"
_cache = DiskCache(LOGS_DIR / "routes")
# service -> (route, monotonic deadline)
_cooldown: dict[str, tuple[str, float]] = {}
# monotonic timestamps of consecutive failures per route fingerprint
_failures: dict[str, int] = {}

class RouteError(RuntimeError):
    """Every candidate route failed. Carries a compact human readable reason."""


def _fingerprint(route: str) -> str:
    return sha256(route.encode()).hexdigest()


def _is_loopback_proxy(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.port:
        return False
    host = (parsed.hostname or "").strip()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _configured_proxies() -> list[str]:
    raw: list[str] = [
        settings.LOCAL_PROXY_URL,
        settings.LOCAL_PROXY_FALLBACK_URL,
        *str(settings.LOCAL_PROXY_LIST or "").split(","),
    ]
    proxies: list[str] = []
    for url in (item.strip() for item in raw):
        if not url or url in proxies:
            continue
        if not _is_loopback_proxy(url):
            logger.warning("Прокси отклонён (нужен локальный http://host:port)")
            continue
        proxies.append(url.rstrip("/"))
    return proxies


MARKETPLACE_SERVICES = frozenset({"ozon", "wb"})


def _service_key(service: str) -> str:
    return str(service or "").strip().lower()


def proxy_routes(service: str) -> list[str]:
    """Proxy-only routes (kept for Telegram, which never has a direct route)."""
    routes = _configured_proxies()
    if not routes:
        raise ValueError("Не задан локальный прокси; прямой маршрут отключён")
    return _prefer_last_working(service, routes)


def request_routes(service: str) -> list[str]:
    """All routes for a service, best-known first.

    Политика задаётся в ``.env``:

    * ``MARKETPLACES_DIRECT_ONLY=true`` — Ozon/WB ходят только напрямую,
      чтобы площадки видели российский IP продавца, а не выходной узел VPN;
    * ``TELEGRAM_PROXY_ONLY=true`` — Telegram только через локальный прокси
      (в РФ прямой доступ закрыт, и ждать 45-секундный таймаут бессмысленно);
    * остальные сервисы (Gemini) — прямой адрес первым, локальные прокси
      резервом.
    """
    key = _service_key(service)
    proxies = _configured_proxies()
    if key in MARKETPLACE_SERVICES and settings.MARKETPLACES_DIRECT_ONLY:
        return [DIRECT]
    if key == "telegram" and settings.TELEGRAM_PROXY_ONLY:
        if not proxies:
            raise ValueError(
                "TELEGRAM_PROXY_ONLY=true, но LOCAL_PROXY_URL не задан: "
                "укажите локальный HTTP-прокси"
            )
        return _prefer_last_working(service, proxies)

    routes: list[str] = [DIRECT] if settings.ALLOW_DIRECT_CONNECTIONS else []
    routes.extend(proxies)
    if not routes:
        raise ValueError(
            "Нет ни одного маршрута: включите ALLOW_DIRECT_CONNECTIONS "
            "или задайте LOCAL_PROXY_URL"
        )
    # A remembered working route jumps to the front, everything else keeps order.
    return _prefer_last_working(service, routes)


def proxy_for_route(route: str) -> str | None:
    """Translate a route token into an aiohttp/aiogram ``proxy`` argument.

    ``DIRECT`` means "no proxy" and must be passed as ``None``; passing an empty
    string to aiohttp raises instead of connecting directly.
    """
    return None if route == DIRECT else route


def telegram_routes(service: str = "telegram") -> list[str]:
    """Routes for Telegram: direct first (if allowed), then local proxies.

    A dead local proxy used to stop the bot from starting at all. The direct
    route is tried first because it needs no extra process, and every configured
    loopback proxy remains a fallback for networks where Telegram is blocked.
    """
    return request_routes(service)


def _preference_key(service: str) -> str:
    return f"route:{service}"


def _prefer_last_working(service: str, routes: list[str]) -> list[str]:
    last = _cache.get(_preference_key(service), 7 * 86400)
    if last not in {_fingerprint(url) for url in routes}:
        return list(routes)  # запомненный маршрут больше не настроен
    return sorted(routes, key=lambda url: _fingerprint(url) != last)


def remember_route(service: str, route: str) -> None:
    fingerprint = _fingerprint(route)
    if _cache.get(_preference_key(service), 7 * 86400) != fingerprint:
        _cache.put(_preference_key(service), fingerprint)


def mark_route_failed(service: str, route: str, reason: str = "") -> None:
    cooldown = max(int(settings.ROUTE_COOLDOWN_SECONDS), 0)
    _failures[_fingerprint(route)] = _failures.get(_fingerprint(route), 0) + 1
    if cooldown:
        _cooldown[service] = (route, time.monotonic() + cooldown)
        logger.warning(
            "Маршрут %s для %s в карантине %s с: %s",
            route,
            service,
            cooldown,
            reason or "сбой",
        )


def mark_route_ok(service: str, route: str) -> None:
    _failures[_fingerprint(route)] = 0
    current = _cooldown.get(service)
    if current and current[0] == route:
        _cooldown.pop(service, None)
    remember_route(service, route)


def route_available(service: str, route: str) -> bool:
    current = _cooldown.get(service)
    if not current or current[0] != route:
        return True
    return time.monotonic() >= current[1]


def _ordered_available(service: str) -> list[str]:
    routes = request_routes(service)
    available = [r for r in routes if route_available(service, r)]
    # If everything is cooling down, still try (better a slow attempt than a
    # guaranteed failure of the whole module).
    return available or routes


TRANSPORT_HINTS = (
    "ClientConnectorError",
    "ServerDisconnectedError",
    "ClientOSError",
    "ClientConnectionError",
    "ClientPayloadError",
    "ClientResponseError",
    "ClientSSLError",
    "ServerTimeoutError",
    "ConnectionResetError",
    "TimeoutError",
)

# Statuses that mean "this route cannot reach the API", not "the API said no".
ROUTE_LEVEL_STATUSES = frozenset({403, 407, 408, 425, 498, 502, 503, 504})


def is_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError, OSError)):
        return True
    name = type(exc).__name__
    return any(hint in name for hint in TRANSPORT_HINTS)


def _proxy_kwargs(route: str) -> dict[str, Any]:
    return {} if route == DIRECT else {"proxy": route, "allow_redirects": False}


async def request_with_failover(
    service: str,
    call: Callable[[aiohttp.ClientSession, str], Awaitable[Any]],
    *,
    timeout: aiohttp.ClientTimeout | None = None,
    headers: dict[str, str] | None = None,
    connector_limit: int = 8,
    route_statuses: Iterable[int] = ROUTE_LEVEL_STATUSES,
) -> Any:
    """Run ``call(session, route)`` until one route succeeds.

    ``call`` raises :class:`aiohttp.ClientResponseError` for a non-2xx status it
    does not want to handle itself; statuses in ``route_statuses`` are treated as
    route failures and the next route is tried, everything else propagates.
    """
    reasons: list[str] = []
    blocked = frozenset(route_statuses)
    for route in _ordered_available(service):
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                connector=aiohttp.TCPConnector(
                    limit=connector_limit, enable_cleanup_closed=True
                ),
                timeout=timeout or aiohttp.ClientTimeout(total=60, connect=12),
                headers=headers,
            ) as session:
                result = await call(session, route)
            mark_route_ok(service, route)
            return result
        except aiohttp.ClientResponseError as exc:
            if exc.status in blocked:
                mark_route_failed(service, route, f"HTTP {exc.status}")
                reasons.append(f"{route}: HTTP {exc.status}")
                continue
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            mark_route_failed(service, route, type(exc).__name__)
            reasons.append(f"{route}: {type(exc).__name__}")
            continue
    raise RouteError(
        f"{service}: все маршруты недоступны ({'; '.join(reasons) or 'нет маршрутов'})"
    )


def reset_routes() -> None:
    """Test helper: forget cooldowns (not the on-disk preference)."""
    _cooldown.clear()
    _failures.clear()


def route_diagnostics(service: str) -> list[dict[str, Any]]:
    """Small status table for the API audit message."""
    rows: list[dict[str, Any]] = []
    try:
        routes = request_routes(service)
    except ValueError as exc:
        return [{"route": "—", "state": str(exc)}]
    for route in routes:
        state = "ок" if route_available(service, route) else "карантин"
        rows.append({"route": route, "state": state})
    return rows


def sequence(routes: Sequence[str]) -> list[str]:  # pragma: no cover - small helper
    return list(dict.fromkeys(routes))
