import asyncio
import html
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import BASE_DIR
from maintenance.ai_inspector import inspect_project, safe_target
from maintenance.supervisor import repair_file

_check_lock = asyncio.Lock()


def _rel_label(path: Path | None) -> str:
    if path is None:
        return "API/среда"
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return path.name


async def run_full_self_check_and_heal(**checks) -> dict:
    """Diagnose without importing application modules, polling sellers or writing orders."""
    async with _check_lock:
        try:
            issues = await asyncio.to_thread(inspect_project)
        except Exception as exc:  # noqa: BLE001 — диагностика не должна падать сама
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "total_issues": 1,
                "healed_count": 0,
                "env_warnings": 0,
                "summary": "Диагностика: инспекция проекта не выполнена",
                "details": [f"🔴 Инспекция проекта не выполнена: {html.escape(str(exc)[:700])}"],
                "details_plain": [f"🔴 Инспекция проекта не выполнена: {str(exc)[:700]}"],
            }
        groups = defaultdict(list)
        details: list[str] = []
        plain: list[str] = []
        warnings: list[str] = []
        code_issues = 0
        healed = 0
        for issue in issues:
            label = _rel_label(issue.file)
            context = f"{label}:{issue.line} {issue.kind}: {issue.detail}"
            if issue.kind == "API" and issue.file is None:
                # Сбой внешнего сервиса (лимит 429, 401/403, 5xx) — это состояние среды, а не ошибка кода:
                # раньше он попадал в «ошибок: 1, исправлено: 0» и выглядел как поломка самопроверки.
                advice = ""
                if "HTTP 429" in issue.detail:
                    advice = "; лимит запросов — подождите несколько минут, повторять запросы не нужно"
                elif "HTTP 401" in issue.detail or "HTTP 403" in issue.detail:
                    advice = "; проверьте срок действия и права токена в .env"
                elif "HTTP 404" in issue.detail:
                    advice = "; устаревший метод API — проверьте обновление кода (git pull)"
                elif "Gemini HTTP 400" in issue.detail:
                    advice = "; проверьте модель и параметры запроса Gemini"
                elif "Ozon HTTP 400" in issue.detail:
                    advice = "; проверьте метод и схему запроса Ozon"
                warnings.append(context + advice)
                continue
            code_issues += 1
            if issue.repairable and issue.file and safe_target(issue.file, BASE_DIR):
                groups[issue.file].append(context)
            else:
                details.append("⚠️ " + html.escape(context) + " (требует ручного внимания)")
                plain.append("⚠️ " + context + " (требует ручного внимания)")
        for path, contexts in groups.items():
            try:
                ok, msg = await repair_file(path, "\n".join(contexts), **checks)
            except Exception as exc:  # noqa: BLE001 — один файл не блокирует остальные
                ok, msg = False, f"{path.name}: починка не удалась ({exc})"
            healed += len(contexts) if ok else 0
            details.append(("🟢 " if ok else "🔴 ") + html.escape(msg))
            plain.append(("🟢 " if ok else "🔴 ") + msg)
        for text in warnings:
            details.append("ℹ️ Среда/API: " + html.escape(text))
            plain.append("ℹ️ Среда/API: " + text)
        if code_issues == 0:
            summary = "Диагностика: ошибок кода нет" + (
                f"; предупреждений среды/API: {len(warnings)} (не требуют правки кода)" if warnings else ""
            )
        else:
            summary = (
                f"Диагностика: ошибок кода {code_issues}, исправлено {healed}"
                + (f"; предупреждений среды/API: {len(warnings)}" if warnings else "")
            )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_issues": code_issues,
            "healed_count": healed,
            "env_warnings": len(warnings),
            "summary": summary,
            "details": details,
            "details_plain": plain,
        }
