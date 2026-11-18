import asyncio
import html
from collections import defaultdict
from datetime import datetime

from config import BASE_DIR
from maintenance.ai_inspector import inspect_project, safe_target
from maintenance.supervisor import repair_file

_check_lock = asyncio.Lock()


async def run_full_self_check_and_heal(**checks) -> dict:
    """Diagnose without importing application modules, polling sellers or writing orders."""
    async with _check_lock:
        issues = await asyncio.to_thread(inspect_project)
        groups = defaultdict(list)
        details = []
        healed = 0
        for issue in issues:
            label = str(issue.file.relative_to(BASE_DIR)) if issue.file else "API/среда"
            context = f"{label}:{issue.line} {issue.kind}: {issue.detail}"
            if issue.repairable and issue.file and safe_target(issue.file, BASE_DIR):
                groups[issue.file].append(context)
            else:
                details.append("⚠️ " + html.escape(context) + " (без изменения кода)")
        for path, contexts in groups.items():
            ok, msg = await repair_file(path, "\n".join(contexts), **checks)
            healed += len(contexts) if ok else 0
            details.append(("🟢 " if ok else "🔴 ") + html.escape(msg))
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_issues": len(issues),
            "healed_count": healed,
            "details": details,
        }
