"""Small atomic JSON files; readers never see half of a tracker/cache entry."""

import logging
import json
import os
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from typing import Any


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            tmp.chmod(path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


class DiskCache:
    """One file per query/account; concurrent writes for different keys cannot collide."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, key: str) -> Path:
        return self.directory / (sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str, ttl: float) -> Any:
        path = self.path(key)
        try:
            if 0 <= time.time() - path.stat().st_mtime < ttl:
                return json.loads(path.read_bytes())
        except (OSError, ValueError):
            return None
        return None

    def put(self, key: str, value: Any) -> None:
        try:
            write_json(self.path(key), value)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Кэш недоступен: %s", type(exc).__name__
            )
