#!/usr/bin/env python3
"""Guards against business logic creeping back into the FastAPI layer.

Routers and dependencies (everything under app/api/) must call service
functions only. Two things are flagged:

- direct session calls (db.query(), db.add(), db.commit(), db.get(), ...):
  they belong in app/services/*.py;
- imports of ORM models: a router that needs the signed-in user annotates it
  with the typed aliases of app/api/deps.py and app/api/auth_guards.py
  (CurrentUser, RequireP4xAdmin, ...), which are the only modules that may
  import the model for that purpose. Enumerations from app.models.enums stay
  allowed, as value types for path and query parameters.

Usage:
    python scripts/check_router_soc.py
"""

import re
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "app" / "api"

VIOLATION_PATTERN = re.compile(
    r"\bdb\.(query|add|add_all|commit|execute|delete|get|scalar|scalars|refresh"
    r"|flush|merge)\("
)
MODEL_IMPORT_PATTERN = re.compile(
    r"^\s*(from app\.models\.(?!enums\b)\w+ import|import app\.models\b)"
)
ALIAS_MODULES = frozenset({"deps.py", "auth_guards.py"})


def _is_violation(path: Path, line: str) -> bool:
    if VIOLATION_PATTERN.search(line):
        return True
    return path.name not in ALIAS_MODULES and bool(MODEL_IMPORT_PATTERN.search(line))


def find_violations() -> dict[Path, list[tuple[int, str]]]:
    violations: dict[Path, list[tuple[int, str]]] = {}
    for path in sorted(API_DIR.rglob("*.py")):
        hits = [
            (lineno, line.strip())
            for lineno, line in enumerate(path.read_text().splitlines(), start=1)
            if _is_violation(path, line)
        ]
        if hits:
            violations[path] = hits
    return violations


def main() -> None:
    violations = find_violations()
    if not violations:
        print("check_router_soc: OK - app/api calls services only.")
        sys.exit(0)

    print(
        "check_router_soc: FAILED - app/api must call service functions only "
        "and import no ORM models.\n"
    )
    for path, hits in violations.items():
        print(f"{path.relative_to(API_DIR.parent.parent)}:")
        for lineno, line in hits:
            print(f"  {lineno}: {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()
