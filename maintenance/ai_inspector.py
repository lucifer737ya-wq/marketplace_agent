"""Side-effect-free source inspection and a bounded, incremental runtime log reader.

Never exec_module(): importing worker/config/CLI tools would start I/O during a check.
"""

import ast
import importlib.util
import os
import re
import sys
import time
import tokenize
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib.machinery import PathFinder
from pathlib import Path

from pyflakes.checker import Checker
from pyflakes.messages import UndefinedLocal, UndefinedName

from config import BASE_DIR, BACKUPS_DIR, LOGS_DIR
from services.common.storage import DiskCache

EXCLUDED = {
    "logs",
    "backups",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "site-packages",
}
SOURCE_DIRS = {"bot", "services", "database", "maintenance"}
ROOT_MODULES = {
    "worker.py",
    "supervisor.py",
    "products_catalog.py",
    "config.py",
    "calibrate_clicks.py",
}
DAY = 86400
TAIL = 128 * 1024


@dataclass(frozen=True)
class Issue:
    kind: str
    file: Path | None
    line: int
    detail: str
    repairable: bool = True


def source_paths(root: Path):
    for base, dirs, files in os.walk(root, followlinks=False):
        parent = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d.lower() not in EXCLUDED
            and (parent / d) not in (LOGS_DIR, BACKUPS_DIR)
            and not (parent / d).is_symlink()
        )
        for name in sorted(files):
            p = parent / name
            if (
                name.endswith(".py")
                and not name.startswith(".")
                and not p.is_symlink()
                and p.stat().st_nlink == 1
            ):
                yield p


def safe_target(value, root: Path) -> Path | None:
    root = root.resolve()
    path = root / str(value).replace("\\", "/")
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if (
        not rel.parts
        or rel.suffix != ".py"
        or any(p.startswith(".") or p.lower() in EXCLUDED for p in rel.parts)
        or not (rel.parts[0] in SOURCE_DIRS or rel.as_posix() in ROOT_MODULES)
    ):
        return None
    if path.resolve() != path or not path.is_file() or path.stat().st_nlink != 1:
        return None
    return path


def module_name(path, root):
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolvable(name: str, root: Path) -> bool:
    paths = [str(root), *sys.path]
    components = name.split(".")
    for index, component in enumerate(components, 1):
        prefix = ".".join(components[:index])
        if index == 1 and prefix in sys.builtin_module_names:
            return len(components) == 1
        spec = PathFinder.find_spec(prefix, paths)
        if spec is None:
            return False
        paths = spec.submodule_search_locations or []
    return True


@lru_cache(maxsize=128)
def _module_bindings(path: Path, mtime: int) -> frozenset[str] | None:
    """Local exports, without running package initializers or dynamic __getattr__."""
    try:
        with tokenize.open(path) as source:
            tree = ast.parse(source.read())
    except (SyntaxError, OSError, UnicodeError):
        return None  # reported on that module itself
    names = set()

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return None if names.intersection({"__getattr__", "*"}) else frozenset(names)


def local_exports(name: str, root: Path):
    parts = name.split(".")
    if not all(part.isidentifier() and part.lower() not in EXCLUDED for part in parts):
        return None
    base = root.joinpath(*parts)
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.resolve() == path and path.is_file() and path.stat().st_nlink == 1:
            return _module_bindings(path, path.stat().st_mtime_ns)
    return None


def inspect_source(path: Path, source: str, root: Path) -> list[Issue]:
    try:
        tree = ast.parse(source, filename=str(path))
        compile(tree, str(path), "exec")  # also catches e.g. a top-level return
    except (SyntaxError, ValueError) as exc:
        return [
            Issue(
                "SyntaxError",
                path,
                getattr(exc, "lineno", 0) or 0,
                getattr(exc, "msg", "Некорректный исходник"),
            )
        ]
    module = module_name(path, root)
    spec = importlib.util.spec_from_file_location(module, path)
    if not spec or not spec.loader:
        return [Issue("ImportError", path, 0, "Не найден загрузчик модуля")]
    issues = [
        Issue("NameError", path, msg.lineno, msg.message % msg.message_args)
        for msg in Checker(tree, filename=str(path)).messages
        if isinstance(msg, (UndefinedName, UndefinedLocal))
    ]
    # Check attributes of statically identifiable local module aliases. Instance attributes
    # and dynamic packages are diagnosed from tracebacks, not guessed by the scanner.
    aliases = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                owner = alias.name if alias.asname else alias.name.split(".")[0]
                exports = local_exports(owner, root)
                if exports is not None:
                    aliases[alias.asname or owner] = (owner, exports)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            owner, exports = aliases[node.value.id]
            if (
                node.attr not in exports
                and not node.attr.startswith("__")
                and not resolvable(owner + "." + node.attr, root)
            ):
                issues.append(
                    Issue(
                        "AttributeError",
                        path,
                        node.lineno,
                        f"{owner}: нет атрибута {node.attr}",
                    )
                )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            try:
                package = (
                    module if path.name == "__init__.py" else module.rpartition(".")[0]
                )
                base = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package
                )
            except ImportError:
                issues.append(
                    Issue(
                        "ImportError",
                        path,
                        node.lineno,
                        "Недопустимый относительный импорт",
                    )
                )
                continue
            exports = local_exports(base, root)
            if exports is not None:
                for alias in node.names:
                    if (
                        alias.name not in exports
                        and alias.name != "*"
                        and not resolvable(base + "." + alias.name, root)
                    ):
                        issues.append(
                            Issue(
                                "ImportError",
                                path,
                                node.lineno,
                                f"{base}: нет {alias.name}",
                            )
                        )
            modules = (
                [base] if node.module else [f"{base}.{n.name}" for n in node.names]
            )
        else:
            continue
        for name in modules:
            if not resolvable(name, root):
                issues.append(
                    Issue(
                        "ImportError",
                        path,
                        node.lineno,
                        f"Не найден модуль {name}",
                        name.split(".")[0] in SOURCE_DIRS,
                    )
                )
    return issues


_TIMESTAMP = re.compile(r"^\[?(20\d\d-\d\d-\d\d[ T]\d\d:\d\d:\d\d)")
_FRAME = re.compile(r'^\s*File "([^"]+\.py)", line (\d+)')
_EXCEPTION = re.compile(
    r"^(NameError|ModuleNotFoundError|ImportError|AttributeError|SyntaxError):\s*(.*)"
)
_HTTP = re.compile(r"\b(?:HTTP(?:Error)?\s*[:=]?\s*|Rate Limit\s+)([45]\d\d)\b", re.I)


def recent_log_issues(root: Path, directory: Path, now: float) -> list[Issue]:
    # Checkpoints prevent an old traceback at the head of a still-active log triggering repairs every night.
    cache = DiskCache(directory / "inspector_state")
    issues = []
    for path in sorted(directory.glob("*.log")):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        if stat.st_nlink != 1 or now - stat.st_mtime > DAY:
            continue
        prev = cache.get(path.name, DAY) or {}
        offset = (
            prev.get("end", 0)
            if prev.get("inode") == stat.st_ino and prev.get("end", 0) <= stat.st_size
            else 0
        )
        if offset == stat.st_size and prev.get("mtime") == stat.st_mtime_ns:
            continue
        start = max(offset if offset < stat.st_size else 0, stat.st_size - TAIL)
        with path.open("rb") as stream:
            stream.seek(start)
            raw = stream.read(TAIL)
        counts = Counter(
            prev.get("counts", {})
            if offset and now - prev.get("since", 0) < DAY
            else {}
        )
        since = prev.get("since", now) if counts else now
        frame, line = None, 0
        event_time = stat.st_mtime
        for text in raw.decode("utf-8", "replace").splitlines():
            stamp = _TIMESTAMP.match(text)
            if stamp:
                try:
                    event_time = datetime.fromisoformat(stamp[1]).timestamp()
                except ValueError:
                    continue
                text = text[stamp.end() :].lstrip(",.0123456789 ]")
            if now - event_time > DAY:
                continue
            if text.startswith("Traceback "):
                frame, line = None, 0
            match = _FRAME.match(text)
            if match:
                candidate = safe_target(match[1], root)
                if candidate:
                    frame, line = candidate, int(match[2])
                continue
            match = _EXCEPTION.match(text)
            if match:
                # Extract symbol names only. Tracebacks may contain headers, user messages and credentials.
                names = re.findall(r"['\"]([\w.]{1,80})['\"]", match[2])[:3]
                templates = {
                    "AttributeError": "'{}' has no attribute '{}'",
                    "NameError": "name '{}' is not defined",
                    "ImportError": "cannot import '{}'",
                    "ModuleNotFoundError": "no module named '{}'",
                }
                template = templates.get(match[1], "")
                detail = match[1] + (
                    ": " + template.format(*names)
                    if 0 < template.count("{}") <= len(names)
                    else ""
                )
                issues.append(
                    Issue(
                        match[1],
                        frame,
                        line,
                        detail,
                        frame is not None
                        and int(frame.stat().st_mtime) <= int(event_time)
                        and match[1] != "ModuleNotFoundError",
                    )
                )
                frame, line = None, 0
            elif m := _HTTP.search(text):
                service = next(
                    (
                        s
                        for s in ("Ozon", "WB", "Gemini", "Telegram")
                        if s.lower() in text.lower()
                    ),
                    "API",
                )
                signature = f"{service} HTTP {m[1]}"
                counts[signature] += 1
                if counts[signature] == 2:
                    issues.append(
                        Issue("API", None, 0, "Повторяющийся сбой: " + signature, False)
                    )
        cache.put(
            path.name,
            {
                "inode": stat.st_ino,
                "end": start + len(raw),
                "mtime": stat.st_mtime_ns,
                "counts": dict(counts),
                "since": since,
            },
        )
    return issues


def inspect_project(root: Path = BASE_DIR, log_dir: Path = LOGS_DIR) -> list[Issue]:
    issues = []
    for path in source_paths(root):
        try:
            with tokenize.open(path) as source:
                issues.extend(inspect_source(path, source.read(), root))
        except (OSError, UnicodeError, SyntaxError) as exc:
            issues.append(Issue("SourceError", path, 0, type(exc).__name__, False))
    issues.extend(recent_log_issues(root, log_dir, time.time()))
    return list(dict.fromkeys(issues))
