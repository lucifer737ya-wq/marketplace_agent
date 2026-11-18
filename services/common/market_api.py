"""Direct marketplace transport. Version negotiation is for read operations only."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any, Awaitable, Callable, Sequence, TypeVar
from urllib.parse import urlsplit

import aiohttp

from config import LOGS_DIR
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
            if i + 1 == len(versions) or exc.status not in {400, 404, 410, 429}:
                raise
            if exc.status == 429:
                if exc.retry_after > 60:
                    raise  # do not hammer a service asking for a longer cooldown
                await asyncio.sleep(max(1, exc.retry_after))
    raise ValueError("Не указаны версии API")


class MarketplaceClient:
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
                ) as resp:
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
                    self._failures = 0
                    if resp.status == 204:
                        return None
                    try:
                        return await resp.json(content_type=None)
                    except (UnicodeError, ValueError) as exc:
                        raise RuntimeError(
                            f"{endpoint}: ответ не является JSON"
                        ) from exc
        except APIStatusError as exc:
            if exc.status < 500:
                raise
            error = exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            error = exc
        self._failures += 1
        if self._failures >= 4:
            self._degraded_until = time.monotonic() + 180
            self._failures = 0
            if self._notify_callback:
                await self._notify_callback(
                    f"⚠️ {self._service}: серия ошибок API, пауза 3 минуты"
                )
        raise CircuitBreakerOpenError(
            f"{self._service} {endpoint}: {type(error).__name__}"
        ) from error
