"""Set up before importing application modules. Never open the deployment state."""

import atexit
import os
import tempfile
from pathlib import Path

_temp = tempfile.TemporaryDirectory()
atexit.register(_temp.cleanup)
STATE = Path(_temp.name) / "state"
STATE.mkdir()
os.environ.update(
    MARKETPLACE_ENV_FILE="",
    MARKETPLACE_LOG_DIR=str(STATE),
    MARKETPLACE_BACKUP_DIR=str(Path(_temp.name) / "snapshots"),
    HEARTBEAT_FILE=str(STATE / "heartbeat.json"),
    RESTART_SIGNAL_FILE=str(STATE / "restart.signal"),
    WORKER_PID_FILE=str(STATE / "worker.pid"),
    SUPERVISOR_PID_FILE=str(STATE / "supervisor.pid"),
    MARKETPLACE_SUPERVISED="0",
    OZON_PERF_CLIENT_ID="perf-fixture",
    OZON_PERF_SECRET="performance-secret-fixture",
    DB_URL="sqlite+aiosqlite:///:memory:",
    TG_ADMIN_ID="123",
    TG_BOT_TOKEN="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    OZON_CLIENT_ID="client",
    OZON_API_KEY="seller-test-key",
    WB_API_TOKEN="wb-test-key",
    GEMINI_API_KEY="test-gemini-key",
    LOCAL_PROXY_URL="http://127.0.0.1:3066",
    LOCAL_PROXY_FALLBACK_URL="",
    OZON_REPLY_DRY_RUN="false",
    # Старые тесты проверяют перебор «прямой → прокси»; продовые умолчания
    # (Gemini/Telegram через прокси, площадки напрямую) проверяет tests/test_audit_20261120.py.
    GEMINI_PROXY_ONLY="false",
    TELEGRAM_PROXY_ONLY="false",
    MARKETPLACES_DIRECT_ONLY="false",
    GEMINI_MODEL="gemini-3.1-flash-lite",
    GEMINI_FAST_MODEL="gemini-3.1-flash-lite",
    RATE_LIMIT_RETRIES="0",
    GEMINI_RETRY_ROUNDS="1",
    GEMINI_NET_RETRY_DELAY="0",
    GEMINI_DIRECT_FALLBACK="false",
    # Старые тесты считают себестоимость как закупка+упаковка из БД; новые правила проверяет tests/test_audit_20261121.py.
    PACKAGING_COST_PER_UNIT="0",
    LABOR_COST_PER_UNIT="0",
    COMPETITOR_OZON_DISCOUNT_RATE="0",
)


def require_isolation() -> None:
    if (
        os.environ.get("MARKETPLACE_ENV_FILE") != ""
        or os.environ.get("DB_URL") != "sqlite+aiosqlite:///:memory:"
        or Path(os.environ["MARKETPLACE_LOG_DIR"]) != STATE
    ):
        raise RuntimeError("Production configuration is not allowed in offline tests")
