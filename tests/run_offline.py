"""Run the entire contract suite, refusing deployment state and non-loopback Python sockets."""

import ipaddress
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    violations = []

    def deny(kind):
        violations.append(kind)
        raise RuntimeError(f"Offline test isolation: {kind}")

    def guard(event, args):
        if event in {
            "open",
            "os.scandir",
            "os.listdir",
            "os.mkdir",
            "os.remove",
            "os.rename",
        }:
            for arg in args[:2] if event == "os.rename" else args[:1]:
                if not isinstance(arg, (str, bytes, os.PathLike)):
                    continue
                path = Path(os.path.abspath(os.fsdecode(arg)))
                if path.is_relative_to(ROOT):
                    parts = {p.lower() for p in path.relative_to(ROOT).parts}
                    if (
                        parts & {".env", "logs", "backups"}
                        or path.suffix.lower() == ".db"
                    ):
                        deny("deployment path")
        if event == "socket.connect" and isinstance(args[1], tuple):
            host = args[1][0]
        elif event == "socket.getaddrinfo" and args[0]:
            host = args[0]
        else:
            return
        if host == "localhost":
            return
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            deny("external connection")

    sys.addaudithook(guard)
    from tests import offline

    offline.require_isolation()
    if not offline.STATE.is_dir() or offline.STATE.is_relative_to(ROOT):
        raise RuntimeError("Expected a temporary state directory outside the checkout")
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"Offline isolation violations: {len(violations)}")
    return int(not result.wasSuccessful() or bool(violations))


if __name__ == "__main__":
    sys.exit(main())
