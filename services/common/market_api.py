"""Direct marketplace transport with route failover. Read-only version negotiation."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any, Awaitable, Callable, Sequence, TypeVar
from urllib.parse import urlsplit

import aiohttp

from config import LOGS_DIR, settings
from services.common.routes import (
    DIRECT,
    request_routes,
    route_available,
    mark_route_failed,
    mark_route_ok,
)
from services.common.storage import DiskCache

T = TypeVar("T")


class CircuitBreakerOpenError(RuntimeError):
    """Temporary transport outage."""


class APIStatusError(RuntimeError):
    def __init__(self, status: int, detail: str, retry_after: int = 0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {detail}")


def retry_seconds(raw: str) -> int:
    if raw.isdecimal():
        return int(raw)
    try:
        until = parsedate_to_datetime(raw)
        return max(0, int((until - datetime.now(timezone.utc)).total_seconds()) + 1)
    except (ValueError, TypeError, OverflowError):
        return 0


async def read_versions(
    versions: Sequence[str], read: Callable[[str], Awaitable[T]]
) -> T:
    for i, version in enumerate(versions):
        try:
            return await read(version)
        except APIStatusError as exc:
            if i + 1 == len(versions) or exc.status not in {400, 404, 410, 425, 429}:
                raise
            if exc.status == 429:
                if exc.retry_after > 60:
                    raise  # do not hammer a service asking for a longer cooldown
                await asyncio.sleep(max(1, exc.retry_after))
    raise ValueError("Не указаны версии API")


class MarketplaceClient:
    """Base for Ozon/WB: retries each endpoint across every available route."""

    # Ошибки маршрута (антибот, edge-прокси, обрыв) — повод перейти к следующему
    # маршруту, а не считать, что площадка ответила «нет».
    ROUTE_STATUSES = frozenset({403, 407, 408, 425, 498, 502, 503, 504})

    def __init__(self, service: str, account: str, notify_callback=None):
        self._cache = DiskCache(
            LOGS_DIR / "api_cache" / service / sha256(account.encode()).hexdigest()[:16]
        )
        self._notify_callback = notify_callback
        self._degraded_until = 0.0
        self._failures = 0
        self._service = service
        self._inventory_lock = asyncio.Lock()

    @property
    def is_degraded(self) -> bool:
        return time.monotonic() < self._degraded_until

    def _read_disk_cache(self, key: str, max_age_sec: int) -> Any:
        return self._cache.get(key, max_age_sec)

    def _write_disk_cache(self, key: str, val: Any) -> None:
        self._cache.put(key, val)

    async def request_json(
        self, method: str, url: str, headers: dict, *, body=None, params=None
    ) -> Any:
        if self.is_degraded:
            raise CircuitBreakerOpenError(
                f"{self._service}: временная приостановка запросов"
            )
        endpoint = urlsplit(url).path
        try:
            routes = request_routes(self._service)
        except ValueError as exc:
            raise CircuitBreakerOpenError(str(exc)) from exc

        transport_errors: list[str] = []
        for route in routes:
            if not route_available(self._service, route):
                continue
            try:
                result = await self._request_with_rate_limit(
                    method, url, headers, body, params, route
                )
            except aiohttp.ClientResponseError as exc:  # pragma: no cover - internal
                mark_route_failed(self._service, route, f"HTTP {exc.status}")
                transport_errors.append(f"{route}: HTTP {exc.status}")
                continue
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                mark_route_failed(self._service, route, type(exc).__name__)
                transport_errors.append(f"{route}: {type(exc).__name__}")
                continue
            mark_route_ok(self._service, route)
            self._failures = 0
            return result

        if not transport_errors:
            raise CircuitBreakerOpenError(
                f"{self._service} {endpoint}: все маршруты в карантине"
            )
        self._failures += 1
        if self._failures >= 4:
            self._degraded_until = time.monotonic() + 60
            self._failures = 0
            if self._notify_callback:
                await self._notify_callback(
                    f"⚠️ {self._service}: серия сетевых ошибок, пауза 1 минута"
                )
        raise CircuitBreakerOpenError(
            f"{self._service} {endpoint}: все маршруты недоступны "
            f"({'; '.join(transport_errors)})"
        )

    async def _request_with_rate_limit(
        self, method: str, url: str, headers: dict, body: Any, params: Any, route: str
    ) -> Any:
        """Один маршрут + ожидание по HTTP 429.

        WB/Ozon присылают в 429 точное время ожидания (X-Ratelimit-Retry /
        Retry-After). Раньше его читали, но не ждали: отчёты WB падали с
        «HTTP 429», хотя finance-api разрешает лишь 1 запрос в минуту и
        достаточно подождать 60 с. Ждём ровно столько, сколько просит площадка
        (не дольше RATE_LIMIT_MAX_WAIT_SEC), и повторяем на том же маршруте.
        """
        retries = max(int(settings.RATE_LIMIT_RETRIES), 0)
        cap = max(int(settings.RATE_LIMIT_MAX_WAIT_SEC), 1)
        for attempt in range(retries + 1):
            try:
                return await self._request_once(method, url, headers, body, params, route)
            except APIStatusError as exc:
                if exc.status != 429 or attempt >= retries or exc.retry_after > cap:
                    raise
                wait = max(exc.retry_after, 1) + 1
                logging.getLogger(__name__).warning(
                    "%s HTTP 429 %s: ожидание %s с (попытка %s/%s)",
                    self._service, urlsplit(url).path, wait, attempt + 1, retries,
                )
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _request_once(
        self,
        method: str,
        url: str,
        headers: dict,
        body: Any,
        params: Any,
        route: str,
    ) -> Any:
        """One attempt over one route. Route-level problems surface as ClientError."""
        endpoint = urlsplit(url).path
        proxy = {} if route == DIRECT else {"proxy": route}
        async with aiohttp.ClientSession(
            trust_env=False, timeout=aiohttp.ClientTimeout(total=60, connect=12)
        ) as session:
            async with session.request(
                method,
                url,
                headers=headers,
                json=body,
                params=params,
                allow_redirects=False,
                **proxy,
            ) as resp:
                if resp.status in self.ROUTE_STATUSES:
                    # Anti-bot / edge proxy / outage: not an answer from the API.
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=endpoint,
                    )
                if 300 <= resp.status < 400:
                    raise APIStatusError(
                        resp.status,
                        f"{endpoint}: редирект {resp.headers.get('Location', '')}"[:500],
                    )
                if not 200 <= resp.status < 300:
                    detail = endpoint
                    try:
                        error = await resp.json(content_type=None)
                        if isinstance(error, dict):
                            detail += (
                                ": "
                                + str(
                                    error.get("errorText")
                                    or error.get("message")
                                    or error.get("detail")
                                    or error.get("code")
                                    or ""
                                )[:500]
                            )
                    except (ValueError, UnicodeError):
                        detail += ": ответ без JSON"
                    wait = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-Ratelimit-Retry")
                        or "0"
                    )
                    logging.getLogger(__name__).warning(
                        "%s HTTP %s %s", self._service, resp.status, endpoint
                    )
                    raise APIStatusError(resp.status, detail, retry_seconds(wait))
                if resp.status == 204:
                    return None
                try:
                    return await resp.json(content_type=None)
                except (UnicodeError, ValueError) as exc:
                    raise APIStatusError(
                        resp.status, f"{endpoint}: ответ не является JSON"
                    ) from exc
