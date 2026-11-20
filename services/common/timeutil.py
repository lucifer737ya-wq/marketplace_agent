"""Единый источник «сегодня» по московскому времени.

``datetime.utcnow().date()`` отставал от Москвы на 3 часа: с 00:00 до 03:00 MSK
отчёты «за 7 дней» заканчивались вчерашним днём, а запросы к WB/Ozon за «сегодня»
получали пустой период. К тому же ``utcnow`` объявлен устаревшим в Python 3.12.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import settings


def now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.TIMEZONE))
    except Exception:  # noqa: BLE001 — неверный TIMEZONE не должен ронять отчёты
        return datetime.now(ZoneInfo("Europe/Moscow"))


def today_local() -> date:
    return now_local().date()
