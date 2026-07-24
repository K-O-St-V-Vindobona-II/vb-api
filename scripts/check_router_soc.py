#!/usr/bin/env python3
"""Guards against business logic (direct DB access) creeping back into
FastAPI routers.

Routers must call service functions only - actual db.query()/db.add()/
db.commit()/db.execute()/db.delete() calls belong in app/services/*.py.
This is a deliberately narrow check (not a general import-linter layers
contract): FastAPI's `Depends(get_current_user) -> Member` needs
app.models imports in practically every router, so a blanket import ban
would false-positive constantly. Scanning for the actual DB-access calls
targets the real Separation-of-Concerns violation directly.

Usage:
    python scripts/check_router_soc.py
"""

import re
import sys
from pathlib import Path

ROUTER_DIR = Path(__file__).resolve().parent.parent / "app" / "api" / "router_includes"

VIOLATION_PATTERN = re.compile(r"\bdb\.(query|add|commit|execute|delete)\(")


def find_violations() -> dict[Path, list[tuple[int, str]]]:
    violations: dict[Path, list[tuple[int, str]]] = {}
    for path in sorted(ROUTER_DIR.glob("*.py")):
        hits = [
            (lineno, line.strip())
            for lineno, line in enumerate(path.read_text().splitlines(), start=1)
            if VIOLATION_PATTERN.search(line)
        ]
        if hits:
            violations[path] = hits
    return violations


def main() -> None:
    violations = find_violations()
    if not violations:
        print("check_router_soc: OK - no direct DB access found in routers.")
        sys.exit(0)

    print("check_router_soc: FAILED - routers must call service functions only.\n")
    for path, hits in violations.items():
        print(f"{path.relative_to(ROUTER_DIR.parent.parent.parent)}:")
        for lineno, line in hits:
            print(f"  {lineno}: {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()
