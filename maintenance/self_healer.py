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
                "details": [f"🔴 Инспекция проекта не выполнена: {html.escape(str(exc)[:700])}"],
            }
        groups = defaultdict(list)
        details = []
        healed = 0
        for issue in issues:
            label = _rel_label(issue.file)
            context = f"{label}:{issue.line} {issue.kind}: {issue.detail}"
            if issue.repairable and issue.file and safe_target(issue.file, BASE_DIR):
                groups[issue.file].append(context)
            else:
                advice = ""
                if issue.kind == "API":
                    if "HTTP 429" in issue.detail:
                        advice = "; проверьте лимиты и повторите позже, не перебирая методы"
                    elif "HTTP 401" in issue.detail or "HTTP 403" in issue.detail:
                        advice = "; проверьте срок действия и права токена в .env"
                    elif "HTTP 404" in issue.detail:
                        advice = "; устаревший метод API — проверьте актуальность ветки через git pull"
                    elif "Gemini HTTP 400" in issue.detail:
                        advice = "; проверьте модель из списка доступных и параметры запроса"
                    elif "Ozon HTTP 400" in issue.detail:
                        advice = "; проверьте название метода и схему запроса в диагностике"
                details.append("⚠️ " + html.escape(context + advice) + " (без изменения кода)")
        for path, contexts in groups.items():
            try:
                ok, msg = await repair_file(path, "\n".join(contexts), **checks)
            except Exception as exc:  # noqa: BLE001 — один файл не блокирует остальные
                ok, msg = False, f"{path.name}: починка не удалась ({exc})"
            healed += len(contexts) if ok else 0
            details.append(("🟢 " if ok else "🔴 ") + html.escape(msg))
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_issues": len(issues),
            "healed_count": healed,
            "details": details,
        }