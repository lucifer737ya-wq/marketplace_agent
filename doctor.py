"""Офлайн-диагностика установки marketplace_agent.

Проверяет всё, что можно проверить без обращения к маркетплейсам: версию
Python, зависимости, .env, права на каталоги, целостность SQLite, синтаксис
исходников, маршруты и наличие Chromium для Playwright.

Запуск:
    venv\\Scripts\\python.exe doctor.py
    venv\\Scripts\\python.exe doctor.py --online     (плюс Telegram и Gemini)

Коды возврата: 0 — критических проблем нет, 1 — есть проблемы.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tokenize
import sys
from pathlib import Path
from typing import List, Tuple

BASE_DIR = Path(__file__).resolve().parent

for _stream in (sys.stdout, sys.stderr):
    try:  # cmd.exe по умолчанию не умеет UTF-8
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"

_results: List[Tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "", critical: bool = True) -> None:
    _results.append((status, name, detail))
    line = f"{status} {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)
    if status == FAIL and critical:
        _results[-1] = (FAIL, name, detail)


def check_python() -> None:
    version = sys.version_info
    report(
        OK if version >= (3, 11) else FAIL,
        "Python",
        f"{version.major}.{version.minor}.{version.micro} (нужен 3.11+)",
    )
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        report(
            WARN,
            "Виртуальное окружение",
            "запущено системным Python; используйте venv\\Scripts\\python.exe",
            critical=False,
        )
    else:
        report(OK, "Виртуальное окружение", sys.prefix)


def check_dependencies() -> None:
    from importlib import metadata

    required = {
        "aiogram": "3.13.1",
        "aiohttp": "3.10.5",
        "SQLAlchemy": "2.0.35",
        "aiosqlite": "0.20.0",
        "APScheduler": "3.10.4",
        "pydantic": "2.9.2",
        "pydantic-settings": "2.5.2",
        "aiohttp-socks": "0.9.1",
        "playwright": "1.48.0",
        "pyflakes": "3.2.0",
    }
    for package, expected in required.items():
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            report(FAIL, f"Зависимость {package}", "не установлена")
            continue
        if installed.split("+")[0] != expected:
            report(
                WARN,
                f"Зависимость {package}",
                f"установлена {installed}, в requirements.txt {expected}",
                critical=False,
            )
        else:
            report(OK, f"Зависимость {package}", installed)


def check_env_file() -> bool:
    env_path = Path(os.environ.get("MARKETPLACE_ENV_FILE") or (BASE_DIR / ".env"))
    if not env_path.exists():
        report(
            FAIL,
            "Файл .env",
            f"не найден: {env_path}. Скопируйте .env.example в .env и заполните ключи",
        )
        return False
    report(OK, "Файл .env", str(env_path))

    try:
        from config import settings
    except Exception as exc:  # noqa: BLE001 — сообщение уже человекочитаемое
        report(FAIL, "Загрузка настроек", str(exc))
        return False
    report(OK, "Загрузка настроек", "все обязательные ключи присутствуют")

    token = str(settings.TG_BOT_TOKEN or "")
    if ":" in token and token.split(":", 1)[0].isdigit() and len(token) > 30:
        report(OK, "Формат TG_BOT_TOKEN")
    else:
        report(WARN, "Формат TG_BOT_TOKEN", "похоже на шаблон, проверьте токен", critical=False)

    if int(settings.TG_ADMIN_ID) > 0:
        report(OK, "TG_ADMIN_ID", str(settings.TG_ADMIN_ID))
    else:
        report(FAIL, "TG_ADMIN_ID", "должен быть положительным числом")

    configured = [
        ("OZON_CLIENT_ID", settings.OZON_CLIENT_ID),
        ("OZON_API_KEY", settings.OZON_API_KEY),
        ("WB_API_TOKEN", settings.WB_API_TOKEN),
        ("GEMINI_API_KEY", settings.GEMINI_API_KEY),
    ]
    empty = [name for name, value in configured if not str(value or "").strip()]
    if empty:
        report(FAIL, "Ключи маркетплейсов и ИИ", "пусто: " + ", ".join(empty))
        return False
    report(OK, "Ключи маркетплейсов и ИИ", "заполнены")
    return True


def check_directories() -> None:
    from config import BACKUPS_DIR, LOGS_DIR, DB_DIR, prepare_runtime_dirs

    try:
        prepare_runtime_dirs()
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "Каталоги logs/backups/database", f"не создаются: {exc}")
        return
    for name, directory in (("logs", LOGS_DIR), ("backups", BACKUPS_DIR), ("database", DB_DIR)):
        probe = directory / ".doctor_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            report(FAIL, f"Права на {name}", f"нет записи: {exc}")
        else:
            report(OK, f"Права на {name}", str(directory))


def check_database() -> None:
    from config import settings

    if not settings.DB_URL.startswith("sqlite"):
        report(OK, "База данных", settings.DB_URL)
        return
    db_path = Path(settings.DB_URL.split("///", 1)[-1])
    if not db_path.exists():
        report(OK, "База данных", f"{db_path} будет создана при первом запуске")
        return
    try:
        with sqlite3.connect(str(db_path), timeout=10) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except sqlite3.Error as exc:
        report(FAIL, "База данных", f"{db_path}: {exc}")
        return
    if not integrity or integrity[0] != "ok":
        report(FAIL, "База данных", f"integrity_check: {integrity}")
        return
    expected = {"products", "pending_actions", "orders"}
    report(
        OK if expected <= tables else WARN,
        "База данных",
        f"{db_path} (таблиц: {len(tables)})",
        critical=False,
    )


def check_sources() -> None:
    errors: List[str] = []
    checked = 0
    skip_dirs = {".git", "venv", ".venv", "__pycache__", "logs", "backups", "node_modules"}
    for path in BASE_DIR.rglob("*.py"):
        if any(part in skip_dirs for part in path.relative_to(BASE_DIR).parts):
            continue
        checked += 1
        try:
            with tokenize.open(path) as stream:
                compile(stream.read(), str(path), "exec")
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            errors.append(f"{path.relative_to(BASE_DIR)}: {exc}")
    if errors:
        report(FAIL, "Синтаксис исходников", "; ".join(errors[:5]))
    else:
        report(OK, "Синтаксис исходников", f"файлов: {checked}")


def check_routes() -> None:
    try:
        from services.common.routes import DIRECT, request_routes
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "Маршруты", f"модуль routes недоступен: {exc}")
        return
    for service in ("telegram", "ozon", "wb", "gemini"):
        try:
            routes = request_routes(service)
        except ValueError as exc:
            report(FAIL, f"Маршруты {service}", str(exc))
            continue
        labels = ["direct" if route == DIRECT else route for route in routes]
        report(OK, f"Маршруты {service}", ", ".join(labels))
        if service in {"ozon", "wb"} and DIRECT in routes and len(routes) > 1:
            report(
                WARN,
                f"Маршруты {service}",
                "есть резервный прокси: при сбое direct площадка увидит "
                "зарубежный IP. Для РФ-адреса включите MARKETPLACES_DIRECT_ONLY=true",
                critical=False,
            )


def check_playwright() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        report(
            FAIL,
            "Playwright",
            f"не установлен ({type(exc).__name__}); выполните "
            "venv\\Scripts\\python.exe -m playwright install chromium",
        )
        return
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
    except Exception as exc:  # noqa: BLE001
        report(WARN, "Playwright", f"браузер не проверен: {type(exc).__name__}", critical=False)
        return
    if executable.exists():
        report(OK, "Playwright Chromium", str(executable))
    else:
        report(
            WARN,
            "Playwright Chromium",
            "не скачан: venv\\Scripts\\python.exe -m playwright install chromium",
            critical=False,
        )


def check_gitignore() -> None:
    ignore = BASE_DIR / ".gitignore"
    if not ignore.exists():
        report(WARN, ".gitignore", "отсутствует: .env и база могут попасть в git", critical=False)
        return
    text = ignore.read_text(encoding="utf-8", errors="replace")
    missing = [pattern for pattern in (".env", "*.db", "logs/", "venv/") if pattern not in text]
    if missing:
        report(WARN, ".gitignore", "нет правил: " + ", ".join(missing), critical=False)
    else:
        report(OK, ".gitignore", ".env, база, логи и venv исключены")


async def check_online() -> None:
    """Живые проверки Telegram и Gemini (ключи и сеть)."""
    print("\n--- Онлайн-проверки (--online) ---", flush=True)
    from config import settings

    try:
        from aiogram import Bot
        from aiogram.client.session.aiohttp import AiohttpSession

        from services.common.routes import proxy_for_route, telegram_routes

        errors: List[str] = []
        for route in telegram_routes("telegram"):
            candidate = Bot(
                token=settings.TG_BOT_TOKEN,
                session=AiohttpSession(proxy=proxy_for_route(route), timeout=20),
            )
            try:
                me = await candidate.get_me()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{route or 'direct'}: {type(exc).__name__}")
                await candidate.session.close()
                continue
            report(OK, "Telegram getMe", f"@{me.username}")
            await candidate.session.close()
            break
        else:
            report(FAIL, "Telegram getMe", "; ".join(errors) or "нет маршрутов")
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "Telegram getMe", str(exc))

    try:
        from services.common.gemini import generate

        text, model = await generate("Ответь одним словом: ОК", max_tokens=32, fast=True, timeout=25)
        report(OK, "Gemini", f"{model}: {text[:40]}")
    except Exception as exc:  # noqa: BLE001
        report(FAIL, "Gemini", str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="Диагностика marketplace_agent")
    parser.add_argument("--online", action="store_true", help="проверить Telegram и Gemini")
    args = parser.parse_args()

    print("=== marketplace_agent · диагностика ===", flush=True)
    print(f"Каталог: {BASE_DIR}\n", flush=True)

    check_python()
    check_dependencies()
    env_ok = check_env_file()
    if env_ok:
        check_directories()
        check_database()
        check_routes()
        check_gitignore()
    check_sources()
    check_playwright()

    if args.online and env_ok:
        import asyncio

        asyncio.run(check_online())

    failures = [item for item in _results if item[0] == FAIL]
    warnings = [item for item in _results if item[0] == WARN]
    print("\n=== Итог ===", flush=True)
    print(f"Ошибок: {len(failures)} · предупреждений: {len(warnings)}", flush=True)
    if failures:
        print("\nЧто делать:", flush=True)
        if not env_ok:
            print("  1. Скопируйте .env.example в .env и заполните ключи.", flush=True)
            print("  2. Затем повторите: venv\\Scripts\\python.exe doctor.py", flush=True)
        else:
            print("  1. Исправьте пункты [FAIL] выше.", flush=True)
            print("  2. Переустановите зависимости: setup_env.bat", flush=True)
        return 1
    print("Критических проблем нет. Можно запускать: start_hidden.vbs", flush=True)
    if warnings:
        print("Предупреждения не блокируют запуск, но их стоит устранить.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
